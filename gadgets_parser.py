"""
Парсер 5 авторитетных англоязычных источников о гаджетах.
Каждый вызов get_gadgets_news() возвращает самую свежую НЕПОКАЗАННУЮ новость из каждой ленты.
Уже показанные URL хранятся в БД (таблица digest_urls) — ни одна новость не показывается дважды.
"""
import logging
from datetime import datetime, timedelta

import feedparser
from bs4 import BeautifulSoup

from database import is_digest_url_seen, mark_digest_url_seen

logger = logging.getLogger(__name__)

TOPIC = "gadgets"
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


class BaseGadgetParser:
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
        logger.info(f"Gadgets [{self.SOURCE_NAME}]: loaded {len(result)} entries")
        return result

    def get_unseen(self) -> dict | None:
        """Вернуть самую свежую НЕПОКАЗАННУЮ новость из ленты."""
        entries = self._fetch_and_parse()
        for news in entries:
            if not is_digest_url_seen(news['link'], TOPIC):
                mark_digest_url_seen(news['link'], TOPIC)
                return news
        return None


class TheVergeGadgetsParser(BaseGadgetParser):
    URL = "https://www.theverge.com/rss/index.xml"
    SOURCE_NAME = "The Verge"
    SOURCE_ICON = "🌐"
    GADGET_KEYWORDS = [
        'phone', 'smartphone', 'laptop', 'tablet', 'watch', 'smartwatch',
        'headphone', 'earbuds', 'speaker', 'display', 'monitor', 'camera',
        'drone', 'robot', 'homepod', 'airpods', 'ipad', 'macbook',
        'gadget', 'device', 'wearable', 'foldable', 'charger', 'usb-c',
        'thunderbolt', 'android', 'ios', 'apple', 'samsung', 'google',
        'pixel', 'galaxy', 'iphone', 'mac', 'ipad', 'airpods',
    ]

    def _is_relevant(self, entry) -> bool:
        text = (entry.get('title', '') + ' ' + entry.get('summary', '')).lower()
        return any(kw in text for kw in self.GADGET_KEYWORDS)


class TechCrunchGadgetsParser(BaseGadgetParser):
    URL = "https://techcrunch.com/category/gadgets/feed/"
    SOURCE_NAME = "TechCrunch"
    SOURCE_ICON = "🚀"


class NineToFiveGoogleParser(BaseGadgetParser):
    URL = "https://9to5google.com/feed/"
    SOURCE_NAME = "9to5Google"
    SOURCE_ICON = "📱"


class AndroidCentralParser(BaseGadgetParser):
    URL = "https://www.androidcentral.com/rss.xml"
    SOURCE_NAME = "Android Central"
    SOURCE_ICON = "🤖"


class ArsTechnicaGadgetsParser(BaseGadgetParser):
    URL = "https://feeds.arstechnica.com/arstechnica/technology-lab"
    SOURCE_NAME = "Ars Technica"
    SOURCE_ICON = "⚙️"


GADGET_PARSERS = [
    TheVergeGadgetsParser(),
    TechCrunchGadgetsParser(),
    NineToFiveGoogleParser(),
    AndroidCentralParser(),
    ArsTechnicaGadgetsParser(),
]


async def get_gadgets_news() -> list:
    news_list = []
    errors = []

    for parser in GADGET_PARSERS:
        try:
            news = parser.get_unseen()
            if news and news['title']:
                news_list.append(news)
            else:
                errors.append(f"{parser.SOURCE_NAME}: exhausted")
        except Exception as e:
            errors.append(f"{parser.SOURCE_NAME}: {e}")
            logger.warning(f"Gadgets parser error [{parser.SOURCE_NAME}]: {e}")

    if errors:
        logger.info(f"Gadgets: {len(news_list)} new, {len(errors)} exhausted: {'; '.join(errors[:3])}")
    else:
        logger.info(f"Gadgets: {len(news_list)} news from {len(GADGET_PARSERS)} sources")

    return news_list
