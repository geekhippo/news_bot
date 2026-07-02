"""
Парсер 5 авторитетных англоязычных AI-источников.
Каждый вызов get_ai_news() возвращает самую свежую НЕПОКАЗАННУЮ новость из каждой ленты.
Уже показанные URL хранятся в БД (таблица digest_urls) — ни одна новость не показывается дважды.
При исчерпании всех URL в ленте — источник пропускается до появления новой статьи.
"""
import logging
from datetime import datetime, timedelta

import feedparser
from bs4 import BeautifulSoup

from database import is_digest_url_seen, mark_digest_url_seen

logger = logging.getLogger(__name__)

TOPIC = "ai"
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

    image_url = ''
    enclosures = entry.get('enclosures', [])
    if enclosures:
        image_url = enclosures[0].get('href', '')
    if not image_url:
        soup = BeautifulSoup(entry.get('summary', ''), 'html.parser')
        img = soup.find('img')
        if img and img.get('src'):
            image_url = img.get('src', '')

    return {
        "title": title,
        "link": link,
        "description": description,
        "image": image_url,
        "source": source_name,
        "source_icon": source_icon,
        "published_at": published,
    }


class BaseAIParser:
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
        return result

    def get_unseen(self) -> dict | None:
        """Вернуть самую свежую НЕПОКАЗАННУЮ новость из ленты.
        Пропускает все URL, уже сохранённые в digest_urls для topic=ai."""
        entries = self._fetch_and_parse()
        for news in entries:
            if not is_digest_url_seen(news['link'], TOPIC):
                mark_digest_url_seen(news['link'], TOPIC)
                return news
        return None


class TheVergeAIParser(BaseAIParser):
    URL = "https://www.theverge.com/rss/index.xml"
    SOURCE_NAME = "The Verge"
    SOURCE_ICON = "🌐"
    AI_KEYWORDS = [
        ' ai ', 'artificial intelligence', 'gpt', 'llm', 'openai',
        'anthropic', 'gemini', 'claude', 'machine learning', 'chatgpt',
        'copilot', 'neural', 'deepseek', 'mistral', 'ai agent',
        'language model', 'large language', 'generative ai',
    ]

    def _is_relevant(self, entry) -> bool:
        text = (entry.get('title', '') + ' ' + entry.get('summary', '')).lower()
        return any(kw in text for kw in self.AI_KEYWORDS)


class ArsTechnicaParser(BaseAIParser):
    URL = "https://feeds.arstechnica.com/arstechnica/technology-lab"
    SOURCE_NAME = "Ars Technica"
    SOURCE_ICON = "⚙️"


class TechCrunchAIParser(BaseAIParser):
    URL = "https://techcrunch.com/category/artificial-intelligence/feed/"
    SOURCE_NAME = "TechCrunch"
    SOURCE_ICON = "🚀"


class WiredAIParser(BaseAIParser):
    URL = "https://www.wired.com/feed/tag/ai/latest/rss"
    SOURCE_NAME = "Wired"
    SOURCE_ICON = "🔌"


class MITTechReviewParser(BaseAIParser):
    URL = "https://www.technologyreview.com/topic/artificial-intelligence/feed/"
    SOURCE_NAME = "MIT Tech Review"
    SOURCE_ICON = "🔬"


AI_PARSERS = [
    TheVergeAIParser(),
    ArsTechnicaParser(),
    TechCrunchAIParser(),
    WiredAIParser(),
    MITTechReviewParser(),
]


async def get_ai_news() -> list:
    """Собрать AI-новости — самую свежую НЕПОКАЗАННУЮ из каждого источника."""
    news_list = []
    errors = []

    for parser in AI_PARSERS:
        try:
            news = parser.get_unseen()
            if news and news['title']:
                news_list.append(news)
            else:
                errors.append(f"{parser.SOURCE_NAME}: exhausted")
        except Exception as e:
            errors.append(f"{parser.SOURCE_NAME}: {e}")
            logger.warning(f"AI parser error [{parser.SOURCE_NAME}]: {e}")

    if errors:
        logger.info(f"AI: {len(news_list)} new, {len(errors)} exhausted: {'; '.join(errors[:3])}")
    else:
        logger.info(f"AI: {len(news_list)} news from {len(AI_PARSERS)} sources")

    return news_list
