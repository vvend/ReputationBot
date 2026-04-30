import requests
import time
import threading
import re
import csv
import io
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import pytz

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8759395470:AAG32n7rCOPan3CpLB1J5baQkdMqpWcXM0I"
SUPABASE_URL = "https://nykmpappwhuurdopmqkp.supabase.co"
SUPABASE_KEY = "sb_publishable_C1ZcZc41DIO7DmDXuj-EmA_uRsOSdX8"
ADMINS = [8578766646, 910694395, 8175540104, 7957758473]
ALLOWED_CHAT_ID = -1003995270858
TIMEZONE = "Europe/Moscow"
CHAT_NAME = "ТОчат"
VERIFIED_THRESHOLD = 50
MAX_FILES_APPEAL = 10
ALLOWED_DOMAINS = []
# ===============================

tz = pytz.timezone(TIMEZONE)
last_update_id = 0
user_last_message = {}
appeal_data = {}

# --- Supabase helpers ---
def supabase_request(method, table, params=None, data=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    if method == "GET":
        response = requests.get(url, headers=headers, params=params)
    elif method == "POST":
        response = requests.post(url, headers=headers, json=data)
    elif method == "DELETE":
        response = requests.delete(url, headers=headers, params=params)
    elif method == "PATCH":
        response = requests.patch(url, headers=headers, json=data, params=params)
    else:
        return None
    if response.status_code in [200, 201]:
        return response.json()
    return None

# --- Вспомогательные функции ---
def get_user_rep(username):
    resp = supabase_request("GET", "users", params={"username": f"eq.{username}"})
    if resp:
        u = resp[0]
        return u.get("plus_count", 0), u.get("minus_count", 0), u.get("celebrated", False), u.get("verified_at"), u.get("verified_by")
    return 0, 0, False, None, None

def update_user_rep(username, delta_plus, delta_minus):
    current_plus, current_minus, _, _, _ = get_user_rep(username)
    data = {
        "plus_count": current_plus + delta_plus,
        "minus_count": current_minus + delta_minus,
        "last_active": datetime.now(tz).isoformat()
    }
    supabase_request("PATCH", "users", params={"username": f"eq.{username}"}, data=data)

def is_banned(username):
    resp = supabase_request("GET", "bans", params={"username": f"eq.{username}"})
    return len(resp) > 0 if resp else False

def send_message(chat_id, text, reply_to_message_id=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    try:
        requests.post(url, json=data).json()
    except: pass

def delete_message(chat_id, message_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
    requests.post(url, json={"chat_id": chat_id, "message_id": message_id})

# --- Обработка команд ---
def process_vote(update, is_plus):
    message = update["message"]
    chat_id = message["chat"]["id"]
    if chat_id != ALLOWED_CHAT_ID:
        return
    user_id = message["from"]["id"]
    # antispam
    now = time.time()
    if user_id in user_last_message and now - user_last_message[user_id] < 2:
        send_message(chat_id, "⏳ Слишком часто!", reply_to_message_id=message["message_id"])
        return
    user_last_message[user_id] = now

    # target
    target_user = None
    reply = message.get("reply_to_message")
    if reply:
        target_user = reply["from"]
    else:
        text = message.get("text", "")
        parts = text.split()
        if len(parts) > 1 and parts[1].startswith("@"):
            target_user = {"username": parts[1][1:], "id": 0}
    if not target_user:
        send_message(chat_id, "❌ Ответьте на сообщение", reply_to_message_id=message["message_id"])
        return

    giver = message["from"]
    giver_username = giver.get("username", f"id{giver['id']}")
    target_username = target_user.get("username", f"id{target_user['id']}")
    if giver["id"] == target_user["id"]:
        send_message(chat_id, "❌ Нельзя себе", reply_to_message_id=message["message_id"])
        return
    if is_banned(giver_username):
        send_message(chat_id, "❌ Вы забанены", reply_to_message_id=message["message_id"])
        return

    # check existing vote
    vote_resp = supabase_request("GET", "votes", params={"giver": f"eq.{giver_username}", "receiver": f"eq.{target_username}"})
    already_voted = len(vote_resp) > 0 if vote_resp else False
    old_vote = vote_resp[0]["vote"] if already_voted else 0

    if already_voted and ((is_plus and old_vote == 1) or (not is_plus and old_vote == -1)):
        send_message(chat_id, "❌ Вы уже оценили", reply_to_message_id=message["message_id"])
        return
    if already_voted:
        # delete old vote
        supabase_request("DELETE", "votes", params={"giver": f"eq.{giver_username}", "receiver": f"eq.{target_username}"})
        if old_vote == 1:
            update_user_rep(target_username, -1, 0)
        else:
            update_user_rep(target_username, 0, -1)

    new_vote = 1 if is_plus else -1
    supabase_request("POST", "votes", data={
        "giver": giver_username,
        "receiver": target_username,
        "vote": new_vote,
        "created_at": datetime.now(tz).isoformat()
    })
    if is_plus:
        update_user_rep(target_username, 1, 0)
    else:
        update_user_rep(target_username, 0, 1)

    plus, minus, _, _, _ = get_user_rep(target_username)
    emoji = "✅" if is_plus else "❌"
    action = "плюс" if is_plus else "минус"
    send_message(chat_id, f"{emoji} Вы поставили {action} {target_username}\n👍 {plus} | 👎 {minus}", reply_to_message_id=message["message_id"])

def info_command(update):
    message = update["message"]
    chat_id = message["chat"]["id"]
    if chat_id != ALLOWED_CHAT_ID:
        return
    target = message["from"]
    if reply := message.get("reply_to_message"):
        target = reply["from"]
    else:
        text = message.get("text", "")
        parts = text.split()
        if len(parts) > 1 and parts[1].startswith("@"):
            target = {"username": parts[1][1:], "id": 0}
    username = target.get("username", f"id{target['id']}")
    plus, minus, _, _, _ = get_user_rep(username)
    diff = plus - minus
    verified = diff >= VERIFIED_THRESHOLD
    header = f"✅ Проверенный: {username}" if verified else f"📊 Репутация {username}"
    send_message(chat_id, f"{header}\n👍 {plus} | 👎 {minus}\nРазница: {diff}")

def top_command(chat_id):
    resp = supabase_request("GET", "users")
    if not resp:
        send_message(chat_id, "❌ Нет данных")
        return
    users = [(u["username"], u["plus_count"] - u["minus_count"]) for u in resp]
    users.sort(key=lambda x: x[1], reverse=True)
    top10 = users[:10]
    lines = ["🏆 Топ-10:"]
    for i, (u, d) in enumerate(top10, 1):
        lines.append(f"{i}. {u} — {d}")
    send_message(chat_id, "\n".join(lines))

def verified_list(chat_id):
    resp = supabase_request("GET", "users")
    if not resp:
        send_message(chat_id, "❌ Нет данных")
        return
    users = [(u["username"], u["plus_count"] - u["minus_count"]) for u in resp if (u["plus_count"] - u["minus_count"]) >= VERIFIED_THRESHOLD]
    users.sort(key=lambda x: x[1], reverse=True)
    if not users:
        send_message(chat_id, "❌ Нет проверенных")
        return
    lines = ["✅ Проверенные:"]
    for i, (u, d) in enumerate(users, 1):
        lines.append(f"{i}. {u} — {d}")
    send_message(chat_id, "\n".join(lines))

def block_links(message):
    chat_id = message["chat"]["id"]
    if chat_id != ALLOWED_CHAT_ID:
        return
    user_id = message["from"]["id"]
    if user_id in ADMINS:
        return
    text = message.get("text", "")
    if re.search(r'https?://[^\s]+|t\.me/[^\s]+', text):
        delete_message(chat_id, message["message_id"])
        send_message(chat_id, f"❌ @{message['from'].get('username', message['from']['id'])}, ссылки запрещены")

# --- Webhook / Polling ---
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if "message" in update:
        msg = update["message"]
        text = msg.get("text", "")
        if text.startswith(('+реп', '+rep')):
            process_vote(update, True)
        elif text.startswith(('-реп', '-rep')):
            process_vote(update, False)
        elif text.startswith(('инфо', 'info')):
            info_command(update)
        elif text == "/top" or text == "топ":
            top_command(msg["chat"]["id"])
        elif text == "/проверенные" or text == "/verified":
            verified_list(msg["chat"]["id"])
        else:
            block_links(msg)
    return "OK", 200

@app.route('/')
def health():
    return "OK", 200

def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url=<YOUR_RENDER_URL>/webhook"
    # эту ссылку нужно будет обновить вручную после деплоя

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)