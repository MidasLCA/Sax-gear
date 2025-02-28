import os
import logging
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urljoin
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests
from apscheduler.schedulers.blocking import BlockingScheduler

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 定义目标品牌
TARGET_BRANDS = ["Selmer", "Otto Link", "Dave Guardala", "Yanagisawa", "Beechler"]

# 定义目标网站及其选择器
SITES = [
    {"url": "http://www.pmwoodwind.com/", "item": ".product-list-item", "name": "h3", "price": ".price", "link": "a"},
    {"url": "https://www.saxquest.com/", "item": ".product-listing", "name": ".product-title", "price": ".product-price", "link": "a"},
    {"url": "https://www.getasax.com/", "item": ".product-grid-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.dcsax.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.saxstable.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://saxalley.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.dillonmusic.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.barnardrepair.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.bostonsaxshop.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "http://www.jwsax.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.junkdude.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://stevedeutschmusic.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://saxpoint.nl/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.tenormadness.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.soundfuga.jp/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.ishibashi-music.jp/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"},
    {"url": "https://www.musicgoround.com/", "item": ".product-item", "name": ".product-title", "price": ".price", "link": "a"}
]

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

def filter_items(items):
    """过滤出包含目标品牌的商品"""
    return [item for item in items if any(brand.lower() in item['name'].lower() for brand in TARGET_BRANDS)]

def fetch_data_with_curl(site):
    """使用 curl_cffi 获取数据"""
    try:
        response = requests.get(site["url"], headers=HEADERS, timeout=10, impersonate="chrome")
        if response.status_code == 404:
            logging.warning(f"Page not found: {site['url']}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        return parse_items(soup, site)
    except Exception as e:
        logging.error(f"Error fetching {site['url']}: {e}")
        return []

def parse_items(soup, site):
    """解析页面中的商品信息"""
    items = []
    for product in soup.select(site["item"]):
        name_element = product.select_one(site["name"])
        price_element = product.select_one(site["price"])
        link_element = product.select_one(site["link"])

        if name_element and link_element:
            name = name_element.text.strip()
            price = price_element.text.strip() if price_element else "Price not listed"
            link = urljoin(site["url"], link_element["href"])
            items.append({"name": name, "price": price, "link": link})
    return items

def send_email(items, recipient_email):
    """发送邮件通知"""
    sender_email = os.getenv("EMAIL_USER")
    sender_password = os.getenv("EMAIL_PASS")

    if not sender_email or not sender_password:
        logging.error("Email credentials are missing. Set EMAIL_USER and EMAIL_PASS environment variables.")
        return

    smtp_server = "smtp.office365.com"
    smtp_port = 587

    subject = "New Saxophone Listings Available!"
    body = "Here are the latest saxophone listings for your preferred brands:\n\n"
    for item in items:
        body += f"{item['name']} - {item['price']} - {item['link']}\n"

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        logging.info(f"Email sent successfully to {recipient_email}")
    except smtplib.SMTPException as e:
        logging.error(f"Error sending email: {e}")

def main():
    """主函数"""
    all_items = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_data_with_curl, site) for site in SITES]
        for future in as_completed(futures):
            all_items.extend(future.result())

    if all_items:
        send_email(all_items, "kennyllm@hotmail.com")

if __name__ == "__main__":
    # 创建调度器
    scheduler = BlockingScheduler()

    # 添加每天运行一次的任务
    scheduler.add_job(main, 'cron', hour=2, minute=0)  # 每天凌晨 2 点运行

    try:
        logging.info("Starting scheduler...")
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Stopping scheduler...")
        scheduler.shutdown()