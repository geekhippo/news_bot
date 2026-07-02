#!/bin/bash
# Установка и запуск Новостного бота Екатеринбурга

set -e

echo "📰 Установка Новостного бота Екатеринбурга..."

# === Docker-установка (рекомендуется) ===
if command -v docker &> /dev/null && command -v docker compose &> /dev/null; then
    echo "🐳 Docker найден."
    echo ""

    # Проверка .env
    if [ ! -f ".env" ]; then
        echo "⚠️  Файл .env не найден!"
        echo "Создайте .env на основе .env.example и добавьте TELEGRAM_TOKEN"
        cp .env.example .env
        echo "📝 Отредактируйте файл .env и добавьте токен бота"
        exit 1
    fi

    echo "🔨 Сборка образа..."
    docker compose build
    echo ""
    echo "🚀 Запуск контейнера..."
    docker compose up -d
    echo ""
    echo "✅ Установка завершена!"
    echo "Проверить логи: docker compose logs -f --tail 20"
    exit 0
fi

# === Локальная установка (без Docker) ===
echo "⚠️  Docker не найден, устанавливаю локально..."

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не найден. Установите Python 3.10+"
    exit 1
fi

# Создание виртуального окружения
if [ ! -d "venv" ]; then
    echo "📦 Создание виртуального окружения..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "📦 Установка зависимостей..."
pip install -r requirements.txt

if [ ! -f ".env" ]; then
    echo "⚠️  Файл .env не найден!"
    cp .env.example .env
    echo "📝 Отредактируйте .env и добавьте токен бота"
    exit 1
fi

echo "✅ Установка завершена!"
echo ""
echo "Для запуска:"
echo "  python bot.py"
echo ""
echo "Или в фоне:"
echo "  nohup python bot.py > bot.log 2>&1 &"
