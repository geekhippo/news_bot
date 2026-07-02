import sqlite3
import hashlib
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import re


logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "news.db"


@contextmanager
def get_db():
    """Получить соединение с БД (context manager — закрывает автоматически)"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Инициализация базы данных"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                description TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                source TEXT NOT NULL,
                hash TEXT UNIQUE NOT NULL,
                is_hot INTEGER DEFAULT 0,
                is_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                published_at TIMESTAMP,
                sent_at TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_news_hash ON news(hash);
            CREATE INDEX IF NOT EXISTS idx_news_sent ON news(is_sent);
            CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at);

            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE NOT NULL,
                username TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS digest_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                topic TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(url, topic)
            );

            CREATE INDEX IF NOT EXISTS idx_digest_urls_url ON digest_urls(url);
            CREATE INDEX IF NOT EXISTS idx_digest_urls_topic ON digest_urls(topic);

            CREATE TABLE IF NOT EXISTS published_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_published_urls ON published_urls(url);
        """)

        # Миграция: добавить published_at
        cols = [r[1] for r in conn.execute("PRAGMA table_info(news)").fetchall()]
        if 'published_at' not in cols:
            logger.info("Migration: adding published_at column")
            conn.execute("ALTER TABLE news ADD COLUMN published_at TIMESTAMP")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at)")


def make_hash(title: str, link: str) -> str:
    """Создать хеш новости для дедупликации"""
    normalized = re.sub(r'[^\w\s]', '', title.strip().lower())
    normalized = re.sub(r'\s+', ' ', normalized)
    text = f"{normalized}|{link.strip()}"
    return hashlib.md5(text.encode()).hexdigest()


def add_news(title: str, link: str, description: str = "",
             image_url: str = "", source: str = "",
             published_at: datetime | None = None) -> bool:
    """
    Добавить новость в БД.
    Возвращает True если новость новая, False если дубликат.
    """
    h = make_hash(title, link)
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO news (title, link, description, image_url, source, hash, published_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, link, description, image_url, source, h,
                 published_at.isoformat() if published_at else None)
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False


def get_unsent_news(limit: int = 20) -> list:
    """Получить неотправленные новости (самые свежие по дате публикации)"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM news WHERE is_sent = 0 ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_latest_unsent_per_source(source: str) -> dict | None:
    """Получить самую свежую неотправленную новость из конкретного источника."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM news WHERE is_sent = 0 AND source = ?
               ORDER BY COALESCE(published_at, created_at) DESC LIMIT 1""",
            (source,)
        ).fetchone()
        return dict(row) if row else None


def get_all_sources() -> list:
    """Получить список всех источников с неотправленными новостями"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT source FROM news WHERE is_sent = 0"""
        ).fetchall()
        return [row['source'] for row in rows]


def get_latest_from_source(source: str) -> dict | None:
    """Получить самую свежую новость из источника (независимо от is_sent)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM news WHERE source = ? ORDER BY COALESCE(published_at, created_at) DESC LIMIT 1",
            (source,)
        ).fetchone()
        return dict(row) if row else None


def clear_unsent_news():
    """Сбросить флаги is_sent для новостей за последние 7 дней (используется в 00:00)"""
    with get_db() as conn:
        conn.execute("UPDATE news SET is_sent = 0, sent_at = NULL WHERE is_sent = 1 AND date(COALESCE(published_at, created_at)) >= date('now', '-7 days')")
        conn.commit()
        count = conn.execute("SELECT changes() as c").fetchone()['c']
        logger.info(f"clear_unsent_news: reset is_sent for {count} news from last 7 days")


def update_news_description(news_id: int, description: str):
    """Обновить описание новости (например, AI-сгенерированное)"""
    with get_db() as conn:
        conn.execute("UPDATE news SET description = ? WHERE id = ?", (description, news_id))
        conn.commit()


def mark_as_sent(news_ids: list):
    """Пометить новости как отправленные"""
    if not news_ids:
        return
    with get_db() as conn:
        placeholders = ','.join('?' * len(news_ids))
        conn.execute(
            f"""UPDATE news SET is_sent = 1, sent_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})""",
            news_ids
        )
        conn.commit()


def get_active_subscribers() -> list:
    """Получить активных подписчиков"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM subscribers WHERE is_active = 1"
        ).fetchall()
        return [row['chat_id'] for row in rows]


def add_subscriber(chat_id: int, username: str = ""):
    """Добавить подписчика"""
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO subscribers (chat_id, username) VALUES (?, ?)""",
            (chat_id, username)
        )
        conn.commit()


def deactivate_subscriber(chat_id: int):
    """Деактивировать подписчика"""
    with get_db() as conn:
        conn.execute(
            "UPDATE subscribers SET is_active = 0 WHERE chat_id = ?",
            (chat_id,)
        )
        conn.commit()



def is_url_published(url: str) -> bool:
    """Проверить, была ли новость уже опубликована в канал."""
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM published_urls WHERE url = ?", (url,)).fetchone()
        return row is not None


def mark_url_published(url: str):
    """Пометить URL как опубликованный в канал."""
    try:
        with get_db() as conn:
            conn.execute("INSERT INTO published_urls (url) VALUES (?)", (url,))
            conn.commit()
    except sqlite3.IntegrityError:
        pass  # уже есть


def cleanup_old_published_urls(days: int = 7):
    """Очистить старые URL публикаций."""
    with get_db() as conn:
        conn.execute("DELETE FROM published_urls WHERE created_at < datetime('now', ?)", (f'-{days} days',))
        conn.commit()


def is_digest_url_seen(url: str, topic: str) -> bool:
    """Проверить, показывалась ли уже эта новость в дайджесте."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM digest_urls WHERE url = ? AND topic = ?",
            (url, topic)
        ).fetchone()
        return row is not None


def mark_digest_url_seen(url: str, topic: str):
    """Пометить URL как показанный в дайджесте (навсегда)."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO digest_urls (url, topic) VALUES (?, ?)",
                (url, topic)
            )
            conn.commit()
    except sqlite3.IntegrityError:
        pass  # уже есть


def cleanup_old_digest_urls(days: int = 30):
    """Очистить URL дайджестов старше N дней."""
    with get_db() as conn:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        deleted = conn.execute(
            "DELETE FROM digest_urls WHERE created_at < ?",
            (cutoff,)
        ).rowcount
        if deleted:
            logger.info(f"cleanup_old_digest_urls: removed {deleted} entries older than {days}d")
        conn.commit()


def cleanup_old_news(days: int = 7):
    """Удалить новости старше N дней"""
    with get_db() as conn:
        cutoff = datetime.now() - timedelta(days=days)
        conn.execute(
            "DELETE FROM news WHERE COALESCE(published_at, created_at) < ?",
            (cutoff.isoformat(),)
        )
        conn.commit()



def get_latest_news(limit=5):
    """Получить 5 самых свежих новостей из всех источников."""
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM news WHERE is_sent = 0 ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats() -> dict:
    """Получить статистику"""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM news").fetchone()['c']
        unsent = conn.execute("SELECT COUNT(*) as c FROM news WHERE is_sent = 0").fetchone()['c']
        subscribers = conn.execute("SELECT COUNT(*) as c FROM subscribers WHERE is_active = 1").fetchone()['c']
        sources = conn.execute(
            "SELECT source, COUNT(*) as c FROM news GROUP BY source ORDER BY c DESC"
        ).fetchall()
        digest_sources = conn.execute(
            "SELECT topic, COUNT(*) as c FROM digest_urls GROUP BY topic ORDER BY c DESC"
        ).fetchall()
        # Размер БД
        row = conn.execute("SELECT page_count, page_size FROM pragma_page_count(), pragma_page_size()").fetchone()
        db_size = row["page_count"] * row["page_size"]

        return {
            "total": total,
            "unsent": unsent,
            "subscribers": subscribers,
            "sources": {row['source']: row['c'] for row in sources},
            "digest_sources": {row['topic']: row['c'] for row in digest_sources},
            "db_size": db_size,
        }


if __name__ == "__main__":
    init_db()
    print("Database initialized!")
    print(f"Stats: {get_stats()}")
