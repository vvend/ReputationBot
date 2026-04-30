import requests
import time
import re
import csv
import io
from datetime import datetime
from flask import Flask, request
import pytz

BOT_TOKEN = "8759395470:AAG32n7rCOPan3CpLB1J5baQkdMqpWcXM0I"
SUPABASE_URL = "https://nykmpappwhuurdopmqkp.supabase.co"
SUPABASE_KEY = "sb_publishable_C1ZcZc41DIO7DmDXuj-EmA_uRsOSdX8"
ADMINS = [8578766646, 910694395, 8175540104, 7957758473]
MAIN_ADMIN = 8578766646
ALLOWED_CHAT_ID = -1003995270858
TIMEZONE = "Europe/Moscow"
CHAT_NAME = "ТОчат"
VERIFIED_THRESHOLD = 50

tz = pytz.timezone(TIMEZONE)
user_cooldown = {}

def supabase_req(method, table, params=None, data=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    try:
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
        if r.status_code == 204 or not r.text.strip():
            return []
        if r.status_code in [200, 201]:
            return r.json()
        return None
    except Exception as e:
        print(f"Supabase error: {e}")
        return None

def send_msg(chat_id, text, reply_to=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    requests.post(url, json=data)

def get_username(user):
    return f"@{user['username']}" if user.get("username") else f"id{user['id']}"

def get_user_rep(username):
    """Возвращает (plus_count, minus_count)"""
    r = supabase_req("GET", "users", params={"username": f"eq.{username}"})
    if r and len(r) > 0:
        return r[0].get("plus_count", 0), r[0].get("minus_count", 0)
    return 0, 0

def update_user_rep(username, delta_plus, delta_minus):
    """Обновляет счётчики пользователя"""
    # Проверяем, существует ли пользователь
    existing = supabase_req("GET", "users", params={"username": f"eq.{username}"})
    if not existing or len(existing) == 0:
        # Создаём нового пользователя
        supabase_req("POST", "users", data={
            "username": username,
            "plus_count": 0,
            "minus_count": 0,
            "last_active": datetime.now(tz).isoformat()
        })
        existing_plus, existing_minus = 0, 0
    else:
        existing_plus = existing[0].get("plus_count", 0)
        existing_minus = existing[0].get("minus_count", 0)
    
    # Обновляем счётчики
    new_plus = existing_plus + delta_plus
    new_minus = existing_minus + delta_minus
    supabase_req("PATCH", "users", params={"username": f"eq.{username}"}, data={
        "plus_count": new_plus,
        "minus_count": new_minus,
        "last_active": datetime.now(tz).isoformat()
    })

def is_banned(username):
    r = supabase_req("GET", "bans", params={"username": f"eq.{username}"})
    return r and len(r) > 0

def get_target_from_message(msg):
    if "reply_to_message" in msg:
        return msg["reply_to_message"]["from"]
    text = msg.get("text", "")
    match = re.search(r'@([a-zA-Z0-9_]+)', text)
    if match:
        return {"username": match.group(1), "id": 0}
    return None

def parse_amount_and_target(text):
    parts = text.split()
    if len(parts) != 3:
        return None, None
    try:
        amount = int(parts[1])
        if amount < 1 or amount > 100:
            return None, None
        target = parts[2]
        if target.startswith("@"):
            return amount, target[1:]
        return None, None
    except:
        return None, None

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if "message" not in update:
        return "OK", 200
    
    msg = update["message"]
    chat_id = msg["chat"]["id"]
    if chat_id != ALLOWED_CHAT_ID:
        return "OK", 200
    
    text = msg.get("text", "").strip()
    user_id = msg["from"]["id"]
    
    # ========== +РЕП ==========
    if text.startswith("+реп") or text.startswith("+rep"):
        now = time.time()
        if user_id in user_cooldown and now - user_cooldown[user_id] < 3:
            send_msg(chat_id, "⏳ Подожди 3 секунды", reply_to=msg["message_id"])
            return "OK", 200
        user_cooldown[user_id] = now
        
        target = get_target_from_message(msg)
        if not target:
            send_msg(chat_id, "❌ Укажи @username или ответь на сообщение", reply_to=msg["message_id"])
            return "OK", 200
        
        giver_name = get_username(msg["from"])
        target_name = target.get("username") or f"id{target['id']}"
        
        if msg["from"]["id"] == target.get("id", 0):
            send_msg(chat_id, "❌ Нельзя ставить репутацию самому себе", reply_to=msg["message_id"])
            return "OK", 200
        
        if is_banned(giver_name):
            send_msg(chat_id, "❌ Вы забанены", reply_to=msg["message_id"])
            return "OK", 200
        
        # Проверяем, голосовал ли уже
        existing = supabase_req("GET", "votes", params={"giver": f"eq.{giver_name}", "receiver": f"eq.{target_name}"})
        if existing and len(existing) > 0:
            send_msg(chat_id, "❌ Вы уже оценили этого пользователя", reply_to=msg["message_id"])
            return "OK", 200
        
        # Записываем голос
        supabase_req("POST", "votes", data={
            "giver": giver_name,
            "receiver": target_name,
            "vote": 1,
            "created_at": datetime.now(tz).isoformat()
        })
        
        # Обновляем репутацию
        update_user_rep(target_name, 1, 0)
        plus, minus = get_user_rep(target_name)
        
        send_msg(chat_id, f"✅ +реп {target_name}\n👍 {plus} | 👎 {minus}", reply_to=msg["message_id"])
        
        # Проверка на проверенного пользователя
        if plus - minus >= VERIFIED_THRESHOLD:
            send_msg(ALLOWED_CHAT_ID, f"🎉 {target_name} достиг {VERIFIED_THRESHOLD}+ и стал проверенным пользователем!")
    
    # ========== -РЕП ==========
    elif text.startswith("-реп") or text.startswith("-rep"):
        now = time.time()
        if user_id in user_cooldown and now - user_cooldown[user_id] < 3:
            send_msg(chat_id, "⏳ Подожди 3 секунды", reply_to=msg["message_id"])
            return "OK", 200
        user_cooldown[user_id] = now
        
        target = get_target_from_message(msg)
        if not target:
            send_msg(chat_id, "❌ Укажи @username или ответь на сообщение", reply_to=msg["message_id"])
            return "OK", 200
        
        giver_name = get_username(msg["from"])
        target_name = target.get("username") or f"id{target['id']}"
        
        if msg["from"]["id"] == target.get("id", 0):
            send_msg(chat_id, "❌ Нельзя ставить репутацию самому себе", reply_to=msg["message_id"])
            return "OK", 200
        
        if is_banned(giver_name):
            send_msg(chat_id, "❌ Вы забанены", reply_to=msg["message_id"])
            return "OK", 200
        
        existing = supabase_req("GET", "votes", params={"giver": f"eq.{giver_name}", "receiver": f"eq.{target_name}"})
        if existing and len(existing) > 0 and existing[0]["vote"] == -1:
            send_msg(chat_id, "❌ Вы уже поставили минус этому пользователю", reply_to=msg["message_id"])
            return "OK", 200
        
        supabase_req("POST", "votes", data={
            "giver": giver_name,
            "receiver": target_name,
            "vote": -1,
            "created_at": datetime.now(tz).isoformat()
        })
        
        update_user_rep(target_name, 0, 1)
        plus, minus = get_user_rep(target_name)
        send_msg(chat_id, f"❌ -реп {target_name}\n👍 {plus} | 👎 {minus}", reply_to=msg["message_id"])
    
    # ========== ИНФО ==========
    elif text.startswith("инфо") or text.startswith("info"):
        target = get_target_from_message(msg)
        if not target:
            target = msg["from"]
        username = target.get("username") or f"id{target['id']}"
        plus, minus = get_user_rep(username)
        diff = plus - minus
        verified = diff >= VERIFIED_THRESHOLD
        
        # Получаем последние 3 оценки
        votes = supabase_req("GET", "votes", params={"receiver": f"eq.{username}", "order": "created_at.desc", "limit": "3"})
        reviews_text = []
        if votes:
            for i, v in enumerate(votes, 1):
                emoji = "👍" if v["vote"] == 1 else "👎"
                giver_display = "Админ" if v["giver"] == "admin_gift" else v["giver"]
                date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
                reviews_text.append(f"{i}) {emoji} от {giver_display} ({date})")
        
        header = f"✅ Проверенный пользователь: {username}" if verified else f"📊 Репутация в {CHAT_NAME} у {username}"
        result = f"{header}\n👍 Плюсы: {plus}\n👎 Минусы: {minus}\n\n📝 Последние 3 оценки:\n"
        result += "\n".join(reviews_text) if reviews_text else "Нет оценок"
        send_msg(chat_id, result, reply_to=msg["message_id"])
    
    # ========== ТОП ==========
    elif text == "топ" or text == "/top":
        users = supabase_req("GET", "users")
        if users:
            data = [(u["username"], u["plus_count"] - u["minus_count"]) for u in users]
            data.sort(key=lambda x: x[1], reverse=True)
            lines = ["🏆 Топ-10 по разнице репутации:"]
            for i, (u, d) in enumerate(data[:10], 1):
                lines.append(f"{i}. {u} — {d}")
            send_msg(chat_id, "\n".join(lines))
    
    # ========== ПРОВЕРЕННЫЕ ==========
    elif text == "/проверенные" or text == "/verified":
        users = supabase_req("GET", "users")
        if users:
            verified = [(u["username"], u["plus_count"] - u["minus_count"]) for u in users if u["plus_count"] - u["minus_count"] >= VERIFIED_THRESHOLD]
            verified.sort(key=lambda x: x[1], reverse=True)
            if verified:
                lines = ["✅ ПРОВЕРЕННЫЕ ПОЛЬЗОВАТЕЛИ (разница >=50)\n"]
                for i, (u, d) in enumerate(verified, 1):
                    lines.append(f"{i}. {u} — разница: {d}")
                send_msg(chat_id, "\n".join(lines))
            else:
                send_msg(chat_id, "❌ Нет проверенных пользователей")
    
    # ========== ИСТОРИЯ ==========
    elif text == "история" or text == "/history":
        target = get_target_from_message(msg)
        if not target:
            target = msg["from"]
        username = target.get("username") or f"id{target['id']}"
        
        received = supabase_req("GET", "votes", params={"receiver": f"eq.{username}", "order": "created_at.desc", "limit": "5"})
        given = supabase_req("GET", "votes", params={"giver": f"eq.{username}", "order": "created_at.desc", "limit": "5"})
        
        out = [f"📜 История для {username}:"]
        if received:
            out.append("\n📥 Получил:")
            for v in received:
                if v.get("giver") == "admin_gift":
                    continue
                emoji = "👍" if v["vote"] == 1 else "👎"
                date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
                out.append(f"  {emoji} от {v['giver']} ({date})")
        if given:
            out.append("\n📤 Поставил:")
            for v in given:
                emoji = "👍" if v["vote"] == 1 else "👎"
                date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
                out.append(f"  {emoji} пользователю {v['receiver']} ({date})")
        send_msg(chat_id, "\n".join(out), reply_to=msg["message_id"])
    
    # ========== БАН / РАЗБАН ==========
    elif text.startswith("?реп") or text.startswith("?rep"):
        if user_id not in ADMINS:
            send_msg(chat_id, "❌ Нет прав")
            return "OK", 200
        target = get_target_from_message(msg)
        if not target:
            send_msg(chat_id, "❌ Укажите @username")
            return "OK", 200
        username = target.get("username") or f"id{target['id']}"
        supabase_req("POST", "bans", data={"username": username})
        send_msg(chat_id, f"✅ Пользователь {username} забанен")
    
    elif text.startswith("!реп") or text.startswith("!rep"):
        if user_id not in ADMINS:
            send_msg(chat_id, "❌ Нет прав")
            return "OK", 200
        target = get_target_from_message(msg)
        if not target:
            send_msg(chat_id, "❌ Укажите @username")
            return "OK", 200
        username = target.get("username") or f"id{target['id']}"
        supabase_req("DELETE", "bans", params={"username": f"eq.{username}"})
        send_msg(chat_id, f"✅ Пользователь {username} разбанен")
    
    # ========== ++РЕП / --РЕП ==========
    elif text.startswith("++реп") or text.startswith("++rep"):
        if user_id not in ADMINS:
            send_msg(chat_id, "❌ Нет прав")
            return "OK", 200
        target = get_target_from_message(msg)
        if not target:
            send_msg(chat_id, "❌ Укажите @username")
            return "OK", 200
        username = target.get("username") or f"id{target['id']}"
        plus, minus = get_user_rep(username)
        if plus == 0:
            send_msg(chat_id, "❌ У пользователя нет плюсов")
            return "OK", 200
        update_user_rep(username, -1, 0)
        new_plus, new_minus = get_user_rep(username)
        send_msg(chat_id, f"✅ Удален один плюс у {username}\n👍 {new_plus} | 👎 {new_minus}")
    
    elif text.startswith("--реп") or text.startswith("--rep"):
        if user_id not in ADMINS:
            send_msg(chat_id, "❌ Нет прав")
            return "OK", 200
        target = get_target_from_message(msg)
        if not target:
            send_msg(chat_id, "❌ Укажите @username")
            return "OK", 200
        username = target.get("username") or f"id{target['id']}"
        plus, minus = get_user_rep(username)
        if minus == 0:
            send_msg(chat_id, "❌ У пользователя нет минусов")
            return "OK", 200
        update_user_rep(username, 0, -1)
        new_plus, new_minus = get_user_rep(username)
        send_msg(chat_id, f"✅ Удален один минус у {username}\n👍 {new_plus} | 👎 {new_minus}")
    
    # ========== +++РЕП / ---РЕП (только главный админ) ==========
    elif text.startswith("+++реп") or text.startswith("+++rep"):
        if user_id != MAIN_ADMIN:
            send_msg(chat_id, "❌ Только главный администратор")
            return "OK", 200
        amount, target = parse_amount_and_target(text)
        if not amount or not target:
            send_msg(chat_id, "❌ Использование: +++реп 50 @username")
            return "OK", 200
        supabase_req("POST", "votes", data={
            "giver": "admin_gift",
            "receiver": target,
            "vote": 1,
            "created_at": datetime.now(tz).isoformat()
        })
        update_user_rep(target, amount, 0)
        plus, minus = get_user_rep(target)
        send_msg(chat_id, f"✅ Выдано {amount} плюсов @{target}\n👍 {plus} | 👎 {minus}")
    
    elif text.startswith("---реп") or text.startswith("---rep"):
        if user_id != MAIN_ADMIN:
            send_msg(chat_id, "❌ Только главный администратор")
            return "OK", 200
        amount, target = parse_amount_and_target(text)
        if not amount or not target:
            send_msg(chat_id, "❌ Использование: ---реп 50 @username")
            return "OK", 200
        supabase_req("POST", "votes", data={
            "giver": "admin_gift",
            "receiver": target,
            "vote": -1,
            "created_at": datetime.now(tz).isoformat()
        })
        update_user_rep(target, 0, amount)
        plus, minus = get_user_rep(target)
        send_msg(chat_id, f"✅ Выдано {amount} минусов @{target}\n👍 {plus} | 👎 {minus}")
    
    # ========== СБРОС ЛИМИТОВ ==========
    elif text == "/reset_limits":
        if user_id in ADMINS:
            supabase_req("DELETE", "vote_changes", params={})
            send_msg(chat_id, "✅ Суточные лимиты сброшены для всех пользователей")
        else:
            send_msg(chat_id, "❌ Нет прав")
    
    # ========== ЭКСПОРТ CSV ==========
    elif text == "/export":
        if user_id != MAIN_ADMIN:
            send_msg(chat_id, "❌ Только главный администратор")
            return "OK", 200
        users = supabase_req("GET", "users")
        if users:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["username", "plus", "minus", "diff"])
            for u in users:
                writer.writerow([u["username"], u["plus_count"], u["minus_count"], u["plus_count"] - u["minus_count"]])
            output.seek(0)
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            files = {"document": ("reputation.csv", output.getvalue().encode())}
            data = {"chat_id": chat_id}
            requests.post(url, files=files, data=data)
        else:
            send_msg(chat_id, "❌ Нет данных")
    
    # ========== БЛОКИРОВКА ССЫЛОК ==========
    else:
        if user_id not in ADMINS:
            if re.search(r'https?://[^\s]+|t\.me/[^\s]+', text):
                del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                requests.post(del_url, json={"chat_id": chat_id, "message_id": msg["message_id"]})
                send_msg(chat_id, f"❌ {get_username(msg['from'])}, ссылки запрещены")
    
    return "OK", 200

@app.route('/')
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)