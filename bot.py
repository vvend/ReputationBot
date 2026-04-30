import requests
import time
import re
from datetime import datetime
from flask import Flask, request
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
# ===============================

tz = pytz.timezone(TIMEZONE)
user_last_cmd = {}

# --- Supabase helpers ---
def supabase_request(method, table, params=None, data=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    if method == "GET":
        r = requests.get(url, headers=headers, params=params)
    elif method == "POST":
        r = requests.post(url, headers=headers, json=data)
    elif method == "DELETE":
        r = requests.delete(url, headers=headers, params=params)
    elif method == "PATCH":
        r = requests.patch(url, headers=headers, json=data, params=params)
    else:
        return None
    return r.json() if r.status_code in [200, 201] else None

# --- Вспомогательные ---
def get_user_rep(username):
    resp = supabase_request("GET", "users", params={"username": f"eq.{username}"})
    if resp and len(resp) > 0:
        u = resp[0]
        return u.get("plus_count", 0), u.get("minus_count", 0), u.get("celebrated", False)
    return 0, 0, False

def update_user_rep(username, delta_plus, delta_minus):
    existing = supabase_request("GET", "users", params={"username": f"eq.{username}"})
    if not existing or len(existing) == 0:
        supabase_request("POST", "users", data={"username": username, "plus_count": 0, "minus_count": 0})
    current_plus, current_minus, _ = get_user_rep(username)
    supabase_request("PATCH", "users", params={"username": f"eq.{username}"}, data={
        "plus_count": current_plus + delta_plus,
        "minus_count": current_minus + delta_minus,
        "last_active": datetime.now(tz).isoformat()
    })

def send_message(chat_id, text, reply_to=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    requests.post(url, json=data)

def get_target(msg):
    if "reply_to_message" in msg:
        return msg["reply_to_message"]["from"]
    text = msg.get("text", "")
    parts = text.split()
    if len(parts) > 1 and parts[1].startswith("@"):
        return {"username": parts[1][1:], "id": 0}
    return None

# --- Обработка ---
def process_vote(msg, is_plus):
    chat_id = msg["chat"]["id"]
    if chat_id != ALLOWED_CHAT_ID:
        return
    
    # Спам-защита только для команд +/-
    user_id = msg["from"]["id"]
    now = time.time()
    if user_id in user_last_cmd and now - user_last_cmd[user_id] < 3:
        send_message(chat_id, "⏳ Подожди 3 секунды", reply_to=msg["message_id"])
        return
    user_last_cmd[user_id] = now
    
    target = get_target(msg)
    if not target:
        send_message(chat_id, "❌ Ответь на сообщение или укажи @username", reply_to=msg["message_id"])
        return
    
    giver = msg["from"]
    giver_name = giver.get("username", f"id{giver['id']}")
    target_name = target.get("username", f"id{target['id']}")
    
    if giver["id"] == target["id"]:
        send_message(chat_id, "❌ Нельзя себе", reply_to=msg["message_id"])
        return
    
    # Проверка бана
    ban_resp = supabase_request("GET", "bans", params={"username": f"eq.{giver_name}"})
    if ban_resp and len(ban_resp) > 0:
        send_message(chat_id, "❌ Ты забанен", reply_to=msg["message_id"])
        return
    
    # Проверка существующего голоса
    vote_resp = supabase_request("GET", "votes", params={"giver": f"eq.{giver_name}", "receiver": f"eq.{target_name}"})
    already = vote_resp and len(vote_resp) > 0
    old_vote = vote_resp[0]["vote"] if already else 0
    
    if already and ((is_plus and old_vote == 1) or (not is_plus and old_vote == -1)):
        send_message(chat_id, "❌ Ты уже оценил", reply_to=msg["message_id"])
        return
    
    # Смена оценки (раз в сутки)
    if already and old_vote != (1 if is_plus else -1):
        today = datetime.now(tz).date().isoformat()
        change_resp = supabase_request("GET", "vote_changes", params={"giver": f"eq.{giver_name}", "receiver": f"eq.{target_name}"})
        if change_resp and len(change_resp) > 0 and change_resp[0].get("last_change_date") == today:
            send_message(chat_id, "❌ Менять можно раз в сутки", reply_to=msg["message_id"])
            return
    
    # Сохраняем старую репутацию для проверки 50
    old_plus, old_minus, _ = get_user_rep(target_name)
    old_diff = old_plus - old_minus
    
    # Удаляем старый голос
    if already:
        supabase_request("DELETE", "votes", params={"giver": f"eq.{giver_name}", "receiver": f"eq.{target_name}"})
        if old_vote == 1:
            update_user_rep(target_name, -1, 0)
        else:
            update_user_rep(target_name, 0, -1)
        supabase_request("POST", "vote_changes", data={"giver": giver_name, "receiver": target_name, "last_change_date": datetime.now(tz).date().isoformat()})
    
    # Новый голос
    supabase_request("POST", "votes", data={
        "giver": giver_name,
        "receiver": target_name,
        "vote": 1 if is_plus else -1,
        "created_at": datetime.now(tz).isoformat()
    })
    
    if is_plus:
        update_user_rep(target_name, 1, 0)
    else:
        update_user_rep(target_name, 0, 1)
    
    plus, minus, _ = get_user_rep(target_name)
    new_diff = plus - minus
    
    # Поздравление при 50+
    if new_diff >= VERIFIED_THRESHOLD and old_diff < VERIFIED_THRESHOLD:
        send_message(ALLOWED_CHAT_ID, f"🎉 В {CHAT_NAME} новый проверенный пользователь {target_name} (+{VERIFIED_THRESHOLD})")
    
    emoji = "✅" if is_plus else "❌"
    action = "плюс" if is_plus else "минус"
    send_message(chat_id, f"{emoji} {action} {target_name}\n👍 {plus} | 👎 {minus}", reply_to=msg["message_id"])

def info_command(chat_id, msg):
    target = get_target(msg)
    if not target:
        target = msg["from"]
    username = target.get("username", f"id{target['id']}")
    plus, minus, _ = get_user_rep(username)
    diff = plus - minus
    verified = diff >= VERIFIED_THRESHOLD
    header = f"✅ Проверенный: {username}" if verified else f"📊 Репутация в {CHAT_NAME} у {username}"
    
    # Последние 3 отзыва
    votes = supabase_request("GET", "votes", params={"receiver": f"eq.{username}", "order": "created_at.desc", "limit": "10"})
    unique, seen = [], set()
    if votes:
        for v in votes:
            if v["giver"] not in seen:
                seen.add(v["giver"])
                unique.append(v)
                if len(unique) == 3:
                    break
    lines = []
    for i, v in enumerate(unique, 1):
        emoji = "👍" if v["vote"] == 1 else "👎"
        date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
        lines.append(f"{i}) {emoji} от {v['giver']} ({date})")
    
    text = f"{header}\n👍 {plus} | 👎 {minus}\n\n📝 Последние 3 оценки:\n" + ("\n".join(lines) if lines else "Нет оценок")
    send_message(chat_id, text, reply_to=msg["message_id"])

def top_command(chat_id):
    users = supabase_request("GET", "users")
    if not users:
        send_message(chat_id, "Нет данных")
        return
    data = [(u["username"], u["plus_count"] - u["minus_count"]) for u in users]
    data.sort(key=lambda x: x[1], reverse=True)
    top10 = data[:10]
    lines = ["🏆 Топ-10 по разнице:"]
    for i, (u, d) in enumerate(top10, 1):
        lines.append(f"{i}. {u} — {d}")
    send_message(chat_id, "\n".join(lines))

def verified_list_command(chat_id):
    users = supabase_request("GET", "users")
    if not users:
        send_message(chat_id, "Нет данных")
        return
    verified = [(u["username"], u["plus_count"] - u["minus_count"]) for u in users if (u["plus_count"] - u["minus_count"]) >= VERIFIED_THRESHOLD]
    verified.sort(key=lambda x: x[1], reverse=True)
    if not verified:
        send_message(chat_id, "Нет проверенных")
        return
    lines = ["✅ Проверенные:"]
    for i, (u, d) in enumerate(verified, 1):
        lines.append(f"{i}. {u} — {d}")
    send_message(chat_id, "\n".join(lines))

def block_links(msg):
    chat_id = msg["chat"]["id"]
    if chat_id != ALLOWED_CHAT_ID:
        return
    if msg["from"]["id"] in ADMINS:
        return
    text = msg.get("text", "")
    if re.search(r'https?://[^\s]+|t\.me/[^\s]+', text):
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
        requests.post(url, json={"chat_id": chat_id, "message_id": msg["message_id"]})
        send_message(chat_id, f"❌ @{msg['from'].get('username', msg['from']['id'])}, ссылки запрещены")

# --- Flask ---
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if "message" in update:
        msg = update["message"]
        text = msg.get("text", "")
        if text.startswith(('+реп', '+rep')):
            process_vote(msg, True)
        elif text.startswith(('-реп', '-rep')):
            process_vote(msg, False)
        elif text.startswith(('инфо', 'info')):
            info_command(msg["chat"]["id"], msg)
        elif text in ["/top", "топ"]:
            top_command(msg["chat"]["id"])
        elif text in ["/проверенные", "/verified"]:
            verified_list_command(msg["chat"]["id"])
        else:
            block_links(msg)
    return "OK", 200

@app.route('/')
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)