"""
Отдельный процесс для публикации в канал и отправки подписчиков.
"""
import asyncio
import logging
import random
import os
import sys
import signal
from datetime import datetime
from io import BytesIO

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
from telegram import Bot
from telegram.constants import ParseMode
import requests as req_lib

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "ekbsummary")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

import sqlite3
from pathlib import Path
from database import is_url_published, mark_url_published

DB_PATH = Path(__file__).parent / "data" / "news.db"


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

# Используем контекстный менеджер для автоматического закрытия
from contextlib import contextmanager

@contextmanager
def get_db_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def get_unsent_news(limit=1):
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM news WHERE is_sent = 0 ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

def mark_as_sent(ids):
    if not ids:
        return
    with get_db_conn() as conn:
        placeholders = ','.join('?' * len(ids))
        conn.execute(f"UPDATE news SET is_sent = 1, sent_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", ids)
        conn.commit()

def get_active_subscribers():
    with get_db_conn() as conn:
        rows = conn.execute("SELECT chat_id FROM subscribers WHERE is_active = 1").fetchall()
        return [r['chat_id'] for r in rows]

def cleanup_old_news(days=7):
    with get_db_conn() as conn:
        conn.execute("DELETE FROM news WHERE COALESCE(published_at, created_at) < datetime('now', ?)", (f'-{days} days',))
        conn.commit()

def cleanup_old_digest_urls(days=30):
    with get_db_conn() as conn:
        conn.execute("DELETE FROM digest_urls WHERE created_at < datetime('now', ?)", (f'-{days} days',))
        conn.commit()


def download_image(url):
    if not url:
        return None
    try:
        resp = req_lib.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if resp.status_code == 200 and 1000 < len(resp.content) < 5000000:
            return resp.content
    except Exception:
        pass
    return None


def _format_news_text(news, desc):
    emoji = {
        "URA.RU": "🔴", "E1.RU": "🟡", "66.RU": "🔵", "OBLTV.RU": "🟢", "JustMedia": "🟣",
        "Lenta.ru": "🟠", "Life.ru": "🟤", "Коммерсантъ": "⚪️", "Ведомости": "🟧", "UralWeb": "⚫️",
        "itsmycity": "🟡", "Областная газета": "🟠",
    }.get(news["source"], "📡")
    return f"{emoji} <b>{news['title']}</b>\n\n{desc[:800]}\n\n📌 <a href=\"{news['link']}\">Читать</a>"


async def send_to_channel(bot, text, image_url=None):
    try:
        if image_url:
            img = await asyncio.to_thread(download_image, image_url)
            if img:
                await bot.send_photo(chat_id=f"@{CHANNEL_USERNAME}", photo=BytesIO(img),
                                     caption=text[:1024], parse_mode=ParseMode.HTML)
                return True
        await bot.send_message(chat_id=f"@{CHANNEL_USERNAME}", text=text,
                               parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return True
    except Exception as e:
        logger.error(f"Channel send error: {e}")
        return False


async def send_to_subscribers(bot, text, image_url=None):
    subscribers = await asyncio.to_thread(get_active_subscribers)
    if not subscribers:
        return 0
    sent = 0
    for chat_id in subscribers:
        try:
            if image_url:
                img = await asyncio.to_thread(download_image, image_url)
                if img:
                    await bot.send_photo(chat_id=chat_id, photo=BytesIO(img),
                                         caption=text[:1024], parse_mode=ParseMode.HTML)
                    sent += 1
                    await asyncio.sleep(0.1)
                    continue
            await bot.send_message(chat_id=chat_id, text=text,
                                   parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            sent += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Send error {chat_id}: {e}")
    return sent


async def publish_news(bot):
    news_list = await asyncio.to_thread(get_unsent_news, 1)
    if not news_list:
        return False
    news = news_list[0]
    desc = news.get("description", "") or ""
    text = _format_news_text(news, desc)
    ok = await send_to_channel(bot, text, news.get("image_url", ""))
    if ok:
        await asyncio.to_thread(mark_as_sent, [news["id"]])
        await asyncio.to_thread(mark_url_published, news["link"])
        logger.info(f"Published [{news['source']}] {news['title'][:50]}...")
    return ok
    if sent_ids:
        await asyncio.to_thread(mark_as_sent, sent_ids)
    return published_count


async def publish_news_to_subscribers(bot):
    news_list = await asyncio.to_thread(get_unsent_news, 5)
    if not news_list:
        return 0
    sent_count = 0
    sent_ids = []
    for news in news_list:
        desc = news.get("description", "") or ""
        text = _format_news_text(news, desc)
        sent = await send_to_subscribers(bot, text, news.get("image_url", ""))
        sent_count += max(sent, 0)
        sent_ids.append(news["id"])
    if sent_ids:
        await asyncio.to_thread(mark_as_sent, sent_ids)
        logger.info(f"Sent {sent_count} subscribers ({len(sent_ids)} news)")
    return sent_count


async def publish_weather(bot):
    import urllib.request, json as _json
    try:
        url = "https://wttr.in/Yekaterinburg?format=j1"
        data = await asyncio.to_thread(lambda: _json.loads(urllib.request.urlopen(url, timeout=10).read().decode('utf-8')))
        cur = data["current_condition"][0]
        tomorrow = data["weather"][1]
        lines = ["🌤️ <b>Погода в Екатеринбурге</b>\n"]
        lines.append(f"<b>Сейчас:</b> {cur['temp_C']}°C (ощущается {cur['FeelsLikeC']}°C)")
        lines.append(f"  {cur['weatherDesc'][0]['value']}")
        lines.append(f"  💧 Влажность: {cur['humidity']}%")
        lines.append(f"  💨 Ветер: {cur['windspeedKmph']} км/ч")
        lines.append(f"\n<b>Завтра ({tomorrow['date']}):</b>")
        lines.append(f"  {tomorrow['mintempC']}°C...{tomorrow['maxtempC']}°C")
        text = "\n".join(lines)
        await bot.send_message(chat_id=f"@{CHANNEL_USERNAME}", text=text, parse_mode=ParseMode.HTML)
        logger.info("Weather published")
        return True
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return False


async def publish_digest(bot, digest_type):
    import feedparser, socket as _socket
    from bs4 import BeautifulSoup

    names = {"ainews": "AI-новости", "gadgets": "Гаджеты", "cars": "Авто"}
    emojis = {"ainews": "🤖", "gadgets": "📱", "cars": "🚗"}
    name = names.get(digest_type, digest_type)
    emoji = emojis.get(digest_type, "📋")

    try:
        await bot.send_message(chat_id=f"@{CHANNEL_USERNAME}", text=f"{emoji} Собираю {name.lower()}...")
        news_list = []
        parsers = {
            "ainews": [
                ("https://www.theverge.com/rss/index.xml", "The Verge", "🌐"),
                ("https://feeds.arstechnica.com/arstechnica/technology-lab", "Ars Technica", "⚙️"),
                ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch", "🚀"),
                ("https://www.wired.com/feed/tag/ai/latest/rss", "Wired", "🔌"),
                ("https://www.technologyreview.com/topic/artificial-intelligence/feed/", "MIT Tech Review", "🔬"),
            ],
            "gadgets": [
                ("https://www.theverge.com/rss/index.xml", "The Verge", "🌐"),
                ("https://techcrunch.com/category/gadgets/feed/", "TechCrunch", "🚀"),
                ("https://9to5google.com/feed/", "9to5Google", "📱"),
                ("https://www.androidcentral.com/rss.xml", "Android Central", "🤖"),
                ("https://feeds.arstechnica.com/arstechnica/technology-lab", "Ars Technica", "⚙️"),
            ],
            "cars": [
                ("https://auto.mail.ru/rss/", "Auto.Mail.ru", "📧"),
                ("https://kolesa.ru/rss", "Kolesa.ru", "🔧"),
                ("http://www.5koleso.ru/rss/", "5koleso.ru", "🚗"),
                ("https://auto.onliner.by/feed", "Auto.Onliner.by", "🚘"),
                ("https://www.ixbt.com/export/rss.xml", "iXBT", "🛡️"),
            ],
        }
        for url, source_name, icon in parsers.get(digest_type, []):
            try:
                _socket.setdefaulttimeout(10)
                feed = feedparser.parse(url)
                _socket.setdefaulttimeout(None)
                for entry in feed.entries[:3]:
                    title = entry.title
                    link = entry.link
                    desc = entry.get("summary", "")[:200]
                    if desc:
                        desc = BeautifulSoup(desc, 'html.parser').get_text(strip=True)[:200]
                    news_list.append({"title": title, "link": link, "desc": desc, "icon": icon, "source": source_name})
            except Exception:
                continue

        if not news_list:
            return False

        parts = [f"<b>{emoji} {name}</b>\n"]
        for i, s in enumerate(news_list, 1):
            parts.append(f"<b>{i}. {s['icon']} {s['source']}</b>\n<a href=\"{s['link']}\">{s['title'][:120]}</a>\n<i>{s['desc'][:300]}</i>\n")
        full_text = "\n".join(parts)
        if len(full_text) > 4000:
            full_text = full_text[:3997] + "..."
        await bot.send_message(chat_id=f"@{CHANNEL_USERNAME}", text=full_text,
                               parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        logger.info(f"Digest published: {digest_type} ({len(news_list)} news)")
        return True
    except Exception as e:
        logger.error(f"Digest error: {e}", exc_info=True)
        return False


async def main_loop():
    global _last_cleanup_date, _last_digest_date, _last_digest_type

    _last_cleanup_date = None
    _last_digest_date = None
    _last_digest_type = None

    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("Channel publisher started")

    while True:
        try:
            now = datetime.now()
            today = now.date()

            if _last_cleanup_date != today and now.hour < 2:
                await asyncio.to_thread(cleanup_old_news, 7)
                await asyncio.to_thread(cleanup_old_digest_urls, 30)
                _last_cleanup_date = today

            if now.hour == 10 and now.minute < 5 and _last_cleanup_date != today:
                await publish_weather(bot)

            if now.hour >= 11 and now.hour < 21 and now.minute < 3 and _last_digest_date != today:
                dtype = random.choice(["ainews", "gadgets", "cars"])
                await publish_digest(bot, dtype)
                _last_digest_date = today
                _last_digest_type = dtype

            await publish_news(bot)
            await publish_news_to_subscribers(bot)

        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)

        wait = random.randint(4, 12) * 60
        logger.info(f"Next in {wait//60} min, sleeping...")
        await asyncio.sleep(wait)
        logger.info("Woke up! Starting next iteration...")


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    asyncio.run(main_loop())


if __name__ == "__main__":
    main()
