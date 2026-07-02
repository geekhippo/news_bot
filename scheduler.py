"""
Планировщик публикаций новостного бота.
Сочетает:
1. Постинг одной новости в канал каждые 7 минут.
2. Ежечасную рассылку 5 свежих новостей подписчикам (отдельно).
3. Фоновые задачи (погода, дайджесты, ежедневная очистка БД в полночь).
"""
import asyncio
import logging
import random
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Импорты
from database import get_latest_news, is_url_published, mark_url_published, get_unsent_news
from channel_publisher import (
    cleanup_old_news, cleanup_old_digest_urls,
    get_active_subscribers, mark_as_sent, publish_weather, publish_digest,
    _format_news_text, send_to_channel, send_to_subscribers,
    TELEGRAM_TOKEN
)
from telegram import Bot

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)

# Состояние
_last_cleanup_date = None
_last_digest_date = None
_last_digest_type = None


def individual_channel_post():
    """Публикация ОДНОЙ новости в канал (каждые 7 минут)."""
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        news_list = get_unsent_news(1)
        if news_list:
            news = news_list[0]
            if not is_url_published(news["link"]):
                desc = news.get("description", "") or ""
                text = _format_news_text(news, desc)
                ok = _loop.run_until_complete(send_to_channel(bot, text, news.get("image_url", "")))
                if ok:
                    mark_as_sent([news["id"]])
                    mark_url_published(news["link"])
                    logger.info(f"[Channel] Published [{news['source']}]")
    except Exception as e:
        logger.error(f"[Channel] Error: {e}", exc_info=True)


def hourly_subscriber_posts():
    """Рассылка 5 ОТДЕЛЬНЫХ свежих новостей подписчикам (раз в час)."""
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        news_list = get_latest_news(5)
        if not news_list:
            return

        for news in news_list:
            desc = news.get("description", "") or ""
            text = _format_news_text(news, desc)
            subscribers = get_active_subscribers()
            if subscribers:
                sent_count = _loop.run_until_complete(send_to_subscribers(bot, text, news.get("image_url", "")))
                if sent_count > 0:
                    logger.info(f"[Subscribers] Sent news: {news['title'][:30]}")
            _loop.run_until_complete(asyncio.sleep(2))
    except Exception as e:
        logger.error(f"[Subscribers] Error: {e}", exc_info=True)


def daily_cleanup():
    """Ежедневная очистка БД (в полночь)."""
    global _last_cleanup_date
    try:
        today = datetime.now().date()
        if _last_cleanup_date != today:
            cleanup_old_news(days=7)
            cleanup_old_digest_urls(days=30)
            _last_cleanup_date = today
            logger.info("[Housekeeping] Daily cleanup performed.")
    except Exception as e:
        logger.error(f"[Housekeeping] Cleanup error: {e}", exc_info=True)


def hourly_housekeeping():
    """Фоновые задачи: погода, дайджесты (раз в час)."""
    global _last_digest_date, _last_digest_type
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        now = datetime.now()

        # Погода (~10:00)
        if now.hour == 10 and now.minute < 15 and _last_digest_date != now.date():
            _loop.run_until_complete(publish_weather(bot))

        # Дайджесты (11:00–21:00)
        if 11 <= now.hour < 21 and now.minute < 5 and _last_digest_date != now.date():
            dtype = random.choice(["ainews", "gadgets", "cars"])
            _loop.run_until_complete(publish_digest(bot, dtype))
            _last_digest_date = now.date()
            _last_digest_type = dtype
    except Exception as e:
        logger.error(f"[Housekeeping] Error: {e}", exc_info=True)


def main():
    scheduler = BlockingScheduler()
    scheduler.add_job(individual_channel_post, 'cron', minute='*/7', id='channel_publish')
    scheduler.add_job(hourly_subscriber_posts, 'cron', minute=0, id='subscriber_publish')
    scheduler.add_job(hourly_housekeeping, 'cron', minute=5, id='housekeeping')
    scheduler.add_job(daily_cleanup, 'cron', hour=0, minute=0, id='daily_cleanup')

    logger.info("Scheduler started with 4 jobs.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
