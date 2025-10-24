from flask import Flask, request, jsonify
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os
import json
import logging
from logging.handlers import RotatingFileHandler

# === FLASK ===
app = Flask(__name__)

# === ЛОГИРОВАНИЕ ===
handler = RotatingFileHandler("log.txt", maxBytes=5*1024*1024, backupCount=3)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
app.logger.addHandler(handler)

# === CONFIG ===
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")               # Slack webhook — из переменной окружения
SHEET_ID = os.getenv("SHEET_ID")                                 # ID Google Sheets — из окружения
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")                   # имя листа (по умолчанию Sheet1)
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")   # JSON ключи сервисного аккаунта
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "focus-automation-credentials.json")

# === SETUP GOOGLE SHEETS ===
scope = ["https://www.googleapis.com/auth/spreadsheets"]

if GOOGLE_CREDENTIALS_JSON:
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
else:
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)

client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)

# === ROUTES ===
@app.route('/webhook', methods=['POST'])
def webhook():
    """Приём JSON от клиента и запись в Google Sheets + Slack"""
    try:
        data = request.get_json(force=True)
        app.logger.info(f"Received event from user={data.get('user')} event={data.get('event')}")
        print("✅ Получен JSON:", data)

        user = data.get("user")
        event = data.get("event")

        if not user or not event:
            return jsonify({"status": "error", "message": "Некорректный JSON"}), 400

        # Запись в Google Sheets
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([timestamp, user, event])

        # Slack-уведомление
        send_slack_message(f"✅ {user} сообщил событие: {event} ({timestamp})")

        print("📗 Событие сохранено и отправлено в Slack.")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        app.logger.error(f"Error processing webhook: {e}", exc_info=True)
        print("❌ Ошибка:", e)
        send_slack_message(f"⚠️ Ошибка: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def send_slack_message(text):
    """Отправка уведомления в Slack"""
    if not SLACK_WEBHOOK_URL:
        app.logger.warning("Slack webhook URL не задан (SLACK_WEBHOOK_URL)")
        return
    payload = {"text": text}
    response = requests.post(SLACK_WEBHOOK_URL, json=payload)
    if response.status_code != 200:
        app.logger.warning(f"Slack error {response.status_code}: {response.text}")
        print(f"⚠️ Ошибка Slack: {response.status_code} - {response.text}")


@app.route('/health', methods=['GET'])
def health():
    """Проверка статуса"""
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    print("✅ Sheets и Slack подключены. Сервер запускается...")
    app.run(port=5000, debug=True)
