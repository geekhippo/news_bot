"""
Внешние сервисы: погода и курс валют.
"""
import json
import logging
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

CITY = "Yekaterinburg"


def get_weather() -> dict:
    """Получить текущую погоду и прогноз на завтра для Екатеринбурга.
    Возвращает словарь с current, tomorrow, alerts."""
    url = f"https://wttr.in/{CITY}?format=j1"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.error(f"Weather fetch error: {e}")
        return {}

    result = {"current": None, "tomorrow": None, "alerts": []}

    # Текущая погода
    try:
        cur = data["current_condition"][0]
        result["current"] = {
            "temp": cur["temp_C"],
            "feels_like": cur["FeelsLikeC"],
            "desc": cur["weatherDesc"][0]["value"].strip(),
            "humidity": cur["humidity"],
            "wind": cur["windspeedKmph"],
            "pressure": cur["pressure"],
            "uv": cur["uvIndex"],
            "visibility": cur["visibility"],
            "cloud": cur["cloudcover"],
        }
    except (KeyError, IndexError) as e:
        logger.error(f"Weather parse current error: {e}")

    # Прогноз на завтра
    try:
        tomorrow = data["weather"][1]
        hourly = tomorrow.get("hourly", [])
        # Берём данные на 12:00 (индекс 4) как дневные
        noon = hourly[4] if len(hourly) > 4 else hourly[0] if hourly else {}
        result["tomorrow"] = {
            "date": tomorrow["date"],
            "max_temp": tomorrow["maxtempC"],
            "min_temp": tomorrow["mintempC"],
            "desc": noon.get("weatherDesc", [{}])[0].get("value", "").strip(),
            "rain_chance": noon.get("chanceofrain", "0"),
            "humidity": noon.get("humidity", ""),
            "wind": noon.get("windspeedKmph", ""),
        }
    except (KeyError, IndexError) as e:
        logger.error(f"Weather parse tomorrow error: {e}")

    # Проверка опасных явлений
    try:
        for hour in data["weather"][0].get("hourly", []):
            rain = int(hour.get("chanceofrain", "0"))
            wind = int(hour.get("windspeedKmph", "0"))
            desc = hour.get("weatherDesc", [{}])[0].get("value", "").lower()
            time_h = int(hour.get("time", "0")) // 100

            if rain > 70:
                result["alerts"].append(f"🌧️ Дождь {rain}% в {time_h}:00")
            if wind > 40:
                result["alerts"].append(f"💨 Ветер {wind} км/ч в {time_h}:00")
            if "snow" in desc or "снег" in desc:
                result["alerts"].append(f"❄️ Снег в {time_h}:00")
            if "thunder" in desc or "гроза" in desc:
                result["alerts"].append(f"⛈️ Гроза в {time_h}:00")
            if "fog" in desc or "туман" in desc:
                result["alerts"].append(f"🌫️ Туман в {time_h}:00")

        # Проверяем и завтра
        if result["tomorrow"]:
            rain_t = int(result["tomorrow"].get("rain_chance", "0"))
            if rain_t > 70:
                result["alerts"].append(f"🌧️ Завтра дождь {rain_t}%")
    except Exception as e:
        logger.debug(f"Weather alerts check error: {e}")

    return result


def get_exchange_rates() -> dict:
    """Получить официальный курс ЦБ на текущую дату для USD, EUR, BYN, CNY.
    Возвращает словарь с курсами."""
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.error(f"Exchange rates fetch error: {e}")
        return {}

    rates = {}
    targets = {
        "USD": "Доллар США",
        "EUR": "Евро",
        "BYN": "Белорусский рубль",
        "CNY": "Китайский юань",
    }

    valute = data.get("Valute", {})
    for code, name in targets.items():
        v = valute.get(code)
        if v:
            rates[code] = {
                "name": name,
                "value": v["Value"],
                "previous": v.get("Previous"),
                "nominal": v.get("Nominal", 1),
            }

    return rates


def format_weather(weather: dict) -> str:
    """Форматировать погоду в текст для Telegram."""
    if not weather or not weather.get("current"):
        return "😕 Не удалось получить погоду."

    cur = weather["current"]
    lines = ["🌤️ <b>Погода в Екатеринбурге</b>\n"]

    # Текущая
    lines.append(f"<b>Сейчас:</b> {cur['temp']}°C (ощущается {cur['feels_like']}°C)")
    lines.append(f"  {cur['desc']}")
    lines.append(f"  💧 Влажность: {cur['humidity']}%")
    lines.append(f"  💨 Ветер: {cur['wind']} км/ч")
    lines.append(f"  🔵 Давление: {cur['pressure']} мм рт.ст.")
    if int(cur.get("uv", 0)) >= 6:
        lines.append(f"  ☀️ UV-индекс: {cur['uv']} (высокий)")

    # Завтра
    t = weather.get("tomorrow")
    if t:
        lines.append(f"\n<b>Завтра ({t['date']}):</b>")
        lines.append(f"  {t['min_temp']}°C...{t['max_temp']}°C, {t['desc']}")
        if int(t.get("rain_chance", 0)) > 30:
            lines.append(f"  🌧️ Вероятность дождя: {t['rain_chance']}%")

    # Опасности
    alerts = weather.get("alerts", [])
    if alerts:
        lines.append(f"\n⚠️ <b>Внимание:</b>")
        for a in alerts:
            lines.append(f"  {a}")

    return "\n".join(lines)


def format_rates(rates: dict) -> str:
    """Форматировать курс валют в текст для Telegram."""
    if not rates:
        return "😕 Не удалось получить курс валют."

    lines = ["💱 <b>Курс ЦБ</b>\n"]

    icons = {"USD": "🇺🇸", "EUR": "🇪🇺", "BYN": "🇧🇾", "CNY": "🇨🇳"}
    for code in ["USD", "EUR", "BYN", "CNY"]:
        r = rates.get(code)
        if r:
            icon = icons.get(code, "💰")
            nominal = r.get("nominal", 1)
            value = r["value"]
            prev = r.get("previous")

            # Сравнение с предыдущим
            diff_str = ""
            if prev:
                diff = value - prev
                if diff > 0:
                    diff_str = f" 📈 +{diff:.2f}"
                elif diff < 0:
                    diff_str = f" 📉 {diff:.2f}"

            if nominal == 1:
                lines.append(f"{icon} {r['name']}: <b>{value:.2f}</b> ₽{diff_str}")
            else:
                lines.append(f"{icon} {r['name']} ({nominal} шт): <b>{value:.2f}</b> ₽{diff_str}")

    return "\n".join(lines)
