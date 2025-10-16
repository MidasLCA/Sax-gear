import os
import json
import logging
import time
import random
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from playwright.sync_api import sync_playwright

# ============ 基础配置 ============
load_dotenv()
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("scraper.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

TARGET_BRANDS = ["Selmer", "Otto", "Guardala", "Yanagisawa", "Beechler", "Yani"]

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

DATA_FILE = "data/last_items.json"

# ============ 抓取函数 ============
def fetch_static_html(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        logging.warning(f"Static fetch failed for {url}: {e}")
    return None

def fetch_dynamic_html(url):
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=60000)
            time.sleep(random.uniform(3, 5))
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logging.error(f"Dynamic fetch failed for {url}: {e}")
        return None

def parse_items(soup, site):
    items = []
    for product in soup.select(site["item"]):
        name_el = product.select_one(site["name"])
        price_el = product.select_one(site["price"])
        link_el = product.select_one(site["link"])

        if name_el and link_el:
            name = name_el.get_text(strip=True)
            price = price_el.get_text(strip=True) if price_el else "Price not listed"
            link = urljoin(site["url"], link_el.get("href"))
            items.append({"name": name, "price": price, "link": link})
    return items

def fetch_site_data(site):
    logging.info(f"Fetching {site['url']}")
    html = fetch_static_html(site["url"])

    if not html or len(html) < 5000:
        logging.info(f"Switching to dynamic mode for {site['url']}")
        html = fetch_dynamic_html(site["url"])

    if not html:
        logging.error(f"Failed to fetch {site['url']}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = parse_items(soup, site)
    logging.info(f"{site['url']} -> found {len(items)} items")
    return items

# ============ 邮件通知 ============
def filter_items(items):
    return [item for item in items if any(b.lower() in item["name"].lower() for b in TARGET_BRANDS)]

def send_email(items, recipient_email):
    sender = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")

    if not sender or not password:
        logging.error("Missing EMAIL_USER or EMAIL_PASS in .env")
        return

    smtp_server = os.getenv("SMTP_SERVER", "smtp.zoho.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient_email
    msg["Subject"] = "🎷 New Saxophone Listings Found!"

    if not items:
        html_body = "<p>No new saxophone listings found today.</p>"
    else:
        rows = "".join(
            f"<tr><td><a href='{i['link']}'>{i['name']}</a></td><td>{i['price']}</td></tr>"
            for i in items
        )
        html_body = f"""
        <h3>🎷 New Saxophone Listings</h3>
        <table border="1" cellspacing="0" cellpadding="6">
            <tr><th>Product</th><th>Price</th></tr>
            {rows}
        </table>
        """

    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient_email, msg.as_string())
        logging.info(f"Email sent successfully to {recipient_email}")
    except Exception as e:
        logging.error(f"Error sending email: {e}")

# ============ 数据记录 ============
def load_previous_items():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_current_items(items):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def find_new_items(current, previous):
    prev_links = {i["link"] for i in previous}
    return [i for i in current if i["link"] not in prev_links]

# ============ 主函数 ============
def main():
    logging.info("=== Scraper started ===")
    all_items = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(fetch_site_data, site) for site in SITES]
        for f in as_completed(futures):
            all_items.extend(f.result())

    filtered = filter_items(all_items)
    logging.info(f"Fetched {len(filtered)} total filtered items.")

    previous = load_previous_items()
    new_items = find_new_items(filtered, previous)
    logging.info(f"Detected {len(new_items)} new items since last run.")

    if new_items:
        save_current_items(filtered)
        send_email(new_items, "kennyllm@hotmail.com")
    else:
        logging.info("No new items. Email not sent.")

    logging.info("=== Scraper finished ===")


if __name__ == "__main__":
    main()
