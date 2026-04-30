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
    if resp and len(resp) > 0:
        u = resp[0]
        return u.get("plus_count", 0), u.get("minus_count", 0), u.get("celebrated", False), u.get("verified_at"), u.get("verified_by")
    return 0, 0, False, None, None

def update_user_rep(username, delta_plus, delta_minus):
    existing = supabase_request("GET", "users", params={"username": f"eq.{username}"})
    if not existing or len(existing) == 0:
        supabase_request("POST", "users", data={"username": username, "plus_count": 0, "minus_count": 0, "celebrated": False})
    current_plus, current_minus, _, _, _ = get_user_rep(username)
    data = {
        "plus_count": current_plus + delta_plus,
        "minus_count": current_minus + delta_minus,
        "last_active": datetime.now(tz).isoformat()
    }
    supabase_request("PATCH", "users", params={"username": f"eq.{username}"}, data=data)

def set_verified_status(username, by_admin=False):
    current_plus, current_minus, _, _, _ = get_user_rep(username)
    diff = current_plus - current_minus
    if diff >= VERIFIED_THRESHOLD:
        data = {
            "verified_at": datetime.now(tz).isoformat(),
            "verified_by": "admin" if by_admin else "auto"
        }
        supabase_request("PATCH", "users", params={"username": f"eq.{username}"}, data=data)
        return True
    return False

def celebrate_user(username, user_id):
    _, _, celebrated, _, _ = get_user_rep(username)
    if not celebrated:
        try:
            send_message(ALLOWED_CHAT_ID, f"🎉 В НАШЕМ {CHAT_NAME} НОВЫЙ ПРОВЕРЕННЫЙ ПОЛЬЗОВАТЕЛЬ, ДОСТИГШИЙ 50 +РЕП @{username if username.startswith('@') else username}")
            supabase_request("PATCH", "users", params={"username": f"eq.{username}"}, data={"celebrated": True})
        except Exception as e:
            print(f"Ошибка отправки поздравления: {e}")

def is_admin(user_id):
    return user_id in ADMINS

def is_main_admin(user_id):
    return user_id == ADMINS[0]

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

def get_username_from_message(message):
    if "reply_to_message" in message:
        return message["reply_to_message"]["from"]
    text = message.get("text", "")
    parts = text.split()
    if len(parts) > 1 and parts[1].startswith("@"):
        username = parts[1][1:]
        return {"username": username, "id": 0}
    return None

# --- Обработка команд ---
def process_vote(message, is_plus):
    chat_id = message["chat"]["id"]
    if chat_id != ALLOWED_CHAT_ID:
        return
    
    user_id = message["from"]["id"]
    
    # Защита от спама (только для команд +реп и -реп)
    now = time.time()
    last_cmd = user_last_message.get(user_id, 0)
    if now - last_cmd < 3:
        send_message(chat_id, "⏳ Не так быстро! Подожди 3 секунды.", reply_to_message_id=message["message_id"])
        return
    user_last_message[user_id] = now
    
    target = get_username_from_message(message)
    if not target:
        send_message(chat_id, "❌ Ответьте на сообщение пользователя или укажите @username", reply_to_message_id=message["message_id"])
        return
    
    giver = message["from"]
    giver_username = giver.get("username", f"id{giver['id']}")
    target_username = target.get("username", f"id{target['id']}")
    
    if giver["id"] == target["id"]:
        send_message(chat_id, "❌ Нельзя ставить репутацию самому себе", reply_to_message_id=message["message_id"])
        return
    
    if is_banned(giver_username):
        send_message(chat_id, "❌ Вы забанены и не можете ставить оценки", reply_to_message_id=message["message_id"])
        return
    
    # Проверка существующего голоса
    vote_resp = supabase_request("GET", "votes", params={"giver": f"eq.{giver_username}", "receiver": f"eq.{target_username}"})
    already_voted = vote_resp and len(vote_resp) > 0
    old_vote = vote_resp[0]["vote"] if already_voted else 0
    
    if already_voted and ((is_plus and old_vote == 1) or (not is_plus and old_vote == -1)):
        send_message(chat_id, "❌ Вы уже оценили этого пользователя. Чтобы изменить оценку, используйте противоположную команду (+/-)", reply_to_message_id=message["message_id"])
        return
    
    # Смена оценки (раз в сутки)
    if already_voted and old_vote != (1 if is_plus else -1):
        today = datetime.now(tz).date().isoformat()
        change_resp = supabase_request("GET", "vote_changes", params={"giver": f"eq.{giver_username}", "receiver": f"eq.{target_username}"})
        if change_resp and len(change_resp) > 0:
            last_date = change_resp[0].get("last_change_date")
            if last_date == today:
                send_message(chat_id, "❌ Вы уже меняли оценку этому пользователю сегодня. Попробуйте завтра", reply_to_message_id=message["message_id"])
                return
    
    old_plus, old_minus, _, _, _ = get_user_rep(target_username)
    old_diff = old_plus - old_minus
    
    # Удаляем старый голос если есть
    if already_voted:
        supabase_request("DELETE", "votes", params={"giver": f"eq.{giver_username}", "receiver": f"eq.{target_username}"})
        if old_vote == 1:
            update_user_rep(target_username, -1, 0)
        else:
            update_user_rep(target_username, 0, -1)
        # Записываем смену
        supabase_request("POST", "vote_changes", data={"giver": giver_username, "receiver": target_username, "last_change_date": datetime.now(tz).date().isoformat()})
    
    # Записываем новый голос
    new_vote = 1 if is_plus else -1
    supabase_request("POST", "votes", data={
        "giver": giver_username,
        "receiver": target_username,
        "vote": new_vote,
        "created_at": datetime.now(tz).isoformat()
    })
    
    # Обновляем репутацию
    if is_plus:
        update_user_rep(target_username, 1, 0)
    else:
        update_user_rep(target_username, 0, 1)
    
    plus, minus, _, _, _ = get_user_rep(target_username)
    new_diff = plus - minus
    
    # Проверка на достижение 50+
    if new_diff >= VERIFIED_THRESHOLD and old_diff < VERIFIED_THRESHOLD:
        set_verified_status(target_username, by_admin=False)
        celebrate_user(target_username, target["id"])
    
    emoji = "✅" if is_plus else "❌"
    action = "плюс" if is_plus else "минус"
    send_message(chat_id, f"{emoji} Вы поставили {action} {target_username}\n📊 Теперь у него: 👍 {plus} | 👎 {minus}", reply_to_message_id=message["message_id"])

def info_command(chat_id, message):
    target = get_username_from_message(message)
    if not target:
        target = message["from"]
    username = target.get("username", f"id{target['id']}")
    plus, minus, _, verified_at, verified_by = get_user_rep(username)
    diff = plus - minus
    verified = diff >= VERIFIED_THRESHOLD
    header = f"✅ Проверенный пользователь: {username}" if verified else f"📊 Репутация в {CHAT_NAME} у {username}"
    
    verified_line = ""
    if verified and verified_at:
        date = datetime.fromisoformat(verified_at).astimezone(tz).strftime("%d.%m.%Y")
        if verified_by == "admin":
            verified_line = f"\n🏅 Статус «Проверенный» выдан администратором ({date})"
        else:
            verified_line = f"\n🏅 Статус «Проверенный» достигнут автоматически ({date})"
    
    # Последние 3 уникальных отзыва
    votes_resp = supabase_request("GET", "votes", params={"receiver": f"eq.{username}", "order": "created_at.desc", "limit": "10"})
    unique = []
    seen = set()
    if votes_resp:
        for v in votes_resp:
            if v["giver"] not in seen and v["giver"] != "admin_gift":
                seen.add(v["giver"])
                unique.append(v)
                if len(unique) == 3:
                    break
    
    feedback_lines = []
    for i, v in enumerate(unique, 1):
        emoji_v = "👍" if v["vote"] == 1 else "👎"
        giver_display = "Админ" if v["giver"] == "admin_gift" else v["giver"]
        reason_text = f" ➞ {v['reason']}" if v.get("reason") else ""
        date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
        feedback_lines.append(f"{i}) {emoji_v} от {giver_display} ({date}){reason_text}")
    
    if not feedback_lines:
        feedback_lines = ["Нет оценок"]
    
    send_message(chat_id, f"{header}{verified_line}\n👍 Плюсы: {plus}\n👎 Минусы: {minus}\n\n📝 Последние 3 оценки:\n" + "\n".join(feedback_lines))

def history_command(chat_id, message):
    target = get_username_from_message(message)
    if not target:
        target = message["from"]
    username = target.get("username", f"id{target['id']}")
    
    received = supabase_request("GET", "votes", params={"receiver": f"eq.{username}", "order": "created_at.desc", "limit": "5"})
    given = supabase_request("GET", "votes", params={"giver": f"eq.{username}", "order": "created_at.desc", "limit": "5"})
    
    out = [f"📜 История для {username}:"]
    if received:
        out.append("\n📥 Получил:")
        for v in received[:5]:
            if v["giver"] == "admin_gift":
                continue
            emoji = "👍" if v["vote"] == 1 else "👎"
            date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
            out.append(f"  {emoji} от {v['giver']} ({date})")
    if given:
        out.append("\n📤 Поставил:")
        for v in given[:5]:
            emoji = "👍" if v["vote"] == 1 else "👎"
            date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
            out.append(f"  {emoji} пользователю {v['receiver']} ({date})")
    send_message(chat_id, "\n".join(out))

def top_command(chat_id):
    resp = supabase_request("GET", "users")
    if not resp:
        send_message(chat_id, "❌ Нет данных")
        return
    users = [(u["username"], u["plus_count"] - u["minus_count"]) for u in resp]
    users.sort(key=lambda x: x[1], reverse=True)
    top10 = users[:10]
    lines = ["🏆 Топ-10 по разнице репутации:"]
    for i, (u, d) in enumerate(top10, 1):
        lines.append(f"{i}. {u} — {d}")
    send_message(chat_id, "\n".join(lines))

def verified_list_command(chat_id):
    resp = supabase_request("GET", "users")
    if not resp:
        send_message(chat_id, "❌ Нет данных")
        return
    users = [(u["username"], u["plus_count"] - u["minus_count"]) for u in resp if (u["plus_count"] - u["minus_count"]) >= VERIFIED_THRESHOLD]
    users.sort(key=lambda x: x[1], reverse=True)
    if not users:
        send_message(chat_id, "❌ Нет проверенных пользователей")
        return
    lines = ["✅ ПРОВЕРЕННЫЕ ПОЛЬЗОВАТЕЛИ (разница >=50)\n"]
    for i, (u, d) in enumerate(users, 1):
        lines.append(f"{i}. {u} — разница: {d}")
    send_message(chat_id, "\n".join(lines))

def ban_user(chat_id, message, is_ban):
    if not is_admin(message["from"]["id"]):
        send_message(chat_id, "❌ Нет прав")
        return
    target = get_username_from_message(message)
    if not target:
        send_message(chat_id, "❌ Укажите @username")
        return
    username = target.get("username", f"id{target['id']}")
    if is_ban:
        supabase_request("POST", "bans", data={"username": username})
        send_message(chat_id, f"✅ Пользователь {username} забанен")
    else:
        supabase_request("DELETE", "bans", params={"username": f"eq.{username}"})
        send_message(chat_id, f"✅ Пользователь {username} разбанен")

def adjust_reputation(chat_id, message, delta_plus, delta_minus, action_name):
    if not is_admin(message["from"]["id"]):
        send_message(chat_id, "❌ Нет прав")
        return
    target = get_username_from_message(message)
    if not target:
        send_message(chat_id, "❌ Укажите @username")
        return
    username = target.get("username", f"id{target['id']}")
    plus, minus, _, _, _ = get_user_rep(username)
    
    if delta_plus < 0 and plus == 0:
        send_message(chat_id, f"❌ У пользователя нет плюсов для удаления")
        return
    if delta_minus < 0 and minus == 0:
        send_message(chat_id, f"❌ У пользователя нет минусов для удаления")
        return
    
    update_user_rep(username, delta_plus, delta_minus)
    new_plus, new_minus, _, _, _ = get_user_rep(username)
    send_message(chat_id, f"✅ {action_name}\n👍 {new_plus} | 👎 {new_minus}")

def mass_adjust(chat_id, message, is_plus):
    if not is_main_admin(message["from"]["id"]):
        send_message(chat_id, "❌ Только главный администратор")
        return
    parts = message.get("text", "").split()
    if len(parts) != 3:
        send_message(chat_id, "❌ Использование: +++реп 50 @username")
        return
    try:
        amount = int(parts[1])
        if amount < 1 or amount > 100:
            send_message(chat_id, "❌ Можно от 1 до 100")
            return
    except:
        send_message(chat_id, "❌ Укажите число")
        return
    target = parts[2]
    if not target.startswith("@"):
        send_message(chat_id, "❌ Укажите @username")
        return
    username = target[1:]
    if is_plus:
        update_user_rep(username, amount, 0)
        supabase_request("POST", "votes", data={"giver": "admin_gift", "receiver": username, "vote": 1, "reason": f"Админ выдал {amount}+", "created_at": datetime.now(tz).isoformat()})
        set_verified_status(username, by_admin=True)
        send_message(chat_id, f"✅ Выдано {amount} плюсов пользователю {username}")
    else:
        update_user_rep(username, 0, amount)
        supabase_request("POST", "votes", data={"giver": "admin_gift", "receiver": username, "vote": -1, "reason": f"Админ выдал {amount}-", "created_at": datetime.now(tz).isoformat()})
        send_message(chat_id, f"✅ Выдано {amount} минусов пользователю {username}")
    
    plus, minus, _, _, _ = get_user_rep(username)
    if (plus - minus) >= VERIFIED_THRESHOLD:
        celebrate_user(username, 0)

def block_links(message):
    chat_id = message["chat"]["id"]
    if chat_id != ALLOWED_CHAT_ID:
        return
    user_id = message["from"]["id"]
    if is_admin(user_id):
        return
    text = message.get("text", "")
    if re.search(r'https?://[^\s]+|t\.me/[^\s]+', text):
        delete_message(chat_id, message["message_id"])
        send_message(chat_id, f"❌ @{message['from'].get('username', message['from']['id'])}, ссылки запрещены")

# --- Flask вебхук ---
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if "message" in update:
        msg = update["message"]
        text = msg.get("text", "")
        
        # Команды
        if text.startswith(('+реп', '+rep')):
            process_vote(msg, True)
        elif text.startswith(('-реп', '-rep')):
            process_vote(msg, False)
        elif text.startswith(('инфо', 'info')):
            info_command(msg["chat"]["id"], msg)
        elif text == "/history" or text == "история":
            history_command(msg["chat"]["id"], msg)
        elif text == "/top" or text == "топ":
            top_command(msg["chat"]["id"])
        elif text == "/проверенные" or text == "/verified":
            verified_list_command(msg["chat"]["id"])
        elif text.startswith(('?реп', '?rep')):
            ban_user(msg["chat"]["id"], msg, True)
        elif text.startswith(('!реп', '!rep')):
            ban_user(msg["chat"]["id"], msg, False)
        elif text.startswith(('++реп', '++rep')):
            adjust_reputation(msg["chat"]["id"], msg, -1, 0, "Удален один плюс")
        elif text.startswith(('--реп', '--rep')):
            adjust_reputation(msg["chat"]["id"], msg, 0, -1, "Удален один минус")
        elif text.startswith(('+++реп', '+++rep')):
            mass_adjust(msg["chat"]["id"], msg, True)
        elif text.startswith(('---реп', '---rep')):
            mass_adjust(msg["chat"]["id"], msg, False)
        else:
            block_links(msg)
    return "OK", 200

@app.route('/')
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)