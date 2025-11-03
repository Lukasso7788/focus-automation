import sys
# Windows console UTF-8 (чтобы не было UnicodeEncodeError в PowerShell/ConEmu)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

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

# === LOGGING (log.txt rotation up to 5MB) ===
handler = RotatingFileHandler("log.txt", maxBytes=5 * 1024 * 1024, backupCount=3)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
app.logger.addHandler(handler)

# === CONFIG (from environment) ===
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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
    app.logger.error(f"Google Sheets setup error: {e}", exc_info=True)
    sheet = None

# === UTILS ===
def client_ip(req) -> str:
    # На Render/за прокси реальный IP приходит в X-Forwarded-For
    xff = req.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return req.remote_addr

def log_to_csv(user, event, ip_address, user_agent):
    """Append events to CSV with простая ротация > 1MB"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = "events_log.csv"
    try:
        if os.path.exists(log_file) and os.path.getsize(log_file) > 1 * 1024 * 1024:
            os.rename(log_file, f"events_log_{timestamp.replace(':', '-')}.bak.csv")
    except Exception as e:
        app.logger.warning(f"CSV rotation warn: {e}")
    try:
        with open(log_file, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([timestamp, user, event, ip_address, user_agent])
        app.logger.info(f"CSV: {timestamp} | {user} | {event} | {ip_address} | {user_agent}")
    except Exception as e:
        app.logger.error(f"CSV write error: {e}", exc_info=True)

# === SENDERS ===
def send_slack_message(text: str):
    if not SLACK_WEBHOOK_URL:
        app.logger.warning("SLACK_WEBHOOK_URL not configured")
        return {"ok": False, "error": "not_configured"}
    try:
        r = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
        return {"ok": r.ok, "status": r.status_code}
    except Exception as e:
        app.logger.error(f"Slack send error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

def send_discord_message(text: str):
    if not DISCORD_WEBHOOK_URL:
        app.logger.warning("DISCORD_WEBHOOK_URL not configured")
        return {"ok": False, "error": "not_configured"}
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": text}, timeout=10)
        return {"ok": r.ok, "status": r.status_code}
    except Exception as e:
        app.logger.error(f"Discord send error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

def send_telegram_message(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        app.logger.warning("Telegram not configured (TELEGRAM_TOKEN or TELEGRAM_CHAT_ID missing)")
        return {"ok": False, "error": "not_configured"}
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        # Telegram часто шлёт JSON-ответ; логируем коротко
        _ = r.json() if r.headers.get("content-type","").startswith("application/json") else r.text
        r.raise_for_status()
        return {"ok": True, "status": r.status_code}
    except Exception as e:
        app.logger.error(f"Telegram send error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

# === ROUTES ===
@app.route("/webhook", methods=["POST"])
def webhook():
    """Main webhook — Sheets + CSV + Slack/Discord/Telegram"""
    try:
        data = request.get_json(force=True)
        user = data.get("user")
        event = data.get("event")
        ip_address = client_ip(request)
        user_agent = request.headers.get("User-Agent", "unknown")

        if not user or not event:
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Google Sheets (best-effort)
        if sheet:
            try:
                sheet.append_row([timestamp, user, event, ip_address, user_agent])
            except Exception as e:
                app.logger.error(f"Sheets append error: {e}", exc_info=True)

        # CSV
        log_to_csv(user, event, ip_address, user_agent)

        # Unified message to channels
        msg = f"{user} triggered: {event} at {timestamp}"

        slack_res = send_slack_message(msg)
        disc_res  = send_discord_message(msg)
        tg_res    = send_telegram_message(msg)

        return jsonify({
            "status": "success",
            "slack": slack_res,
            "discord": disc_res,
            "telegram": tg_res
        }), 200

    except Exception as e:
        app.logger.error(f"Webhook error: {e}", exc_info=True)
        # попытка уведомить Slack об ошибке (если настроен)
        send_slack_message(f"Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    """Service health check"""
    return jsonify({
        "status": "ok",
        "slack": bool(SLACK_WEBHOOK_URL),
        "discord": bool(DISCORD_WEBHOOK_URL),
        "telegram": bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID),
        "sheets": bool(sheet)
    }), 200

@app.route("/debug/env", methods=["GET"])
def debug_env():
    def mask(v):
        return (v[:10] + "…") if v else None

    return jsonify({
        "TELEGRAM_TOKEN_prefix": mask(os.getenv("TELEGRAM_TOKEN")),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID"),
        "SLACK_WEBHOOK_URL_set": bool(os.getenv("SLACK_WEBHOOK_URL")),
        "DISCORD_WEBHOOK_URL_set": bool(os.getenv("DISCORD_WEBHOOK_URL")),
        "SHEET_ID_set": bool(os.getenv("SHEET_ID")),
        "GOOGLE_CREDENTIALS_JSON_loaded": bool(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    }), 200


# === MAIN ===
if __name__ == "__main__":
    print("Sheets/Slack/Discord/Telegram pipeline starting...")
    app.run(port=5000, debug=True)
