"""
Парсер 5 авторитетных российских авто-источников.
Каждый вызов get_cars_news() возвращает самую свежую НЕПОКАЗАННУЮ новость из каждой ленты.
Уже показанные URL хранятся в БД (таблица digest_urls) — ни одна новость не показывается дважды.
"""
import logging
from datetime import datetime, timedelta

import feedparser
from bs4 import BeautifulSoup

from database import is_digest_url_seen, mark_digest_url_seen

logger = logging.getLogger(__name__)

TOPIC = "cars"
_FEED_CACHE_TTL = 1800


def _parse_entry(entry, source_name: str, source_icon: str) -> dict:
    title = entry.get('title', '').strip()
    link = entry.get('link', '')
    description = entry.get('summary', entry.get('description', ''))
    if description:
        description = BeautifulSoup(description, 'html.parser').get_text(strip=True)[:500]

    published = None
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        published = datetime(*entry.published_parsed[:6])

    return {
        "title": title,
        "link": link,
        "description": description,
        "source": source_name,
        "source_icon": source_icon,
        "published_at": published,
    }


class BaseCarParser:
    URL = ""
    SOURCE_NAME = ""
    SOURCE_ICON = ""
    _feed_cache: list[dict] | None = None
    _feed_cache_ts: datetime | None = None

    def _is_relevant(self, entry) -> bool:
        return True

    def _fetch_and_parse(self) -> list[dict]:
        now = datetime.now()
        if self._feed_cache is not None and self._feed_cache_ts is not None:
            if now - self._feed_cache_ts < timedelta(seconds=_FEED_CACHE_TTL):
                return self._feed_cache

        import socket
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        result = []
        for entry in feed.entries:
            if not self._is_relevant(entry):
                continue
            news = _parse_entry(entry, self.SOURCE_NAME, self.SOURCE_ICON)
            if news.get('link'):
                result.append(news)

        self._feed_cache = result
        self._feed_cache_ts = now
        logger.info(f"Cars [{self.SOURCE_NAME}]: loaded {len(result)} entries")
        return result

    def get_unseen(self) -> dict | None:
        """Вернуть самую свежую НЕПОКАЗАННУЮ новость из ленты."""
        entries = self._fetch_and_parse()
        for news in entries:
            if not is_digest_url_seen(news['link'], TOPIC):
                mark_digest_url_seen(news['link'], TOPIC)
                return news
        return None


class AutoMailParser(BaseCarParser):
    URL = "https://auto.mail.ru/rss/"
    SOURCE_NAME = "Auto.Mail.ru"
    SOURCE_ICON = "📧"


class KolesaParser(BaseCarParser):
    URL = "https://kolesa.ru/rss"
    SOURCE_NAME = "Kolesa.ru"
    SOURCE_ICON = "🔧"


class FiveKolesoParser(BaseCarParser):
    URL = "http://www.5koleso.ru/rss/"
    SOURCE_NAME = "5koleso.ru"
    SOURCE_ICON = "🚗"


class AutoOnlinerParser(BaseCarParser):
    URL = "https://auto.onliner.by/feed"
    SOURCE_NAME = "Auto.Onliner.by"
    SOURCE_ICON = "🚘"


class IXBTCarsParser(BaseCarParser):
    URL = "https://www.ixbt.com/export/rss.xml"
    SOURCE_NAME = "iXBT"
    SOURCE_ICON = "🛡️"
    AUTO_KEYWORDS = [
        'авто', 'машина', 'автомобиль', 'внедорожник', 'кроссовер',
        'легковой', 'электромобиль', 'двигатель', 'коробка', 'привод',
        'mercedes', 'bmw', 'audi', 'toyota', 'honda', 'ford', 'volkswagen',
        'nissan', 'hyundai', 'kia', 'renault', 'lada', 'лада',
        'шины', 'бензин', 'зарядка', 'пробег', 'лизинг',
        'автомобильная', 'электро',
    ]

    def _is_relevant(self, entry) -> bool:
        text = (entry.get('title', '') + ' ' + entry.get('summary', '')).lower()
        return any(kw in text for kw in self.AUTO_KEYWORDS)


CAR_PARSERS = [
    AutoMailParser(),
    KolesaParser(),
    FiveKolesoParser(),
    AutoOnlinerParser(),
    IXBTCarsParser(),
]


async def get_cars_news() -> list:
    news_list = []
    errors = []

    for parser in CAR_PARSERS:
        try:
            news = parser.get_unseen()
            if news and news['title']:
                news_list.append(news)
            else:
                errors.append(f"{parser.SOURCE_NAME}: exhausted")
        except Exception as e:
            errors.append(f"{parser.SOURCE_NAME}: {e}")
            logger.warning(f"Cars parser error [{parser.SOURCE_NAME}]: {e}")

    if errors:
        logger.info(f"Cars: {len(news_list)} new, {len(errors)} exhausted: {'; '.join(errors[:3])}")
    else:
        logger.info(f"Cars: {len(news_list)} news from {len(CAR_PARSERS)} sources")

    return news_list
