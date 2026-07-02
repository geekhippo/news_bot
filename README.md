# 📰 Новостной бот Екатеринбурга

Telegram-бот, который собирает главные новости Екатеринбурга из **12 источников**, публикует их в канале **@ekbsummary**, а также отвечает на команды пользователей.

## 🔗 Бот и канал

- **@ekb_bot** — основной бот (команды, рассылка подписчикам)
- **@ekbsummary** — канал с автопубликацией новостей, погоды и дайджестов

## 📡 Источники (12)

### Локальные (Екатеринбург)
| Источник | Эмодзи | Тип |
|----------|--------|-----|
| [URA.RU](https://ura.news) | 🔴 | RSS |
| [E1.RU](https://www.e1.ru) | 🟡 | HTML |
| [66.RU](https://www.66.ru) | 🔵 | HTML |
| [OBLTV.RU](https://obltv.ru) | 🟢 | HTML |
| [JustMedia](https://justmedia.ru) | 🟣 | HTML |

### Федеральные
| Источник | Эмодзи | Тип |
|----------|--------|-----|
| [Lenta.ru](https://lenta.ru) | 🟠 | RSS |
| [Life.ru](https://life.ru) | 🟤 | RSS |
| [Коммерсантъ](https://www.kommersant.ru) | ⚪️ | RSS |
| [Ведомости](https://www.vedomosti.ru) | 🟧 | RSS |
| [UralWeb](https://uralweb.ru) | ⚫️ | RSS |
| [itsmycity](https://itsmycity.ru) | 📰 | RSS |
| [Областная газета](https://oblgazeta.ru) | 📰 | RSS |

## 🧠 Как работает

### Автопубликация в канал (@ekbsummary)
Запускается как отдельный процесс (`channel-publisher`):
- **Новости**: по одной каждые 4-12 минут (случайный интервал), без повторов
- **Погода**: каждый день в 10:00 (текущая + прогноз на завтра, предупреждения)
- **Дайджесты**: 1 раз в день в случайное время 11:00-21:00 (AI, гаджеты, авто)
- **Курс валют**: по запросу `/rates` (ЦБ: USD, EUR, BYN, CNY)

### Команды бота (@ekb_bot)
- `/start` — подписаться на рассылку
- `/news` — получить 5 свежих новостей сейчас
- `/ainews` — дайджест AI-новостей (5 зарубежных источников)
- `/gadgets` — дайджест гаджетов (5 зарубежных источников)
- `/cars` — дайджест авто-новостей (5 российских источников)
- `/weather` — погода в Екатеринбурге
- `/rates` — курс ЦБ (USD, EUR, BYN, CNY)
- `/stats` — статистика (новости, источники, подписчики, размер БД)
- `/menu` — меню с кнопками
- `/stop` — отписаться

### Два процесса
Бот запускается в двух Docker-контейнерах:
1. **ekaterinburg-news-bot** — основной бот (команды, сбор новостей каждый час)
2. **ekaterinburg-channel-publisher** — публикация в канал (новости, погода, дайджесты)

Разделение необходимо, потому что `run_polling()` блокирует event loop и не даёт APScheduler работать в том же процессе.

## 🚀 Быстрый старт (Docker)

```bash
# 1. Клонировать репозиторий
git clone https://github.com/geekhippo/news_bot.git
cd news_bot

# 2. Создать .env с токенами
cp .env.example .env
# Отредактировать .env — вставить TELEGRAM_TOKEN и OPENROUTER_API_KEY

# 3. Запустить оба контейнера
docker compose up -d
```

### Переменные окружения (.env)

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `TELEGRAM_TOKEN` | ✅ | Токен бота от @BotFather |
| `OPENROUTER_API_KEY` | ❌ | Ключ OpenRouter (для AI-описаний) |
| `OPENROUTER_MODEL` | ❌ | Модель OpenRouter (по ум. `google/gemini-3.1-flash-lite-preview`) |
| `CHANNEL_USERNAME` | ❌ | Имя канала (по ум. `ekbsummary`) |

## 🐳 Команды Docker

```bash
docker compose logs -f                    # Логи обоих контейнеров
docker compose logs -f news-bot           # Логи только бота
docker compose logs -f channel-publisher   # Логи только канала
docker compose restart                    # Перезапуск
docker compose down                       # Остановка
docker compose pull && docker compose up -d  # Обновить
```

## 📁 Структура проекта

```
├── bot.py                 # Основной бот (команды, сбор новостей)
├── channel_publisher.py   # Публикация в канал (отдельный процесс)
├── parser.py              # Парсеры 12 новостных источников
├── database.py            # SQLite база данных
├── ai.py                  # AI-описания через OpenRouter
├── services.py            # Погода и курс валют
├── ai_news_parser.py      # 5 AI-источников (The Verge, Ars Technica, TechCrunch, Wired, MIT)
├── gadgets_parser.py      # 5 источников гаджетов
├── cars_parser.py         # 5 российских авто-источников
├── docker-compose.yml     # Два контейнера: бот + канал
├── requirements.txt
├── .env.example
└── README.md
```

## 📄 Лицензия

MIT
