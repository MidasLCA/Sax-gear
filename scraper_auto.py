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

# Playwright (动态渲染)
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# Scheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    APS_AVAILABLE = True
except Exception:
    APS_AVAILABLE = False
smtp_port_str = os.getenv("SMTP_PORT")
print("DEBUG SMTP_PORT raw:", repr(smtp_port_str))
try:
    SMTP_PORT = int(smtp_port_str) if smtp_port_str else 587
except ValueError:
    SMTP_PORT = 587

# ======= 配置与环境 =======
load_dotenv()

# 必要 env（请在 .env 中设置，示例见下方）
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT") or 587)

RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", EMAIL_USER)

# 调度： 使用 INTERVAL_MINUTES 来配置每隔多少分钟运行一次（默认 60 分钟）
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", 60))

# 其他配置
DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "last_items.json")
LOG_FILE = "scraper.log"
os.makedirs(DATA_DIR, exist_ok=True)

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
)

# 用户可以根据需要定制品牌、站点和 CSS 选择器
TARGET_BRANDS = [
    "Selmer", "Otto Link", "Dave Guardala", "Yanagisawa", "Beechler", "Yani", "Otto"
]

SITES = [
    # 示例：你可以根据需要增加/修改
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

# ======= 抓取静态 / 动态 HTML =======
def fetch_static_html(url, timeout=15):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        status = getattr(resp, "status_code", None)
        if status == 200:
            return resp.text
        logging.warning(f"Static fetch {url} returned status {status}")
    except Exception as e:
        logging.warning(f"Static fetch failed for {url}: {e}")
    return None

def fetch_dynamic_html(url, wait_seconds_range=(2, 5)):
    if not PLAYWRIGHT_AVAILABLE:
        logging.error("Playwright not available. Install playwright if you need dynamic rendering.")
        return None
    try:
        with sync_playwright() as p:
            # 使用 firefox 或 chromium，headless True
            browser = p.firefox.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(60000)
            page.goto(url)
            # 随机短等待，给 JS 加载时间
            time.sleep(random.uniform(*wait_seconds_range))
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        logging.error(f"Dynamic fetch failed for {url}: {e}")
        return None

# ======= 解析商品 =======
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
            logging.debug(f"Error parsing a product element on {site['url']}: {e}")
    return items

# ======= 单站点抓取（静态优先 -> 动态回退） =======
def fetch_site(site):
    url = site["url"]
    logging.info(f"Fetching {url}")
    # 先尝试静态请求
    html = fetch_static_html(url)
    # 简单判定：如果响应太短或 None，尝试动态渲染
    if not html or len(html) < 2000:
        logging.info(f"Static content insufficient for {url}; trying dynamic fetch")
        html = fetch_dynamic_html(url)

    if not html:
        logging.error(f"Failed to retrieve HTML for {url}")
        return []

    items = parse_items_from_html(html, site)
    logging.info(f"Found {len(items)} items on {url}")
    return items

# ======= 过滤、历史对比、数据保存 =======
def filter_by_brand(items):
    if not items:
        return []
    brands_l = [b.lower() for b in TARGET_BRANDS]
    filtered = [it for it in items if any(b in it["name"].lower() for b in brands_l)]
    return filtered

def load_previous():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load previous data: {e}")
        return []

def save_current(items):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed to save current data: {e}")

def find_new_items(current, previous):
    prev_links = {i.get("link") for i in previous if i.get("link")}
    new = [i for i in current if i.get("link") and i.get("link") not in prev_links]
    return new

# ======= 邮件发送（HTML） =======
def send_email(new_items, recipient=RECIPIENT_EMAIL):
    if not EMAIL_USER or not EMAIL_PASS:
        logging.error("EMAIL_USER or EMAIL_PASS not set in environment.")
        return False

    # 组织邮件
    msg = MIMEMultipart("alternative")
    # “去人化”发件显示成系统名
    msg["From"] = f"SaxBot <{EMAIL_USER}>"
    msg["To"] = recipient
    msg["Subject"] = "🎷 New Saxophone Listings"

    if not new_items:
        html = "<p>No new saxophone listings found at this run.</p>"
    else:
        rows = ""
        for item in new_items:
            name = item.get("name")
            price = item.get("price")
            link = item.get("link")
            source = item.get("source", "")
            rows += f"<tr><td><a href='{link}' target='_blank'>{name}</a></td><td>{price}</td><td>{source}</td></tr>"
        html = f"""
            <h3>🎷 New Saxophone Listings</h3>
            <table border="1" cellpadding="6" cellspacing="0">
                <tr><th>Product</th><th>Price</th><th>Source</th></tr>
                {rows}
            </table>
            <p>Sent by SaxBot</p>
        """

    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, recipient, msg.as_string())
        logging.info(f"Email sent to {recipient} with {len(new_items)} new items.")
        return True
    except Exception as e:
        logging.error(f"Error sending email: {e}")
        return False

# ======= 主运行流程 =======
def run_once():
    logging.info("=== Run started ===")
    all_items = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(fetch_site, site) for site in SITES]
        for fut in as_completed(futures):
            try:
                res = fut.result()
                if res:
                    all_items.extend(res)
            except Exception as e:
                logging.error(f"Error in site fetch future: {e}")

    # 过滤品牌
    filtered = filter_by_brand(all_items)
    logging.info(f"Total filtered items: {len(filtered)}")

    # 加载历史并找出新增
    previous = load_previous()
    new_items = find_new_items(filtered, previous)
    logging.info(f"New items detected: {len(new_items)}")

    if new_items:
        # 保存当前（以便下次对比）
        save_current(filtered)
        # 发送邮件
        send_email(new_items)
    else:
        logging.info("No new items to send. Skipping email.")

    logging.info("=== Run finished ===\n")

# ======= 调度入口 =======
def start_scheduler(interval_minutes=INTERVAL_MINUTES):
    if not APS_AVAILABLE:
        logging.error("APScheduler not installed. To enable scheduling, install apscheduler.")
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(run_once, 'interval', minutes=interval_minutes, next_run_time=None)
    scheduler.start()
    logging.info(f"Scheduler started: run every {interval_minutes} minutes.")
    try:
        # Keep main thread alive
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler stopped by user.")
        scheduler.shutdown()

# ======= CLI =======
if __name__ == "__main__":
    # 如果你只希望运行一次，使用 `python scraper_auto.py`
    # 如果你希望启用内置调度（后台定时），确保 APScheduler 可用并运行 `python scraper_auto.py schedule`
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "schedule":
        start_scheduler()
    else:
        run_once()



