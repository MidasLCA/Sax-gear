#!/usr/bin/env python3
# scraper_auto.py
import os
import json
import logging
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from curl_cffi import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# ======= 加载环境变量 (.env) =======
load_dotenv()

# ======= 邮件配置 =======
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.zoho.com")

# SMTP_PORT 处理（修复报错点）
try:
    smtp_port_raw = os.getenv("SMTP_PORT", "587").strip()
    SMTP_PORT = int(smtp_port_raw) if smtp_port_raw else 587
except ValueError:
    print(f"⚠️ Warning: invalid SMTP_PORT value '{smtp_port_raw}', defaulting to 587.")
    SMTP_PORT = 587

RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", EMAIL_USER)
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", 60))

# ======= 数据与日志 =======
DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "last_items.json")
LOG_FILE = "scraper.log"
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
)

# ======= Playwright / APScheduler 可选依赖 =======
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    APS_AVAILABLE = True
except Exception:
    APS_AVAILABLE = False

# ======= 目标配置 =======
TARGET_BRANDS = ["Selmer", "Otto Link", "Dave Guardala", "Yanagisawa", "Beechler", "Yani", "Otto"]

SITES = [
    {"url": "https://www.getasax.com/collections/mouthpieces", "item": ".product-grid-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.saxquest.com/", "item": ".product-listing", "name": ".product-title", "price": ".product-price", "link": "a"},
    {"url": "https://www.dcsax.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.soundfuga.jp/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.reverb.com/marketplace?query=saxophone", "item": ".product-card", "name": ".product-card-title", "price": ".product-card-price", "link": "a"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# ======= 抓取函数 =======
def fetch_static_html(url, timeout=15):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
        logging.warning(f"Static fetch {url} returned {resp.status_code}")
    except Exception as e:
        logging.warning(f"Static fetch failed for {url}: {e}")
    return None

def fetch_dynamic_html(url, wait_seconds_range=(2, 5)):
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            page = browser.new_page()
            page.goto(url)
            time.sleep(random.uniform(*wait_seconds_range))
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logging.error(f"Dynamic fetch failed for {url}: {e}")
        return None

def parse_items_from_html(html, site):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for product in soup.select(site.get("item", "")):
        try:
            name_el = product.select_one(site.get("name", ""))
            link_el = product.select_one(site.get("link", ""))
            price_el = product.select_one(site.get("price", ""))
            if not name_el or not link_el:
                continue
            name = name_el.get_text(strip=True)
            price = price_el.get_text(strip=True) if price_el else "Price not listed"
            href = link_el.get("href") or link_el.get("data-href") or ""
            link = urljoin(site["url"], href)
            items.append({"name": name, "price": price, "link": link, "source": site["url"]})
        except Exception as e:
            logging.debug(f"Error parsing product on {site['url']}: {e}")
    return items

def fetch_site(site):
    url = site["url"]
    logging.info(f"Fetching {url}")
    html = fetch_static_html(url)
    if not html or len(html) < 2000:
        html = fetch_dynamic_html(url)
    if not html:
        return []
    items = parse_items_from_html(html, site)
    logging.info(f"Found {len(items)} items on {url}")
    return items

# ======= 数据处理 =======
def filter_by_brand(items):
    brands = [b.lower() for b in TARGET_BRANDS]
    return [i for i in items if any(b in i["name"].lower() for b in brands)]

def load_previous():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_current(items):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed to save current data: {e}")

def find_new_items(current, previous):
    prev_links = {i.get("link") for i in previous}
    return [i for i in current if i.get("link") not in prev_links]

# ======= 邮件通知 =======
def send_email(new_items):
    if not EMAIL_USER or not EMAIL_PASS:
        logging.error("EMAIL_USER or EMAIL_PASS missing.")
        return
    msg = MIMEMultipart("alternative")
    msg["From"] = f"SaxBot <{EMAIL_USER}>"
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = "🎷 New Saxophone Listings"

    if new_items:
        rows = "".join(
            f"<tr><td><a href='{i['link']}'>{i['name']}</a></td><td>{i['price']}</td><td>{i['source']}</td></tr>"
            for i in new_items
        )
        html = f"<h3>🎷 New Listings</h3><table border='1'>{rows}</table>"
    else:
        html = "<p>No new listings this time.</p>"

    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
            s.starttls()
            s.login(EMAIL_USER, EMAIL_PASS)
            s.sendmail(EMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        logging.info(f"Email sent to {RECIPIENT_EMAIL} ({len(new_items)} new items).")
    except Exception as e:
        logging.error(f"Email send failed: {e}")

# ======= 主流程 =======
def run_once():
    logging.info("=== Run started ===")
    all_items = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(fetch_site, s) for s in SITES]
        for f in as_completed(futures):
            all_items.extend(f.result() or [])
    filtered = filter_by_brand(all_items)
    previous = load_previous()
    new_items = find_new_items(filtered, previous)
    if new_items:
        save_current(filtered)
        send_email(new_items)
    logging.info("=== Run finished ===\n")

# ======= 调度模式 =======
def start_scheduler():
    if not APS_AVAILABLE:
        logging.error("APScheduler not installed.")
        return
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_once, "interval", minutes=INTERVAL_MINUTES)
    scheduler.start()
    logging.info(f"Scheduler started every {INTERVAL_MINUTES} minutes.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        scheduler.shutdown()

# ======= CLI =======
if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "schedule":
        start_scheduler()
    else:
        run_once()




