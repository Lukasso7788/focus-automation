from flask import Flask, request, jsonify
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os
import json
import logging
import csv
from logging.handlers import RotatingFileHandler

# === FLASK ===
app = Flask(__name__)

# === LOGGING ===
handler = RotatingFileHandler("log.txt", maxBytes=5 * 1024 * 1024, backupCount=3)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
app.logger.addHandler(handler)

# === CONFIG ===
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SHEET_ID = os.getenv("SHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "focus-automation-credentials.json")

# === GOOGLE SHEETS SETUP ===
scope = ["https://www.googleapis.com/auth/spreadsheets"]
try:
    if GOOGLE_CREDENTIALS_JSON:
        creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
except Exception as e:
    app.logger.error(f"❌ Google Sheets setup error: {e}")
    sheet = None

# === CSV LOGGING ===
def log_to_csv(user, event, ip_address, user_agent):
    """Append events to CSV with rotation if size > 1MB"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = "events_log.csv"

    # rotate if >1MB
    if os.path.exists(log_file) and os.path.getsize(log_file) > 1 * 1024 * 1024:
        os.rename(log_file, f"events_log_{timestamp.replace(':', '-')}.bak.csv")

    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, user, event, ip_address, user_agent])

    app.logger.info(f"🗒 CSV: {timestamp} | {user} | {event} | {ip_address} | {user_agent}")

# === HELPERS ===
def send_slack_message(text):
    if not SLACK_WEBHOOK_URL:
        app.logger.warning("⚠️ SLACK_WEBHOOK_URL not configured")
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text})
    except Exception as e:
        app.logger.error(f"Slack send error: {e}")

def send_discord_message(text):
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        app.logger.warning("⚠️ DISCORD_WEBHOOK_URL not configured")
        return
    try:
        requests.post(url, json={"content": text})
    except Exception as e:
        app.logger.error(f"Discord send error: {e}")

def send_telegram_message(text):
    """Отправка сообщения в Telegram с полным логом ответа"""
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        app.logger.warning("⚠️ Telegram not configured (missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID)")
        return {"ok": False, "error": "not_configured"}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    try:
        r = requests.post(url, json=payload, timeout=10)
        try:
            body = r.json()
        except Exception:
            body = r.text
        app.logger.info(f"📤 Telegram HTTP {r.status_code}: {body}")
        r.raise_for_status()
        return {"ok": True, "status": r.status_code, "body": body}
    except Exception as e:
        app.logger.error(f"Telegram send error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

# === ROUTES ===
@app.route("/webhook", methods=["POST"])
def webhook():
    """Основной эндпоинт — записывает данные в Sheets, CSV и Slack"""
    try:
        data = request.get_json(force=True)
        user = data.get("user")
        event = data.get("event")
        ip_address = request.remote_addr
        user_agent = request.headers.get("User-Agent", "unknown")

        if not user or not event:
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if sheet:
            sheet.append_row([timestamp, user, event, ip_address, user_agent])

        log_to_csv(user, event, ip_address, user_agent)
        send_slack_message(f"✅ {user} triggered: {event} ({timestamp})")
        send_discord_message(f"📢 {user}: {event} ({timestamp})")

        return jsonify({"status": "success"}), 200
    except Exception as e:
        app.logger.error(f"Webhook error: {e}", exc_info=True)
        send_slack_message(f"⚠️ Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/telegram", methods=["POST"])
def telegram():
    """Приём JSON и отправка сообщения в Telegram"""
    try:
        data = request.get_json(force=True)
        user = data.get("user")
        message = data.get("message", "")
        ip_address = request.remote_addr
        user_agent = request.headers.get("User-Agent", "unknown")

        result = send_telegram_message(f"📩 {user}: {message}")
        log_to_csv(user, message, ip_address, user_agent)
        return jsonify({"status": "sent", "telegram_result": result}), 200
    except Exception as e:
        app.logger.error(f"Telegram endpoint error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/debug/telegram", methods=["POST"])
def debug_telegram():
    """Проверка Telegram-связи и ENV"""
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    masked_token = (token[:10] + "…") if token else None
    result = send_telegram_message("🔧 Debug ping from Flask app (Render)")
    return jsonify({
        "env_seen_by_app": {
            "TELEGRAM_TOKEN_prefix": masked_token,
            "TELEGRAM_CHAT_ID": chat_id
        },
        "send_result": result
    }), (200 if result.get("ok") else 500)

@app.route("/health", methods=["GET"])
def health():
    """Общая проверка сервиса"""
    token = os.getenv("TELEGRAM_TOKEN")
    return jsonify({
        "status": "ok",
        "slack": bool(SLACK_WEBHOOK_URL),
        "telegram": bool(token),
        "sheets": bool(sheet)
    }), 200

# === DEBUG PRINT ===
print("🔍 TELEGRAM_TOKEN (first 10 chars):", str(os.getenv("TELEGRAM_TOKEN"))[:10])
print("🔍 TELEGRAM_CHAT_ID:", os.getenv("TELEGRAM_CHAT_ID"))

# === MAIN ===
if __name__ == "__main__":
    print("✅ Sheets and Slack connected. Server starting...")
    app.run(port=5000, debug=True)
