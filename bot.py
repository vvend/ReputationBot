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
appeal_temp = {}

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
        
        # Если ответ пустой (204 No Content) — возвращаем пустой список
        if r.status_code == 204 or not r.text.strip():
            return []
        
        # Пробуем распарсить JSON
        if r.status_code in [200, 201]:
            return r.json()
        return None
    except Exception as e:
        print(f"Supabase error: {e}, status: {r.status_code if 'r' in locals() else 'unknown'}")
        return None

def send_msg(chat_id, text, reply_to=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    requests.post(url, json=data)

def send_photo(chat_id, caption, reply_to=None):
    """Отправка картинки (если есть)"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        data = {"chat_id": chat_id, "caption": caption}
        if reply_to:
            data["reply_to_message_id"] = reply_to
        # Пробуем отправить без фото (только текст)
        send_msg(chat_id, caption, reply_to)
    except:
        send_msg(chat_id, caption, reply_to)

def get_username(user):
    return f"@{user['username']}" if user.get("username") else f"id{user['id']}"

def get_user_rep(username):
    r = supabase_req("GET", "users", params={"username": f"eq.{username}"})
    if r and len(r) > 0:
        return r[0].get("plus_count", 0), r[0].get("minus_count", 0), r[0].get("celebrated", False)
    return 0, 0, False

def update_user_rep(username, delta_plus, delta_minus):
    exists = supabase_req("GET", "users", params={"username": f"eq.{username}"})
    if not exists or len(exists) == 0:
        supabase_req("POST", "users", data={"username": username, "plus_count": 0, "minus_count": 0})
    plus, minus, _ = get_user_rep(username)
    supabase_req("PATCH", "users", params={"username": f"eq.{username}"}, data={
        "plus_count": plus + delta_plus,
        "minus_count": minus + delta_minus,
        "last_active": datetime.now(tz).isoformat()
    })

def set_verified_status(username, by_admin=False):
    plus, minus, _ = get_user_rep(username)
    if plus - minus >= VERIFIED_THRESHOLD:
        supabase_req("PATCH", "users", params={"username": f"eq.{username}"}, data={
            "verified_at": datetime.now(tz).isoformat(),
            "verified_by": "admin" if by_admin else "auto"
        })
        return True
    return False

def celebrate_user(username):
    _, _, celebrated = get_user_rep(username)
    if not celebrated:
        send_msg(ALLOWED_CHAT_ID, f"🎉 В {CHAT_NAME} новый проверенный пользователь {username} (+{VERIFIED_THRESHOLD})")
        supabase_req("PATCH", "users", params={"username": f"eq.{username}"}, data={"celebrated": True})

def is_banned(username):
    r = supabase_req("GET", "bans", params={"username": f"eq.{username}"})
    return r and len(r) > 0

def can_change_vote(giver, receiver):
    today = datetime.now(tz).date().isoformat()
    r = supabase_req("GET", "vote_changes", params={"giver": f"eq.{giver}", "receiver": f"eq.{receiver}"})
    if not r or len(r) == 0:
        return True
    return r[0].get("last_change_date") != today

def record_vote_change(giver, receiver):
    today = datetime.now(tz).date().isoformat()
    supabase_req("POST", "vote_changes", data={"giver": giver, "receiver": receiver, "last_change_date": today})

def get_target_from_message(msg):
    if "reply_to_message" in msg:
        return msg["reply_to_message"]["from"]
    text = msg.get("text", "")
    match = re.search(r'@(\w+)', text)
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

def get_last_3_reviews(username):
    votes = supabase_req("GET", "votes", params={"receiver": f"eq.{username}", "order": "created_at.desc", "limit": "10"})
    if not votes:
        return []
    unique = []
    seen = set()
    for v in votes:
        if v["giver"] not in seen and v["giver"] != "admin_gift":
            seen.add(v["giver"])
            unique.append(v)
            if len(unique) == 3:
                break
    return unique

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
        
        existing = supabase_req("GET", "votes", params={"giver": f"eq.{giver_name}", "receiver": f"eq.{target_name}"})
        if existing and len(existing) > 0:
            send_msg(chat_id, "❌ Вы уже оценили этого пользователя", reply_to=msg["message_id"])
            return "OK", 200
        
        supabase_req("POST", "votes", data={
            "giver": giver_name,
            "receiver": target_name,
            "vote": 1,
            "created_at": datetime.now(tz).isoformat()
        })
        
        old_plus, old_minus, _ = get_user_rep(target_name)
        update_user_rep(target_name, 1, 0)
        plus, minus, _ = get_user_rep(target_name)
        
        if plus - minus >= VERIFIED_THRESHOLD and old_plus - old_minus < VERIFIED_THRESHOLD:
            set_verified_status(target_name)
            celebrate_user(target_name)
        
        send_msg(chat_id, f"✅ +реп {target_name}\n👍 {plus} | 👎 {minus}", reply_to=msg["message_id"])
    
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
        
        if existing and len(existing) > 0 and existing[0]["vote"] == 1:
            if not can_change_vote(giver_name, target_name):
                send_msg(chat_id, "❌ Менять оценку можно раз в сутки", reply_to=msg["message_id"])
                return "OK", 200
            supabase_req("DELETE", "votes", params={"giver": f"eq.{giver_name}", "receiver": f"eq.{target_name}"})
            update_user_rep(target_name, -1, 0)
            record_vote_change(giver_name, target_name)
        
        supabase_req("POST", "votes", data={
            "giver": giver_name,
            "receiver": target_name,
            "vote": -1,
            "created_at": datetime.now(tz).isoformat()
        })
        
        update_user_rep(target_name, 0, 1)
        plus, minus, _ = get_user_rep(target_name)
        send_msg(chat_id, f"❌ -реп {target_name}\n👍 {plus} | 👎 {minus}", reply_to=msg["message_id"])
    
    # ========== ИНФО ==========
    elif text.startswith("инфо") or text.startswith("info"):
        target = get_target_from_message(msg)
        if not target:
            target = msg["from"]
        username = target.get("username") or f"id{target['id']}"
        plus, minus, _ = get_user_rep(username)
        diff = plus - minus
        verified = diff >= VERIFIED_THRESHOLD
        
        verified_line = ""
        if verified:
            user_data = supabase_req("GET", "users", params={"username": f"eq.{username}"})
            if user_data and len(user_data) > 0:
                verified_at = user_data[0].get("verified_at")
                verified_by = user_data[0].get("verified_by")
                if verified_at:
                    date = datetime.fromisoformat(verified_at).astimezone(tz).strftime("%d.%m.%Y")
                    if verified_by == "admin":
                        verified_line = f"\n🏅 Статус выдан администратором ({date})"
                    else:
                        verified_line = f"\n🏅 Статус достигнут автоматически ({date})"
        
        header = f"✅ Проверенный пользователь: {username}" if verified else f"📊 Репутация в {CHAT_NAME} у {username}"
        
        last_reviews = get_last_3_reviews(username)
        reviews_text = []
        for i, v in enumerate(last_reviews, 1):
            emoji = "👍" if v["vote"] == 1 else "👎"
            giver_display = "Админ" if v["giver"] == "admin_gift" else v["giver"]
            date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
            reviews_text.append(f"{i}) {emoji} от {giver_display} ({date})")
        
        result = f"{header}{verified_line}\n👍 Плюсы: {plus}\n👎 Минусы: {minus}\n\n📝 Последние 3 оценки:\n"
        result += "\n".join(reviews_text) if reviews_text else "Нет оценок"
        send_msg(chat_id, result, reply_to=msg["message_id"])
    
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
                if v["giver"] == "admin_gift":
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
    
    # ========== ++РЕП (удалить один плюс) ==========
    elif text.startswith("++реп") or text.startswith("++rep"):
        if user_id not in ADMINS:
            send_msg(chat_id, "❌ Нет прав")
            return "OK", 200
        target = get_target_from_message(msg)
        if not target:
            send_msg(chat_id, "❌ Укажите @username")
            return "OK", 200
        username = target.get("username") or f"id{target['id']}"
        plus, minus, _ = get_user_rep(username)
        if plus == 0:
            send_msg(chat_id, "❌ У пользователя нет плюсов")
            return "OK", 200
        update_user_rep(username, -1, 0)
        new_plus, new_minus, _ = get_user_rep(username)
        send_msg(chat_id, f"✅ Удален один плюс у {username}\n👍 {new_plus} | 👎 {new_minus}")
    
    # ========== --РЕП (удалить один минус) ==========
    elif text.startswith("--реп") or text.startswith("--rep"):
        if user_id not in ADMINS:
            send_msg(chat_id, "❌ Нет прав")
            return "OK", 200
        target = get_target_from_message(msg)
        if not target:
            send_msg(chat_id, "❌ Укажите @username")
            return "OK", 200
        username = target.get("username") or f"id{target['id']}"
        plus, minus, _ = get_user_rep(username)
        if minus == 0:
            send_msg(chat_id, "❌ У пользователя нет минусов")
            return "OK", 200
        update_user_rep(username, 0, -1)
        new_plus, new_minus, _ = get_user_rep(username)
        send_msg(chat_id, f"✅ Удален один минус у {username}\n👍 {new_plus} | 👎 {new_minus}")
    
    # ========== +++РЕП (массовая выдача плюсов, только главный админ) ==========
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
            "reason": f"Админ выдал {amount}+",
            "created_at": datetime.now(tz).isoformat()
        })
        update_user_rep(target, amount, 0)
        set_verified_status(target, by_admin=True)
        celebrate_user(target)
        plus, minus, _ = get_user_rep(target)
        send_msg(chat_id, f"✅ Выдано {amount} плюсов пользователю @{target}\n👍 {plus} | 👎 {minus}")
    
    # ========== ---РЕП (массовая выдача минусов, только главный админ) ==========
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
            "reason": f"Админ выдал {amount}-",
            "created_at": datetime.now(tz).isoformat()
        })
        update_user_rep(target, 0, amount)
        plus, minus, _ = get_user_rep(target)
        send_msg(chat_id, f"✅ Выдано {amount} минусов пользователю @{target}\n👍 {plus} | 👎 {minus}")
    
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
                        # Отправляем файлом через Telegram
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            files = {"document": ("reputation.csv", output.getvalue().encode())}
            data = {"chat_id": chat_id}
            requests.post(url, files=files, data=data)
        else:
            send_msg(chat_id, "❌ Нет данных")
    
    # ========== АПЕЛЛЯЦИЯ (только ответ на -реп) ==========
    elif text in ["/ap", "/ап"]:
        send_msg(chat_id, "📝 Функция апелляции в разработке. Пока можно обжаловать минус лично админу.")
    
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