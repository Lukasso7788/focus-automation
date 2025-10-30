import requests
import json

BASE_URL = "https://focus-automation.onrender.com"

tests = [
    ("Health check", "GET", "/health", None),
    ("Webhook", "POST", "/webhook", {"user": "Yaroslav", "event": "Full pipeline test"}),
    ("Telegram", "POST", "/telegram", {"user": "Yaroslav", "message": "Telegram endpoint test"}),
]

# Если у тебя есть Discord webhook — добавь этот эндпоинт в список
# tests.append(("Discord", "POST", "/discord", {"user": "Yaroslav", "message": "Discord test"}))

print("🚀 Starting full automation test...\n")

for name, method, endpoint, payload in tests:
    url = f"{BASE_URL}{endpoint}"
    try:
        if method == "GET":
            response = requests.get(url)
        else:
            response = requests.post(url, json=payload)

        status = "✅" if response.status_code == 200 else "❌"
        print(f"{status} {name} → {response.status_code} {response.text[:100]}")

    except Exception as e:
        print(f"❌ {name} → Error: {e}")

print("\n🎯 Test completed.")
