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

# === CONFIG (read from environment) ===
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SHEET_ID = os.getenv("SHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "focus-automation-credentials.json")

# === GOOGLE SHEETS SETUP ===
scope = ["https://www.googleapis.com/auth/spreadsheets"]

if GOOGLE_CREDENTIALS_JSON:
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
else:
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)

client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)

# === CSV LOGGING FUNCTION (NEW / IMPROVED) ===
def log_to_csv(user, event, ip_address, user_agent):
    """Append events to CSV with rotation if size > 1MB"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = "events_log.csv"

    # Rotate old CSV manually if > 1 MB
    if os.path.exists(log_file) and os.path.getsize(log_file) > 1 * 1024 * 1024:
        os.rename(log_file, f"events_log_{timestamp.replace(':', '-')}.bak.csv")

    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, user, event, ip_address, user_agent])

    app.logger.info(f"🗒 CSV log: {user}, {event}, {ip_address}, {user_agent}, {timestamp}")

# === ROUTES ===
@app.route("/webhook", methods=["POST"])
def webhook():
    """Receive JSON event → save to Google Sheets + Slack + Discord"""
    try:
        data = request.get_json(force=True)
        app.logger.info(f"Received event from user={data.get('user')} event={data.get('event')}")
        print("✅ JSON received:", data)

        user = data.get("user")
        event = data.get("event")
        ip_address = request.remote_addr
        user_agent = request.headers.get("User-Agent", "unknown")

        if not user or not event:
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        # Write to Google Sheets
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([timestamp, user, event, ip_address, user_agent])
        log_to_csv(user, event, ip_address, user_agent)  # ⬅️ added here

        # Notify Slack + Discord
        send_slack_message(f"✅ {user} triggered: {event} ({timestamp})")
        send_discord_message(f"📢 {user}: {event} ({timestamp})")

        print("📗 Event saved and sent to Slack.")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        app.logger.error(f"Error processing webhook: {e}", exc_info=True)
        send_slack_message(f"⚠️ Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# === SENDERS ===
def send_slack_message(text):
    if not SLACK_WEBHOOK_URL:
        app.logger.warning("⚠️ Slack webhook URL not set (SLACK_WEBHOOK_URL)")
        return
    payload = {"text": text}
    response = requests.post(SLACK_WEBHOOK_URL, json=payload)
    if response.status_code != 200:
        app.logger.warning(f"Slack error {response.status_code}: {response.text}")

def send_discord_message(text):
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        app.logger.warning("⚠️ Discord webhook not configured")
        return
    payload = {"content": text}
    requests.post(url, json=payload)

def send_telegram_message(text):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        app.logger.warning("⚠️ Telegram not configured")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    response = requests.post(url, json=payload)
    app.logger.info(f"Telegram response: {response.status_code} {response.text}")

# === TELEGRAM ENDPOINT ===
@app.route("/telegram", methods=["POST"])
def telegram():
    try:
        data = request.get_json(force=True)
        user = data.get("user")
        message = data.get("message", "")
        send_telegram_message(f"📩 {user}: {message}")
        log_to_csv(user, message)  # ⬅️ added here
        return jsonify({"status": "sent"}), 200
    except Exception as e:
        app.logger.error(f"Telegram endpoint error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# === HEALTH CHECK ===
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

# === MAIN ===
if __name__ == "__main__":
    print("✅ Sheets and Slack connected. Server starting...")
    app.run(port=5000, debug=True)
