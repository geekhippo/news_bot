"""
Новостной бот Екатеринбурга.
Собирает новости из 10 источников, выбирает главные с помощью AI,
отправляет подписчикам каждый час.
"""
import asyncio
import os
import sys
import logging
import difflib
import re
from datetime import datetime
from pathlib import Path

import io

from dotenv import load_dotenv
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, BotCommand
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

sys.path.insert(0, str(Path(__file__).parent))

from parser import UraParser, E1Parser, RU66Parser, ObltvParser, JustMediaParser, LentaParser, LifeParser, KommersantParser, VedomostiParser, UralWebParser, ItsmycityParser, OblGazetaParser
from database import (
    init_db, add_news, mark_as_sent,
    get_active_subscribers, add_subscriber, deactivate_subscriber,
    cleanup_old_news, cleanup_old_digest_urls, get_stats, get_latest_unsent_per_source, get_all_sources,
    get_latest_from_source, clear_unsent_news, update_news_description,
    make_hash, get_db, is_url_published, mark_url_published,
)
from ai import generate_summary, _call_ai
from ai_news_parser import get_ai_news
from gadgets_parser import get_gadgets_news
from cars_parser import get_cars_news
from services import get_weather, get_exchange_rates, format_weather, format_rates

load_dotenv(Path(__file__).parent / ".env", override=False)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Не логировать URL с токенами Telegram API
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL", "60"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "ekbsummary")

# Типы дайджестов
DIGEST_TYPES = {
    "ainews": {"name": "AI-новости", "emoji": "🤖"},
    "gadgets": {"name": "Гаджеты", "emoji": "📱"},
    "cars": {"name": "Авто", "emoji": "🚗"},
}

PARSERS = [
    UraParser(),
    E1Parser(),
    RU66Parser(),
    ObltvParser(),
    JustMediaParser(),
    LentaParser(),
    LifeParser(),
    KommersantParser(),
    VedomostiParser(),
    UralWebParser(),
    ItsmycityParser(),
    OblGazetaParser(),
]

# Глобальная ссылка на приложение (для _news_loop)
_app = None


# === Утилиты ===


def _download_image(url: str) -> bytes | None:
    """Скачать изображение через наш сервер (обходит CDN-блокировки) и вернуть байты"""
    try:
        resp = requests.get(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': 'https://www.e1.ru/',
            },
            timeout=15
        )
        if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image/'):
            return resp.content
    except Exception as e:
        logger.debug(f"Image download failed for {url[:80]}: {e}")
    return None


def _is_valid_image_url(url: str) -> bool:
    """Проверить, что URL выглядит как изображение"""
    if not url:
        return False
    if not url.startswith(('http://', 'https://')):
        return False
    # Отсекаем ссылки на веб-страницы
    skip_suffixes = {'.html', '.htm', '.php', '.asp', '.aspx', '.jsp'}
    clean = url.split('?')[0].split('#')[0].rstrip('/')
    for s in skip_suffixes:
        if clean.endswith(s):
            return False
    return True


def _safe_chat_id(update: Update) -> int:
    """Получить chat_id из update независимо от типа (message / callback_query)"""
    if update.effective_chat:
        return update.effective_chat.id
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message.chat_id
    raise ValueError("Cannot determine chat_id")


def _format_news_text(news):
    """Форматировать текст новости единообразно"""
    source_emoji = {
        "URA.RU": "\U0001f534",      # 🔴 красный шар
        "E1.RU": "\U0001f7e1",     # 🟡 жёлтый шар
        "66.RU": "\U0001f535",     # 🔵 синий шар
        "OBLTV.RU": "\U0001f7e2",  # 🟢 зелёный шар
        "JustMedia": "\U0001f7e3", # 🟣 фиолетовый шар
        "Lenta.ru": "\U0001f7e0",  # 🟠 оранжевый шар
        "Life.ru": "\U0001f7e4",   # 🟤 коричневый шар
        "Коммерсантъ": "\u26aa\ufe0f",  # ⚪ белый шар
        "Ведомости": "\U0001f7e7",  # 🩷 розовый шар
        "UralWeb": "\u26ab\ufe0f",  # ⚫ чёрный шар
    }.get(news['source'], "\U0001f4f0")
    desc = news.get('description', '') or ''
    return (
        f"{source_emoji} <b>{news['title']}</b>\n\n"
        f"{desc[:1740]}\n\n"
        f"\U0001f4ce <a href=\"{news['link']}\">Читать полностью</a>\n"
        f"\U0001f4f0 <a href=\"https://t.me/ekb_bot\">Новости Екатеринбурга</a>"
    )


# === Сбор и отправка новостей ===

async def collect_news():
    """Сбор новостей из всех источников, сохранение в БД.
    Генерирует AI-описание для всех новых новостей (единый визуальный формат).
    Возвращает количество новых новостей."""
    logger.info("=== collect_news() started ===")
    new_count = 0
    duplicate_count = 0

    for parser in PARSERS:
        try:
            # Синхронный парсинг не блокирует event loop — бежим в потоке
            news_list = await asyncio.to_thread(parser.get_news)
            logger.info(f"{parser.__class__.__name__}: {len(news_list)} новостей")

            for news in news_list:
                h = make_hash(news['title'], news['link'])
                is_new = add_news(
                    title=news['title'],
                    link=news['link'],
                    description=news.get('description', ''),
                    image_url=news.get('image', ''),
                    source=news.get('source', 'Unknown'),
                    published_at=news.get('published_at')
                )
                if is_new:
                    new_count += 1
                    # Генерируем AI-описание для новой новости
                    if OPENROUTER_API_KEY:
                        with get_db() as conn:
                            row = conn.execute("SELECT id FROM news WHERE hash = ?", (h,)).fetchone()
                            if row:
                                try:
                                    desc = await generate_summary(news['title'])
                                    update_news_description(row['id'], desc)
                                except Exception as e:
                                    logger.warning(f"AI desc failed: {e}")
                                # Задержка между AI-запросами
                                await asyncio.sleep(3)
                else:
                    duplicate_count += 1

        except Exception as e:
            logger.error(f"Error with {parser.__class__.__name__}: {e}")

    logger.info(f"Сбор завершён: {new_count} новых, {duplicate_count} дублей")
    return new_count



def _title_normalize(title: str) -> str:
    """Нормализовать заголовок для сравнения"""
    norm = re.sub(r'[^\w\s\u0430-\u044f\u0451]', '', title.lower())
    return ' '.join(norm.split())


def _dedup_by_title(news_list: list) -> list:
    """Удалить дубли по похожести заголовков (одна новость в разных источниках)"""
    result = []
    for news in news_list:
        title_norm = _title_normalize(news['title'])
        is_dup = False
        for existing in result:
            existing_norm = _title_normalize(existing['title'])
            ratio = difflib.SequenceMatcher(None, title_norm, existing_norm).ratio()
            if ratio > 0.6:
                logger.info(f"Dedup: [{news['source']}] {news['title'][:30]}... ~ [{existing['source']}] {existing['title'][:30]}... (ratio={ratio:.2f})")
                is_dup = True
                break
        if not is_dup:
            result.append(news)
    return result


def get_next_news_batch() -> list:
    """Получить следующий набор новостей: по одной самой свежей из каждого источника.
    Берёт неотправленные новости (is_sent=0). Если все уже отправлены —
    берёт самые свежие из каждого источника независимо от флага.
    Без фильтрации по геолокации — публикуем всё.
    AI-описания уже сгенерированы в collect_news()."""
    sources = get_all_sources()
    batch = []
    for source in sources:
        news = get_latest_unsent_per_source(source)
        if news:
            batch.append(news)

    # Если все новости уже отправлены — берём самые свежие из каждого источника
    if not batch:
        all_sources = ["URA.RU", "E1.RU", "66.RU", "OBLTV.RU", "JustMedia",
                        "Lenta.ru", "Life.ru", "Коммерсантъ", "Ведомости", "UralWeb",
                        "itsmycity", "Областная газета"]
        for source in all_sources:
            news = get_latest_from_source(source)
            if news:
                batch.append(news)

    # Сортируем по времени (самые свежие первыми по дате публикации)
    batch.sort(key=lambda n: (n['published_at'] or n['created_at']), reverse=True)
    # Дедупликация по похожести заголовков
    before = len(batch)
    batch = _dedup_by_title(batch)
    if len(batch) < before:
        logger.info(f"Dedup: убрано {before - len(batch)} дублей")

    # Ограничиваем до 5 новостей на публикацию
    batch = batch[:5]
    logger.info(f"get_next_news_batch: {len(batch)} новостей из {len(sources)} источников")
    return batch


async def _send_news_to_subscribers(news_list):
    """Отправить список новостей всем подписчикам"""
    if not news_list:
        return
    subscribers = get_active_subscribers()
    if not subscribers:
        return
    logger.info(f"Отправка {len(news_list)} новостей {len(subscribers)} подписчикам")
    sent_ids = []
    for news in news_list:
        text = _format_news_text(news)
        for chat_id in subscribers:
            try:
                if _is_valid_image_url(news.get('image_url')):
                    img_data = _download_image(news['image_url'])
                    if img_data:
                        await _app.bot.send_photo(
                            chat_id=chat_id,
                            photo=io.BytesIO(img_data),
                            caption=text[:1024],
                            parse_mode="HTML"
                        )
                    else:
                        await _app.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
                else:
                    await _app.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                await asyncio.sleep(0.1)
            except Forbidden:
                logger.warning(f"Бот заблокирован пользователем {chat_id}, отписываю")
                deactivate_subscriber(chat_id)
            except Exception as e:
                logger.error(f"send error {chat_id}: {e}")
        sent_ids.append(news['id'])
    mark_as_sent(sent_ids)
    cleanup_old_news(days=7)


async def _summarize_digest(news: dict, topic: str) -> str:
    """Кратко пересказать новость на русский (topic: "AI", "гаджетов", "автомобилей")"""
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    f"Ты — редактор дайджеста новостей о {topic}. "
                    "Напиши краткий пересказ новости на русском языке (2-3 предложения, по делу). "
                    "Не используй маркдаун, просто текст."
                ),
            },
            {
                "role": "user",
                "content": f"Заголовок: {news['title']}\n\nОписание: {news.get('description', '')}",
            },
        ]
        result = await _call_ai(messages, max_tokens=500)
        return result if result else news['title']
    except Exception as e:
        logger.warning(f"Digest summary failed: {e}")
        return news.get('description', news['title'])[:300]


async def _news_loop():
    """Фоновый цикл: сбор новостей каждый час, очистка базы в 00:00.
    Публикация в канал идёт через APScheduler (см. main())."""
    await asyncio.sleep(3)
    last_cleanup_date = None
    while True:
        try:
            now = datetime.now()
            today = now.date()

            # Очистка базы неотправленных новостей только в реальный полуночный час
            if last_cleanup_date != today and now.hour < 2:
                logger.info("midnight cleanup")
                clear_unsent_news()
                cleanup_old_digest_urls(days=30)
                last_cleanup_date = today

            # Собираем свежие новости каждый час
            await collect_news()

            # Публикация в канал: по 1 новости
            await _publish_news()

            # Рассылка подписчикам: по 5 новостей
            batch = get_next_news_batch()
            if batch:
                await _send_news_to_subscribers(batch)

            # Погода в ~10:00 (один раз в день)
            if now.hour == 10 and now.minute < 5:
                await _publish_weather()

            # Дайджесты 11:00-21:00 (один раз в день)
            if 11 <= now.hour < 21 and now.minute < 5:
                dtype = random.choice(["ainews", "gadgets", "cars"])
                await _publish_digest(dtype)
        except Exception as e:
            logger.error(f"_news_loop error: {e}", exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)


# === Команды ===

async def _reply(update: Update, text: str, **kwargs):
    """Отправить сообщение — работает и для обычных сообщений, и для callback_query"""
    if "disable_web_page_preview" not in kwargs:
        kwargs["disable_web_page_preview"] = True
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, **kwargs)
    elif update.message:
        await update.message.reply_text(text, **kwargs)
    else:
        chat_id = _safe_chat_id(update)
        await _app.bot.send_message(chat_id=chat_id, text=text, **kwargs)


async def _send_news_to_chat(update: Update, news_list):
    """Отправить список новостей в конкретный чат"""
    for news in news_list:
        text = _format_news_text(news)
        try:
            if _is_valid_image_url(news.get('image_url')):
                img_data = _download_image(news['image_url'])
                if img_data:
                    await update.effective_message.reply_photo(
                        photo=io.BytesIO(img_data),
                        caption=text[:1024],
                        parse_mode="HTML"
                    )
                else:
                    await update.effective_message.reply_text(
                        text,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
            else:
                await update.effective_message.reply_text(
                    text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
        except Exception as e:
            logger.warning(f"news send error: {e}")
            await _reply(update, text, parse_mode="HTML", disable_web_page_preview=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_user.username or ""
    add_subscriber(chat_id, username)

    keyboard = [
        [InlineKeyboardButton("\U0001f4f0 Получить новости", callback_data="latest")],
        [InlineKeyboardButton("\U0001f916 AI-новости", callback_data="ainews")],
        [InlineKeyboardButton("\U0001f4f1 Гаджеты", callback_data="gadgets")],
        [InlineKeyboardButton("\U0001f697 Авто", callback_data="cars")],
        [InlineKeyboardButton("\U0001f324 Погода", callback_data="weather")],
        [InlineKeyboardButton("\U0001f4b0 Курс валют", callback_data="rates")],
        [InlineKeyboardButton("\U0001f4ca Статистика", callback_data="stats")],
    ]

    await _reply(
        update,
        "\U0001f44b <b>Новости Екатеринбурга</b>\n\n"
        "Главные новости города каждый час!\n\n"
        "\U0001f4cc Источники:\n"
        "\U0001f534 URA.RU \u00b7 \U0001f7e1 E1.RU \u00b7 \U0001f535 66.RU\n"
        "\U0001f7e2 OBLTV.RU \u00b7 \U0001f7e3 JustMedia\n\n"
        "\U0001f916 AI выбирает по одной главной новости из каждого источника.\n\n"
        "/news — получить новости сейчас\n"
        "/ainews — AI-дайджест\n"
        "/gadgets — гаджеты\n"
        "/cars — авто\n"
        "/stats — статистика",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручной запрос новостей — работает всегда.
    Собирает свежие новости, потом выдаёт по одной самой свежей из каждого источника."""
    await _reply(update, "\U0001f504 Собираю новости...")
    await collect_news()
    batch = get_next_news_batch()

    if not batch:
        await _reply(update, "\U0001f4ed Пока нет новостей.")
        return

    await _send_news_to_chat(update, batch)
    mark_as_sent([n['id'] for n in batch])


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    sources = "\n".join(f"  \u2022 {s}: {c}" for s, c in stats['sources'].items())
    digest = "\n".join(f"  \u2022 {s}: {c}" for s, c in stats.get('digest_sources', {}).items())
    db_size = stats.get('db_size', 0)
    db_size_str = f"{db_size / (1024*1024):.1f} МБ" if db_size > 1024*1024 else f"{db_size / 1024:.1f} КБ"
    text = (
        f"\U0001f4ca <b>Статистика</b>\n\n"
        f"\U0001f4f0 Всего: {stats['total']}\n"
        f"\U0001f4ec Не отправлено: {stats['unsent']}\n"
        f"\U0001f465 Подписчиков: {stats['subscribers']}\n"
        f"\U0001f4c1 БД: {db_size_str}\n\n"
        f"<b>Источники:</b>\n{sources}\n\n"
        f"<b>Дайджесты (показано URL):</b>\n{digest}"
    )
    await _reply(update, text, parse_mode="HTML")


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню"""
    keyboard = [
        [InlineKeyboardButton("\U0001f4f0 Получить новости", callback_data="latest")],
        [InlineKeyboardButton("\U0001f916 AI-новости", callback_data="ainews")],
        [InlineKeyboardButton("\U0001f4f1 Гаджеты", callback_data="gadgets")],
        [InlineKeyboardButton("\U0001f697 Авто", callback_data="cars")],
        [InlineKeyboardButton("\U0001f324 Погода", callback_data="weather")],
        [InlineKeyboardButton("\U0001f4b0 Курс валют", callback_data="rates")],
        [InlineKeyboardButton("\U0001f4ca Статистика", callback_data="stats")],
    ]
    text = (
        "\U0001f4cb <b>Меню бота</b>\n\n"
        "\U0001f4cc <b>Источники:</b>\n"
        "\U0001f534 URA.RU \u00b7 \U0001f7e1 E1.RU \u00b7 \U0001f535 66.RU\n"
        "\U0001f7e2 OBLTV.RU \u00b7 \U0001f7e3 JustMedia\n\n"
        "\U0001f4cb <b>Команды:</b>\n"
        "/news — получить новости сейчас\n"
        "/ainews — AI-дайджест (5 источников)\n"
        "/gadgets — дайджест гаджетов\n"
        "/cars — дайджест авто (РФ)\n"
        "/stats — статистика"
    )
    await _reply(update, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_ainews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получить дайджест AI-новостей из 5 зарубежных источников"""
    await _reply(update, "🤖 Собираю свежие AI-новости...")

    try:
        news_list = await get_ai_news()
        news_list = _dedup_by_title(news_list)  # убираем дубликаты между источниками
        if not news_list:
            await _reply(update, "😕 Не удалось получить AI-новости. Попробуй позже.")
            return

        # Суммируем каждую новость через AI
        summaries = []
        for i, news in enumerate(news_list):
            try:
                summary = await _summarize_digest(news, "AI")
                summaries.append({
                    "title": news['title'],
                    "link": news['link'],
                    "summary": summary,
                    "icon": news.get('source_icon', '📡'),
                    "source": news['source'],
                })
            except Exception as e:
                logger.warning(f"AI news summary error #{i}: {e}")
                summaries.append({
                    "title": news['title'],
                    "link": news['link'],
                    "summary": news.get('description', '')[:200],
                    "icon": news.get('source_icon', '📡'),
                    "source": news['source'],
                })
            await asyncio.sleep(2)  # задержка между AI-запросами

        # Форматируем дайджест
        parts = ["<b>🤖 Дайджест AI-новостей</b>\n"]
        for i, s in enumerate(summaries, 1):
            link = s['link']
            title = s['title'][:120]
            summary = s['summary'][:500]
            text = (
                f"<b>{i}. {s['icon']} {s['source']}</b>\n"
                f"<a href='{link}'>{title}</a>\n"
                f"<i>{summary}</i>\n"
            )
            parts.append(text)

        full_text = '\n'.join(parts)

        # Telegram лимит 4096 символов — режем если нужно
        if len(full_text) > 4000:
            full_text = full_text[:3997] + "..."

        await _reply(update, full_text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"cmd_ainews error: {e}", exc_info=True)
        await _reply(update, "😵 Ошибка при получении AI-новостей. Попробуй позже.")

async def cmd_gadgets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получить дайджест гаджетов из 5 зарубежных источников"""
    await _reply(update, "\U0001f4f1 Собираю свежие новости о гаджетах...")

    try:
        news_list = await get_gadgets_news()
        news_list = _dedup_by_title(news_list)
        if not news_list:
            await _reply(update, "\U0001f615 Не удалось получить новости о гаджетах. Попробуй позже.")
            return

        summaries = []
        for i, news in enumerate(news_list):
            try:
                summary = await _summarize_digest(news, "гаджетов")
                summaries.append({
                    "title": news['title'], "link": news['link'], "summary": summary,
                    "icon": news.get('source_icon', '\U0001f4f1'), "source": news['source'],
                })
            except Exception as e:
                logger.warning(f"Gadgets summary error #{i}: {e}")
                summaries.append({
                    "title": news['title'], "link": news['link'],
                    "summary": news.get('description', '')[:200],
                    "icon": news.get('source_icon', '\U0001f4f1'), "source": news['source'],
                })
            await asyncio.sleep(2)

        parts = ["<b>\U0001f4f1 Дайджест гаджетов</b>\n"]
        for i, s in enumerate(summaries, 1):
            link = s['link']; title = s['title'][:120]; summary = s['summary'][:500]
            parts.append(f"<b>{i}. {s['icon']} {s['source']}</b>\n<a href='{link}'>{title}</a>\n<i>{summary}</i>\n")

        full_text = '\n'.join(parts)
        if len(full_text) > 4000: full_text = full_text[:3997] + "..."
        await _reply(update, full_text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"cmd_gadgets error: {e}", exc_info=True)
        await _reply(update, "\U0001f635 Ошибка при получении новостей о гаджетах.")


async def cmd_cars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получить дайджест автомобильных новостей из 5 российских источников"""
    await _reply(update, "\U0001f697 Собираю свежие авто-новости...")

    try:
        news_list = await get_cars_news()
        news_list = _dedup_by_title(news_list)
        if not news_list:
            await _reply(update, "\U0001f615 Не удалось получить авто-новости. Попробуй позже.")
            return

        summaries = []
        for i, news in enumerate(news_list):
            try:
                summary = await _summarize_digest(news, "автомобилей")
                summaries.append({
                    "title": news['title'], "link": news['link'], "summary": summary,
                    "icon": news.get('source_icon', '\U0001f697'), "source": news['source'],
                })
            except Exception as e:
                logger.warning(f"Cars summary error #{i}: {e}")
                summaries.append({
                    "title": news['title'], "link": news['link'],
                    "summary": news.get('description', '')[:200],
                    "icon": news.get('source_icon', '\U0001f697'), "source": news['source'],
                })
            await asyncio.sleep(2)

        parts = ["<b>\U0001f697 Дайджест авто-новостей</b>\n"]
        for i, s in enumerate(summaries, 1):
            link = s['link']; title = s['title'][:120]; summary = s['summary'][:500]
            parts.append(f"<b>{i}. {s['icon']} {s['source']}</b>\n<a href='{link}'>{title}</a>\n<i>{summary}</i>\n")

        full_text = '\n'.join(parts)
        if len(full_text) > 4000: full_text = full_text[:3997] + "..."
        await _reply(update, full_text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"cmd_cars error: {e}", exc_info=True)
        await _reply(update, "\U0001f635 Ошибка при получении авто-новостей.")




async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получить текущую погоду и прогноз на завтра"""
    await _reply(update, "\U0001f324 Получаю погоду...")
    try:
        weather = await asyncio.to_thread(get_weather)
        text = format_weather(weather)
        await _reply(update, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_weather error: {e}", exc_info=True)
        await _reply(update, "\U0001f635 Ошибка при получении погоды.")


async def cmd_rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получить официальный курс ЦБ"""
    await _reply(update, "\U0001f4b0 Получаю курс валют...")
    try:
        rates = await asyncio.to_thread(get_exchange_rates)
        text = format_rates(rates)
        await _reply(update, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_rates error: {e}", exc_info=True)
        await _reply(update, "\U0001f635 Ошибка при получении курса валют.")



async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deactivate_subscriber(update.effective_chat.id)
    await _reply(update, "\U0001f622 Вы отписаны. /start — подписаться снова.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "latest":
        await cmd_news(update, context)
    elif query.data == "stats":
        await cmd_stats(update, context)
    elif query.data == "ainews":
        await cmd_ainews(update, context)
    elif query.data == "gadgets":
        await cmd_gadgets(update, context)
    elif query.data == "cars":
        await cmd_cars(update, context)
    elif query.data == "weather":
        await cmd_weather(update, context)
    elif query.data == "rates":
        await cmd_rates(update, context)
    elif query.data == "unsubscribe":
        deactivate_subscriber(update.effective_chat.id)
        await query.edit_message_text("\U0001f622 Вы отписаны. /start — подписаться.")


import random
from datetime import timedelta


async def _send_to_channel(text: str, image_url: str = ''):
    """Отправить сообщение в канал. Если есть рабочая картинка — photo, иначе text."""
    chat_id = f"@{CHANNEL_USERNAME}"
    try:
        if image_url and _is_valid_image_url(image_url):
            img_data = _download_image(image_url)
            if img_data:
                await _app.bot.send_photo(
                    chat_id=chat_id,
                    photo=io.BytesIO(img_data),
                    caption=text[:1024],
                    parse_mode="HTML",
                )
                return
        await _app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Channel send error: {e}")


async def _publish_news():
    """Опубликовать одну новость в канал."""
    with get_db() as conn:
        news_row = conn.execute(
            """SELECT * FROM news WHERE is_sent = 0
               ORDER BY COALESCE(published_at, created_at) DESC LIMIT 1"""
        ).fetchone()

    if not news_row:
        logger.info("_publish_news: no unsent news")
        return

    news = dict(news_row)

    # Добавляем проверку на публикацию URL
    if is_url_published(news['link']):
        logger.info(f"_publish_news: пропуск дубля {news['link']}")
        mark_as_sent([news['id']])
        return

    try:
        ai_desc = await generate_summary(news['title'])
    except Exception:
        ai_desc = news.get('description', '') or ''
        if len(ai_desc) > 500:
            ai_desc = ai_desc[:500]

    text = _format_news_text_with_desc(news, ai_desc)
    await _send_to_channel(text, news.get('image_url', ''))
    mark_as_sent([news['id']])
    mark_url_published(news['link'])
    logger.info(f"_publish_news: [{news['source']}] {news['title'][:50]}...")


def _format_news_text_with_desc(news: dict, desc: str) -> str:
    """Форматировать текст новости для канала с AI-описанием."""
    source_emoji = {
        "URA.RU": "🔴", "E1.RU": "🟡", "66.RU": "🔵",
        "OBLTV.RU": "🟢", "JustMedia": "🟣",
        "Lenta.ru": "🟠", "Life.ru": "🟤",
        "Коммерсантъ": "⚪️", "Ведомости": "🟧", "UralWeb": "⚫️",
    }.get(news['source'], "📡")
    return (
        f"{source_emoji} <b>{news['title']}</b>\n\n"
        f"{desc[:800]}\n\n"
        f"📌 <a href=\"{news['link']}\">Читать полностью</a>"
    )


async def _publish_digest(digest_type: str):
    """Опубликовать дайджест в канал."""
    info = DIGEST_TYPES[digest_type]
    await _send_to_channel(f"{info['emoji']} Собираю {info['name'].lower()}...")

    try:
        if digest_type == "ainews":
            news_list = await get_ai_news()
        elif digest_type == "gadgets":
            news_list = await get_gadgets_news()
        elif digest_type == "cars":
            news_list = await get_cars_news()
        else:
            return

        news_list = _dedup_by_title(news_list)
        # Дедупликация по URL для дайджестов
        final_news = []
        for news in news_list:
            if not is_url_published(news['link']):
                final_news.append(news)
                mark_url_published(news['link'])
        news_list = final_news

        if not news_list:
            await _send_to_channel(f"😕 Нет новых {info['name'].lower()}.")
            return

        summaries = []
        for i, news in enumerate(news_list):
            try:
                summary = await _summarize_digest(news, info['name'])
            except Exception:
                summary = news.get('description', '')[:200]
            summaries.append({
                "title": news['title'], "link": news['link'],
                "summary": summary, "icon": news.get('source_icon', '📡'),
                "source": news['source'],
            })
            await asyncio.sleep(2)

        parts = [f"<b>{info['emoji']} {info['name']}</b>\n"]
        for i, s in enumerate(summaries, 1):
            text = f"<b>{i}. {s['icon']} {s['source']}</b>\n<a href=\"{s['link']}\">{s['title'][:120]}</a>\n<i>{s['summary'][:500]}</i>\n"
            parts.append(text)

        full_text = '\n'.join(parts)
        if len(full_text) > 4000:
            full_text = full_text[:3997] + "..."
        await _send_to_channel(full_text)
        logger.info(f"_publish_digest: {digest_type} ({len(summaries)} news)")
    except Exception as e:
        logger.error(f"_publish_digest error ({digest_type}): {e}", exc_info=True)
        await _send_to_channel(f"😵 Ошибка при получении {info['name'].lower()}.")


async def _publish_weather():
    """Опубликовать дайджест погоды в канал."""
    await _send_to_channel("🌤️ Получаю погоду...")
    try:
        weather = await asyncio.to_thread(get_weather)
        text = format_weather(weather)
        await _send_to_channel(text)
        logger.info("_publish_weather: sent")
    except Exception as e:
        logger.error(f"_publish_weather error: {e}", exc_info=True)
        await _send_to_channel("😵 Ошибка при получении погоды.")


# Трекер последнего опубликованного дайджеста
_last_digest_date = None
_last_digest_type = None



# === Main ===

def main():
    global _app

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN не задан!")
        sys.exit(1)

    init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    _app = app

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("ainews", cmd_ainews))
    app.add_handler(CommandHandler("gadgets", cmd_gadgets))
    app.add_handler(CommandHandler("cars", cmd_cars))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("rates", cmd_rates))
    app.add_handler(CallbackQueryHandler(button_callback))

    commands = [
        BotCommand("Запустить", "start"),
        BotCommand("Получить новости", "news"),
        BotCommand("AI-новости", "ainews"),
        BotCommand("Гаджеты", "gadgets"),
        BotCommand("Авто", "cars"),
        BotCommand("Погода", "weather"),
        BotCommand("Курс валют", "rates"),
        BotCommand("Статистика", "stats"),
    ]

    async def post_init(application):
        # set_my_commands отключен — команды работают и без меню
        logger.info("Пост-инициализация завершена")
        asyncio.ensure_future(_news_loop())

    app.post_init = post_init

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

