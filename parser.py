import feedparser
import requests
from bs4 import BeautifulSoup
import re
import json
import logging
from datetime import datetime
import dateutil.parser
import socket
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Mapping of Russian month names to numbers
RU_MONTHS = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
}

def _parse_russian_date(date_str: str) -> Optional[datetime]:
    """Convert Russian human‑readable date to datetime object."""
    if not date_str:
        return None
    now = datetime.now()

    # ‘Сегодня в 12:22’
    m = re.search(r'Сегодня в (\d{1,2}):(\d{2})', date_str)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)

    # ‘11:56 / 22 июня’
    m = re.search(r'(\d{1,2}):(\d{2})\s*/\s*(\d{1,2})\s+(\w+)', date_str)
    if m:
        hour, minute, day, month_str = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4).lower()
        month = RU_MONTHS.get(month_str)
        if month:
            return now.replace(year=now.year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)

    # Fallback to dateutil
    try:
        return dateutil.parser.parse(date_str, fuzzy=True)
    except Exception:
        return None


def _fetch_og_image(self, url: str) -> str:
    """Try to get the OG image or first <img> on a page."""
    try:
        resp = requests.get(url, headers=self._headers(), timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        meta = soup.find('meta', property='og:image')
        if meta and meta.get('content'):
            return meta['content']

        img = soup.find('img', class_=re.compile(r'wp-image-\d+'))
        if img and img.get('src'):
            return img['src']
    except Exception as e:
        logger.debug(f"og:image fetch failed for {url}: {e}")
    return ''


class BaseParser:
    """Base class that provides common utilities."""

    def _headers(self) -> dict:          # pragma: no cover
        raise NotImplementedError("Sub‑classes must implement _headers()")

    def safe_get(self, url: str) -> Optional[str]:
        """GET with timeout and generic error handling."""
        try:
            resp = requests.get(url, headers=self._headers(), timeout=15)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return None

    def fetch_og_image(self, url: str) -> str:
        return _fetch_og_image(self, url)


# -------------------------- Concrete parsers --------------------------

class UraParser(BaseParser):
    URL = "https://ura.news/rss"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0'}

    def get_news(self) -> List[Dict[str, Any]]:
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        items = []
        for entry in feed.entries[:10]:
            pub = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else None
            items.append({
                "title": entry.title,
                "link": entry.link,
                "description": BeautifulSoup(entry.get('summary', ''), 'html.parser')
                                 .get_text(strip=True)[:1200],
                "image": self.fetch_og_image(entry.link),
                "source": "URA.RU",
                "published_at": pub,
            })
        return items


class E1Parser(BaseParser):
    URL = "https://www.e1.ru/text/"
    BASE_URL = "https://www.e1.ru"
    PATTERN = re.compile(
        r'/text/(gorod|incidents|criminal|realty|longread|auto|sport|health|business|politics|world)/\d{4}/\d{2}/\d{2}/\d+/'
    )

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    def get_news(self) -> List[Dict[str, Any]]:
        html = self.safe_get(self.URL)
        if not html:
            return []
        soup = BeautifulSoup(html, 'html.parser')

        news: List[Dict[str, Any]] = []
        seen = set()

        for a in soup.find_all('a', href=True):
            href = a['href']
            if not self.PATTERN.search(href) or '/comments/' in href:
                continue
            full_url = href if href.startswith('http') else self.BASE_URL + href
            if full_url in seen:
                continue
            seen.add(full_url)

            title = a.get('title', '').strip() or a.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            dm = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', full_url)
            published = datetime(int(dm.group(1)), int(dm.group(2)), int(dm.group(3))) if dm else None

            news.append({
                "title": title,
                "link": full_url,
                "description": '',
                "image": self.fetch_og_image(full_url),
                "source": "E1.RU",
                "published_at": published,
            })

            if len(news) >= 10:
                break

        return news


class RU66Parser(BaseParser):
    URL = "https://www.66.ru/news/"
    BASE_URL = "https://www.66.ru"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    def get_news(self) -> List[Dict[str, Any]]:
        html = self.safe_get(self.URL)
        if not html:
            return []
        soup = BeautifulSoup(html, 'html.parser')

        out: List[Dict[str, Any]] = []
        for item in soup.select('div.section_item')[:10]:
            title_tag = item.select_one('a.section_item-body-link') or item.select_one('h2.section_item-body-title')
            if not title_tag:
                continue
            link = title_tag.get('href', '')
            if not link.startswith('http'):
                link = self.BASE_URL + link

            title_el = item.select_one('h2.section_item-body-title')
            title = title_el.get_text(strip=True) if title_el else title_tag.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            img = item.select_one('img.section_item-picture')
            image_url = (img.get('src') or img.get('data-src') or '').strip()
            if image_url and not image_url.startswith('http'):
                image_url = self.BASE_URL + image_url

            description = ''
            body = item.select_one('div.section_item-body')
            if body:
                for div in body.find_all('div', recursive=False):
                    txt = div.get_text(strip=True)
                    if txt and txt != title and len(txt) > 20:
                        description = txt[:270]
                        break

            ds = ''
            de = item.select_one('span.section_item-date-time')
            if de:
                ds = de.get_text(strip=True)
            published = _parse_russian_date(ds)

            out.append({
                "title": title,
                "link": link,
                "description": description,
                "image": image_url,
                "source": "66.RU",
                "published_at": published,
            })

        return out


class ObltvParser(BaseParser):
    URL = "https://obltv.ru/news/"
    BASE_URL = "https://obltv.ru"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    def get_news(self) -> List[Dict[str, Any]]:
        html = self.safe_get(self.URL)
        if not html:
            return []
        soup = BeautifulSoup(html, 'html.parser')

        items = soup.select('div.moskvin-item-row')[:10]
        out = []

        for item in items:
            tl = item.select_one('a.moskvin-item-title')
            if not tl:
                continue
            title = tl.get_text(strip=True)
            if len(title) < 5:
                continue

            link = tl.get('href', '')
            full = link if link.startswith('http') else self.BASE_URL + link

            img = item.select_one('img.moskvin-item-image')
            image_url = (img.get('src') or img.get('data-src') or '').strip()
            if image_url and not image_url.startswith('http'):
                image_url = self.BASE_URL + image_url

            de = item.select_one('div.moskvin-item-text')
            description = de.get_text(strip=True)[:270] if de else ''

            ds = ''
            dte = item.select_one('div.moskvin-item-date')
            if dte:
                ds = dte.get_text(strip=True)
            published = _parse_russian_date(ds)

            out.append({
                "title": title,
                "link": full,
                "description": description,
                "image": image_url,
                "source": "OBLTV.RU",
                "published_at": published,
            })

        return out


class JustMediaParser(BaseParser):
    URL = "https://justmedia.ru/news/"
    BASE_URL = "https://justmedia.ru"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    def get_news(self) -> List[Dict[str, Any]]:
        html = self.safe_get(self.URL)
        if not html:
            return []
        soup = BeautifulSoup(html, 'html.parser')

        items = soup.select('div.article-item')[:10]
        out = []

        for item in items:
            tl = item.select_one('a.article-item__title')
            if not tl:
                continue
            title = tl.get_text(strip=True)
            if len(title) < 5:
                continue

            link = tl.get('href', '')
            full = link if link.startswith('http') else self.BASE_URL + link

            img = item.select_one('img.article-item__image')
            image_url = (img.get('src') or img.get('data-src') or '').strip()
            if image_url and not image_url.startswith('http'):
                image_url = self.BASE_URL + image_url

            de = item.select_one('div.article-item__text')
            description = de.get_text(strip=True)[:270] if de else ''

            ds = ''
            dte = item.select_one('div.article-item__date')
            if dte:
                ds = dte.get_text(strip=True)
            published = _parse_russian_date(ds)

            out.append({
                "title": title,
                "link": full,
                "description": description,
                "image": image_url,
                "source": "JustMedia",
                "published_at": published,
            })

        return out


class LentaParser(BaseParser):
    URL = "https://lenta.ru/rss/news"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0'}

    def get_news(self) -> List[Dict[str, Any]]:
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        items = []
        for entry in feed.entries[:10]:
            pub = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else None
            items.append({
                "title": entry.title,
                "link": entry.link,
                "description": BeautifulSoup(entry.get('summary', ''), 'html.parser')
                                 .get_text(strip=True)[:1200],
                "image": self.fetch_og_image(entry.link),
                "source": "Lenta.ru",
                "published_at": pub,
            })
        return items


class LifeParser(BaseParser):
    URL = "https://life.ru/rss/news"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0'}

    def get_news(self) -> List[Dict[str, Any]]:
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        items = []
        for entry in feed.entries[:10]:
            pub = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else None
            items.append({
                "title": entry.title,
                "link": entry.link,
                "description": BeautifulSoup(entry.get('summary', ''), 'html.parser')
                                 .get_text(strip=True)[:1200],
                "image": self.fetch_og_image(entry.link),
                "source": "Life.ru",
                "published_at": pub,
            })
        return items


class KommersantParser(BaseParser):
    URL = "https://www.kommersant.ru/rss/news.xml"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0'}

    def get_news(self) -> List[Dict[str, Any]]:
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        items = []
        for entry in feed.entries[:10]:
            pub = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else None
            items.append({
                "title": entry.title,
                "link": entry.link,
                "description": BeautifulSoup(entry.get('summary', ''), 'html.parser')
                                 .get_text(strip=True)[:1200],
                "image": self.fetch_og_image(entry.link),
                "source": "Коммерсантъ",
                "published_at": pub,
            })
        return items


class VedomostiParser(BaseParser):
    URL = "https://www.vedomosti.ru/rss/news"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0'}

    def get_news(self) -> List[Dict[str, Any]]:
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        items = []
        for entry in feed.entries[:10]:
            pub = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else None
            items.append({
                "title": entry.title,
                "link": entry.link,
                "description": BeautifulSoup(entry.get('summary', ''), 'html.parser')
                                 .get_text(strip=True)[:1200],
                "image": self.fetch_og_image(entry.link),
                "source": "Ведомости",
                "published_at": pub,
            })
        return items


class UralWebParser(BaseParser):
    URL = "https://uralweb.ru/rss"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0'}

    def get_news(self) -> List[Dict[str, Any]]:
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        items = []
        for entry in feed.entries[:10]:
            pub = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else None
            items.append({
                "title": entry.title,
                "link": entry.link,
                "description": BeautifulSoup(entry.get('summary', ''), 'html.parser')
                                 .get_text(strip=True)[:1200],
                "image": self.fetch_og_image(entry.link),
                "source": "UralWeb",
                "published_at": pub,
            })
        return items


class ItsmycityParser(BaseParser):
    URL = "https://itsmycity.ru/rss"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0'}

    def get_news(self) -> List[Dict[str, Any]]:
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        items = []
        for entry in feed.entries[:10]:
            pub = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else None
            items.append({
                "title": entry.title,
                "link": entry.link,
                "description": BeautifulSoup(entry.get('summary', ''), 'html.parser')
                                 .get_text(strip=True)[:1200],
                "image": self.fetch_og_image(entry.link),
                "source": "Itsmycity",
                "published_at": pub,
            })
        return items


class OblGazetaParser(BaseParser):
    URL = "https://oblgazeta.ru/rss"

    def _headers(self) -> dict:
        return {'User-Agent': 'Mozilla/5.0'}

    def get_news(self) -> List[Dict[str, Any]]:
        socket.setdefaulttimeout(15)
        try:
            feed = feedparser.parse(self.URL)
        finally:
            socket.setdefaulttimeout(None)

        items = []
        for entry in feed.entries[:10]:
            pub = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else None
            items.append({
                "title": entry.title,
                "link": entry.link,
                "description": BeautifulSoup(entry.get('summary', ''), 'html.parser')
                                 .get_text(strip=True)[:1200],
                "image": self.fetch_og_image(entry.link),
                "source": "Областная газета",
                "published_at": pub,
            })
        return items
