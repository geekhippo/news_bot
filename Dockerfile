FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV CHECK_INTERVAL="60"

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s \
    CMD python -c "import sqlite3; sqlite3.connect('/app/data/news.db').execute('SELECT 1')" || exit 1

CMD ["python", "bot.py"]
