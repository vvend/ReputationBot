import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import os
import csv
import io

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from supabase import create_client, Client
import pytz
from aiohttp import web

# ========== НАСТРОЙКИ (ТВОИ ДАННЫЕ) ==========
BOT_TOKEN = "8759395470:AAG32n7rCOPan3CpLB1J5baQkdMqpWcXM0I"
SUPABASE_URL = "https://nykmpappwhuurdopmqkp.supabase.co"
SUPABASE_KEY = "sb_publishable_C1ZcZc41DIO7DmDXuj-EmA_uRsOSdX8"
ADMINS = [8578766646, 910694395, 8175540104, 7957758473]
ALLOWED_CHAT_ID = -1003995270858
TIMEZONE = "Europe/Moscow"
CHAT_NAME = "ТОчат"
VERIFIED_THRESHOLD = 50
MAX_FILES_APPEAL = 10
APPEAL_TIMEOUT_MINUTES = 120
ALLOWED_DOMAINS = []
# =============================================

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
tz = pytz.timezone(TIMEZONE)

logging.basicConfig(level=logging.INFO)

class AppealState(StatesGroup):
    waiting_for_text = State()
    waiting_for_files = State()

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def is_main_admin(user_id: int) -> bool:
    return user_id == ADMINS[0]

def get_display_name(username: str, user_id: int) -> str:
    if username and username.strip():
        return f"@{username}"
    return f"@id{user_id}"

def parse_command_args(text: str) -> Tuple[str, str]:
    if not text:
        return None, None
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if arg.startswith("@"):
        return cmd, arg[1:]
    return cmd, None

def get_user_rep(username: str):
    resp = supabase.table("users").select("plus_count, minus_count, celebrated, verified_at, verified_by").eq("username", username).execute()
    if resp.data:
        return resp.data[0]["plus_count"], resp.data[0]["minus_count"], resp.data[0].get("celebrated", False), resp.data[0].get("verified_at"), resp.data[0].get("verified_by")
    return 0, 0, False, None, None

def update_user_rep(username: str, delta_plus: int, delta_minus: int):
    existing = supabase.table("users").select("username").eq("username", username).execute()
    if not existing.data:
        supabase.table("users").insert({"username": username, "plus_count": 0, "minus_count": 0, "celebrated": False}).execute()
    current_plus, current_minus, _, _, _ = get_user_rep(username)
    supabase.table("users").update({
        "plus_count": current_plus + delta_plus,
        "minus_count": current_minus + delta_minus,
        "last_active": datetime.now(tz).isoformat()
    }).eq("username", username).execute()

def set_verified_status(username: str, by_admin: bool = False, admin_username: str = None):
    current_plus, current_minus, _, _, _ = get_user_rep(username)
    diff = current_plus - current_minus
    if diff >= VERIFIED_THRESHOLD:
        supabase.table("users").update({
            "verified_at": datetime.now(tz).isoformat(),
            "verified_by": "admin" if by_admin else "auto"
        }).eq("username", username).execute()
        return True
    return False

def celebrate_user(username: str, user_id: int):
    _, _, celebrated, _, _ = get_user_rep(username)
    if not celebrated:
        try:
            photo = FSInputFile("celebrate.jpg")
            asyncio.create_task(bot.send_photo(
                chat_id=ALLOWED_CHAT_ID,
                photo=photo,
                caption=f"🎉 В НАШЕМ {CHAT_NAME} НОВЫЙ ПРОВЕРЕННЫЙ ПОЛЬЗОВАТЕЛЬ, ДОСТИГШИЙ 50 +РЕП {get_display_name(username, user_id)}"
            ))
            supabase.table("users").update({"celebrated": True}).eq("username", username).execute()
        except Exception as e:
            logging.error(f"Ошибка отправки поздравления: {e}")

def is_banned(username: str) -> bool:
    resp = supabase.table("bans").select("username").eq("username", username).execute()
    return len(resp.data) > 0

def can_change_vote(giver: str, receiver: str) -> bool:
    today = datetime.now(tz).date().isoformat()
    resp = supabase.table("vote_changes").select("last_change_date").eq("giver", giver).eq("receiver", receiver).execute()
    if not resp.data:
        return True
    last_date = resp.data[0]["last_change_date"]
    return last_date != today

def record_vote_change(giver: str, receiver: str):
    today = datetime.now(tz).date().isoformat()
    supabase.table("vote_changes").upsert({"giver": giver, "receiver": receiver, "last_change_date": today}).execute()

def log_admin_action(admin_username: str, action: str, target: str = None, details: str = None):
    supabase.table("admin_logs").insert({
        "admin_username": admin_username,
        "action": action,
        "target": target,
        "details": details,
        "created_at": datetime.now(tz).isoformat()
    }).execute()

async def notify_user(user_id: int, text: str):
    try:
        await bot.send_message(user_id, text)
    except:
        pass

def contains_forbidden_link(text: str) -> bool:
    urls = re.findall(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+', text)
    for url in urls:
        if ALLOWED_DOMAINS:
            allowed = any(domain in url for domain in ALLOWED_DOMAINS)
            if not allowed:
                return True
        else:
            return True
    if re.search(r'(?:^|\s)t\.me/[^\s]+', text):
        if not ALLOWED_DOMAINS or not any("t.me" in domain for domain in ALLOWED_DOMAINS):
            return True
    return False

user_last_message = {}

async def check_spam(message: Message) -> bool:
    now = datetime.now(tz).timestamp()
    user_id = message.from_user.id
    last = user_last_message.get(user_id, 0)
    if now - last < 2:
        await message.reply("⏳ Слишком часто! Подождите немного.")
        return True
    user_last_message[user_id] = now
    return False

async def process_vote(message: Message, is_plus: bool, reason: str = ""):
    if message.chat.id != ALLOWED_CHAT_ID:
        return
    if await check_spam(message):
        return
    
    target_user = None
    target_username = None
    target_id = None
    
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
        target_username = target_user.username
        target_id = target_user.id
    else:
        cmd, arg = parse_command_args(message.text)
        if arg:
            target_username = arg
            target_id = 0
    
    if not target_user and not target_username:
        await message.reply("❌ Ответьте на сообщение пользователя или укажите @username")
        return
    
    if target_user:
        target_username = target_user.username
        target_id = target_user.id
    
    giver = message.from_user
    giver_username = giver.username or f"id{giver.id}"
    giver_id = giver.id
    target_display = target_username or f"id{target_id}"
    
    if giver.id == target_id:
        await message.reply("❌ Нельзя ставить репутацию самому себе")
        return
    
    if is_banned(giver_username):
        await message.reply("❌ Вы забанены и не можете ставить оценки")
        return
    
    old_plus, old_minus, _, _, _ = get_user_rep(target_display)
    old_diff = old_plus - old_minus
    
    existing_vote = supabase.table("votes").select("vote").eq("giver", giver_username).eq("receiver", target_display).execute()
    already_voted = len(existing_vote.data) > 0
    old_vote = existing_vote.data[0]["vote"] if already_voted else 0
    
    if already_voted and ((is_plus and old_vote == 1) or (not is_plus and old_vote == -1)):
        await message.reply("❌ Вы уже оценили этого пользователя. Чтобы изменить оценку, используйте противоположную команду (+/-)")
        return
    
    if already_voted and old_vote != (1 if is_plus else -1):
        if not can_change_vote(giver_username, target_display):
            await message.reply("❌ Вы уже меняли оценку этому пользователю сегодня. Попробуйте завтра")
            return
    
    if already_voted:
        supabase.table("votes").delete().eq("giver", giver_username).eq("receiver", target_display).execute()
        if old_vote == 1:
            update_user_rep(target_display, -1, 0)
        else:
            update_user_rep(target_display, 0, -1)
        record_vote_change(giver_username, target_display)
    else:
        record_vote_change(giver_username, target_display)
    
    new_vote = 1 if is_plus else -1
    supabase.table("votes").insert({
        "giver": giver_username,
        "receiver": target_display,
        "vote": new_vote,
        "reason": reason[:200] if reason else None,
        "created_at": datetime.now(tz).isoformat()
    }).execute()
    
    if is_plus:
        update_user_rep(target_display, 1, 0)
    else:
        update_user_rep(target_display, 0, 1)
    
    plus, minus, _, _, _ = get_user_rep(target_display)
    new_diff = plus - minus
    
    if new_diff >= VERIFIED_THRESHOLD and old_diff < VERIFIED_THRESHOLD:
        set_verified_status(target_display, by_admin=False)
        celebrate_user(target_display, target_id)
    
    emoji = "✅" if is_plus else "❌"
    action_text = "плюс" if is_plus else "минус"
    await message.reply(f"{emoji} Вы поставили {action_text} {get_display_name(target_username, target_id)}\n📊 Теперь у него: 👍 - {plus} | 👎 - {minus}")
    
    if target_user and target_user.id:
        await notify_user(target_user.id, f"📢 Вам поставил {action_text} {get_display_name(giver.username, giver.id)}\nТекущая репутация: 👍{plus} 👎{minus}")

@dp.message(F.text & (F.text.lower().startswith("+реп") | F.text.lower().startswith("+rep")))
async def plus_rep(message: Message):
    reason = ""
    text = message.text
    parts = text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("@"):
        rest = parts[1].split(maxsplit=1)
        if len(rest) > 1:
            reason = rest[1]
    await process_vote(message, True, reason)

@dp.message(F.text & (F.text.lower().startswith("-реп") | F.text.lower().startswith("-rep")))
async def minus_rep(message: Message):
    reason = ""
    text = message.text
    parts = text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("@"):
        rest = parts[1].split(maxsplit=1)
        if len(rest) > 1:
            reason = rest[1]
    await process_vote(message, False, reason)

@dp.message(F.text & (F.text.lower().startswith("+++реп") | F.text.lower().startswith("+++rep")))
async def mass_plus(message: Message):
    if not is_main_admin(message.from_user.id):
        await message.reply("❌ Только главный администратор может использовать эту команду")
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.reply("❌ Использование: +++реп 50 @username")
        return
    try:
        amount = int(parts[1])
    except:
        await message.reply("❌ Укажите число")
        return
    target = parts[2]
    if not target.startswith("@"):
        await message.reply("❌ Укажите @username")
        return
    target = target[1:]
    update_user_rep(target, amount, 0)
    supabase.table("votes").insert({
        "giver": "admin_gift",
        "receiver": target,
        "vote": 1,
        "reason": f"Админ выдал {amount}+",
        "created_at": datetime.now(tz).isoformat()
    }).execute()
    set_verified_status(target, by_admin=True)
    log_admin_action(message.from_user.username or str(message.from_user.id), "mass_plus", target, f"{amount}")
    await message.reply(f"✅ Выдано {amount} плюсов пользователю @{target}")
    plus, minus, celebrated, _, _ = get_user_rep(target)
    if (plus - minus) >= VERIFIED_THRESHOLD and not celebrated:
        celebrate_user(target, 0)

@dp.message(F.text & (F.text.lower().startswith("---реп") | F.text.lower().startswith("---rep")))
async def mass_minus(message: Message):
    if not is_main_admin(message.from_user.id):
        await message.reply("❌ Только главный администратор может использовать эту команду")
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.reply("❌ Использование: ---реп 50 @username")
        return
    try:
        amount = int(parts[1])
    except:
        await message.reply("❌ Укажите число")
        return
    target = parts[2]
    if not target.startswith("@"):
        await message.reply("❌ Укажите @username")
        return
    target = target[1:]
    update_user_rep(target, 0, amount)
    supabase.table("votes").insert({
        "giver": "admin_gift",
        "receiver": target,
        "vote": -1,
        "reason": f"Админ выдал {amount}-",
        "created_at": datetime.now(tz).isoformat()
    }).execute()
    log_admin_action(message.from_user.username or str(message.from_user.id), "mass_minus", target, f"{amount}")
    await message.reply(f"✅ Выдано {amount} минусов пользователю @{target}")

@dp.message(F.text & (F.text.lower().startswith("инфо") | F.text.lower().startswith("info")))
async def info_command(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID:
        return
    if await check_spam(message):
        return
    target_username = None
    target_id = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        target_username = target.username
        target_id = target.id
    else:
        _, arg = parse_command_args(message.text)
        if arg:
            target_username = arg
            target_id = 0
    if not target_username and not target_id:
        target_username = message.from_user.username or f"id{message.from_user.id}"
        target_id = message.from_user.id
    plus, minus, _, verified_at, verified_by = get_user_rep(target_username or str(target_id))
    diff = plus - minus
    verified = diff >= VERIFIED_THRESHOLD
    header = f"✅ Проверенный пользователь: {get_display_name(target_username, target_id)}" if verified else f"📊 Репутация в {CHAT_NAME} у {get_display_name(target_username, target_id)}"
    
    verified_line = ""
    if verified and verified_at:
        date = datetime.fromisoformat(verified_at).astimezone(tz).strftime("%d.%m.%Y")
        if verified_by == "admin":
            verified_line = f"\n🏅 Статус «Проверенный» выдан администратором ({date})"
        else:
            verified_line = f"\n🏅 Статус «Проверенный» достигнут автоматически ({date})"
    
    votes_resp = supabase.table("votes").select("giver, vote, reason, created_at").eq("receiver", target_username or str(target_id)).order("created_at", desc=True).limit(10).execute()
    unique = []
    seen = set()
    for v in votes_resp.data:
        if v["giver"] not in seen and v["giver"] != "admin_gift":
            seen.add(v["giver"])
            unique.append(v)
            if len(unique) == 3:
                break
    feedback_lines = []
    for i, v in enumerate(unique, 1):
        emoji = "👍" if v["vote"] == 1 else "👎"
        giver_display = "Админ" if v["giver"] == "admin_gift" else v["giver"]
        reason_text = f" ➞ {v['reason']}" if v.get("reason") else ""
        date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
        feedback_lines.append(f"{i}) {emoji} от {giver_display} ({date}){reason_text}")
    if not feedback_lines:
        feedback_lines = ["Нет оценок"]
    
    await message.reply(f"{header}{verified_line}\n👍 Плюсы: {plus}\n👎 Минусы: {minus}\n\n📝 Последние 3 оценки:\n" + "\n".join(feedback_lines))

@dp.message(Command("history"))
async def history_command(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID:
        return
    if await check_spam(message):
        return
    target_username = None
    target_id = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        target_username = target.username
        target_id = target.id
    else:
        parts = message.text.split()
        if len(parts) > 1 and parts[1].startswith("@"):
            target_username = parts[1][1:]
            target_id = 0
    if not target_username and not target_id:
        target_username = message.from_user.username or f"id{message.from_user.id}"
        target_id = message.from_user.id
    received = supabase.table("votes").select("giver, vote, reason, created_at").eq("receiver", target_username or str(target_id)).order("created_at", desc=True).limit(5).execute()
    given = supabase.table("votes").select("receiver, vote, reason, created_at").eq("giver", target_username or str(target_id)).order("created_at", desc=True).limit(5).execute()
    out = [f"📜 История для {get_display_name(target_username, target_id)}:"]
    if received.data:
        out.append("\n📥 Получил:")
        for v in received.data[:5]:
            if v["giver"] == "admin_gift":
                continue
            emoji = "👍" if v["vote"] == 1 else "👎"
            date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
            out.append(f"  {emoji} от {v['giver']} ({date})")
    if given.data:
        out.append("\n📤 Поставил:")
        for v in given.data[:5]:
            emoji = "👍" if v["vote"] == 1 else "👎"
            date = datetime.fromisoformat(v["created_at"]).astimezone(tz).strftime("%d.%m %H:%M")
            out.append(f"  {emoji} пользователю {v['receiver']} ({date})")
    await message.reply("\n".join(out))

@dp.message(Command("top"))
async def top_command(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID:
        return
    if await check_spam(message):
        return
    resp = supabase.table("users").select("username, plus_count, minus_count").execute()
    users = [(u["username"], u["plus_count"] - u["minus_count"]) for u in resp.data]
    users.sort(key=lambda x: x[1], reverse=True)
    top10 = users[:10]
    lines = ["🏆 Топ-10 по разнице репутации:"]
    for i, (u, diff) in enumerate(top10, 1):
        lines.append(f"{i}. {u} — {diff}")
    await message.reply("\n".join(lines))

@dp.message(Command(commands=["проверенные", "verified"]))
async def verified_list(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID:
        return
    resp = supabase.table("users").select("username, plus_count, minus_count").execute()
    verified_users = []
    for u in resp.data:
        diff = u["plus_count"] - u["minus_count"]
        if diff >= VERIFIED_THRESHOLD:
            verified_users.append((u["username"], diff))
    verified_users.sort(key=lambda x: x[1], reverse=True)
    if not verified_users:
        await message.reply("❌ Нет проверенных пользователей")
        return
    lines = ["✅ ПРОВЕРЕННЫЕ ПОЛЬЗОВАТЕЛИ (разница >=50)\n"]
    for i, (username, diff) in enumerate(verified_users, 1):
        lines.append(f"{i}. {username} — разница: {diff}")
    await message.reply("\n".join(lines))

appeal_data = {}

@dp.message(Command(commands=["ap", "ап"]))
async def start_appeal(message: Message, state: FSMContext):
    if message.chat.id != ALLOWED_CHAT_ID:
        return
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение с оценкой, которую хотите оспорить")
        return
    original = message.reply_to_message
    if not original.text or not (original.text.startswith("-реп") or original.text.startswith("-rep")):
        await message.reply("❌ Оспорить можно только отрицательную оценку (-реп)")
        return
    appeal_data[message.from_user.id] = {
        "receiver": original.reply_to_message.from_user.username or f"id{original.reply_to_message.from_user.id}" if original.reply_to_message else None,
        "giver": original.from_user.username or f"id{original.from_user.id}"
    }
    await message.reply("📝 Опишите ситуацию и приложите доказательства.\nНапишите текст апелляции:")
    await state.set_state(AppealState.waiting_for_text)

@dp.message(AppealState.waiting_for_text)
async def appeal_text(message: Message, state: FSMContext):
    if message.text.startswith("/"):
        await state.clear()
        await message.reply("❌ Апелляция отменена")
        return
    await state.update_data(text=message.text)
    await message.reply(f"📎 Теперь пришлите файлы (до {MAX_FILES_APPEAL} шт.). Когда закончите, напишите /done")
    await state.set_state(AppealState.waiting_for_files)
    appeal_data[message.from_user.id] = appeal_data.get(message.from_user.id, {})
    appeal_data[message.from_user.id]["files"] = []

@dp.message(AppealState.waiting_for_files, F.document | F.photo | F.video)
async def appeal_files(message: Message, state: FSMContext):
    files = appeal_data.get(message.from_user.id, {}).get("files", [])
    if len(files) >= MAX_FILES_APPEAL:
        await message.reply(f"❌ Нельзя больше {MAX_FILES_APPEAL} файлов")
        return
    file_id = None
    if message.document:
        file_id = message.document.file_id
    elif message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    if file_id:
        files.append(file_id)
        appeal_data[message.from_user.id]["files"] = files
        await message.reply(f"Файл добавлен ({len(files)}/{MAX_FILES_APPEAL})")

@dp.message(Command("done"), AppealState.waiting_for_files)
async def appeal_done(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("text")
    files = appeal_data.get(message.from_user.id, {}).get("files", [])
    receiver = appeal_data.get(message.from_user.id, {}).get("receiver")
    giver = appeal_data.get(message.from_user.id, {}).get("giver")
    if not text:
        await message.reply("❌ Ошибка, начните заново /ap")
        await state.clear()
        return
    supabase.table("appeals").insert({
        "receiver": receiver,
        "giver": giver,
        "text": text,
        "files": ",".join(files),
        "status": "pending",
        "created_at": datetime.now(tz).isoformat()
    }).execute()
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, f"⚠️ НОВАЯ АПЕЛЛЯЦИЯ\nОт: @{message.from_user.username or message.from_user.id}\nНа: {receiver}\nОт пользователя: {giver}\nТекст: {text}\nФайлов: {len(files)}")
        except:
            pass
    await message.reply("✅ Апелляция отправлена на рассмотрение")
    await state.clear()

@dp.message(Command(commands=["?реп", "?rep"]))
async def ban_user(message: Message):
    if not is_admin(message.from_user.id):
        return
    _, arg = parse_command_args(message.text)
    if not arg:
        await message.reply("❌ Укажите @username")
        return
    supabase.table("bans").insert({"username": arg}).execute()
    log_admin_action(message.from_user.username or str(message.from_user.id), "ban", arg)
    await message.reply(f"✅ Пользователь {arg} забанен")

@dp.message(Command(commands=["!реп", "!rep"]))
async def unban_user(message: Message):
    if not is_admin(message.from_user.id):
        return
    _, arg = parse_command_args(message.text)
    if not arg:
        await message.reply("❌ Укажите @username")
        return
    supabase.table("bans").delete().eq("username", arg).execute()
    log_admin_action(message.from_user.username or str(message.from_user.id), "unban", arg)
    await message.reply(f"✅ Пользователь {arg} разбанен")

@dp.message(Command(commands=["++реп", "++rep"]))
async def remove_plus(message: Message):
    if not is_admin(message.from_user.id):
        return
    _, arg = parse_command_args(message.text)
    if not arg:
        await message.reply("❌ Укажите @username")
        return
    plus, minus, _, _, _ = get_user_rep(arg)
    if plus == 0:
        await message.reply("❌ У пользователя нет плюсов для удаления")
        return
    update_user_rep(arg, -1, 0)
    log_admin_action(message.from_user.username or str(message.from_user.id), "remove_plus", arg)
    await message.reply(f"✅ Удален один плюс у {arg}. Теперь: 👍{plus-1} 👎{minus}")

@dp.message(Command(commands=["--реп", "--rep"]))
async def remove_minus(message: Message):
    if not is_admin(message.from_user.id):
        return
    _, arg = parse_command_args(message.text)
    if not arg:
        await message.reply("❌ Укажите @username")
        return
    plus, minus, _, _, _ = get_user_rep(arg)
    if minus == 0:
        await message.reply("❌ У пользователя нет минусов для удаления")
        return
    update_user_rep(arg, 0, -1)
    log_admin_action(message.from_user.username or str(message.from_user.id), "remove_minus", arg)
    await message.reply(f"✅ Удален один минус у {arg}. Теперь: 👍{plus} 👎{minus-1}")

@dp.message(Command("reset_limits"))
async def reset_limits(message: Message):
    if not is_admin(message.from_user.id):
        return
    supabase.table("vote_changes").delete().execute()
    await message.reply("✅ Суточные лимиты сброшены для всех пользователей")
    log_admin_action(message.from_user.username or str(message.from_user.id), "reset_all_limits")

@dp.message(Command("export"))
async def export_csv(message: Message):
    if not is_main_admin(message.from_user.id):
        return
    resp = supabase.table("users").select("username, plus_count, minus_count").execute()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["username", "plus", "minus", "diff"])
    for u in resp.data:
        writer.writerow([u["username"], u["plus_count"], u["minus_count"], u["plus_count"] - u["minus_count"]])
    output.seek(0)
    await message.reply_document(FSInputFile(io.BytesIO(output.getvalue().encode()), filename="reputation_export.csv"))

@dp.message(F.chat.id == ALLOWED_CHAT_ID)
async def block_links(message: Message):
    if message.text and (message.text.startswith(('/', '+', '-', '!', '?')) or message.text.startswith(('+++', '---'))):
        return
    if is_admin(message.from_user.id) or is_main_admin(message.from_user.id):
        return
    if message.text and contains_forbidden_link(message.text):
        await message.delete()
        await message.answer(f"❌ @{message.from_user.username or message.from_user.id}, ссылки запрещены", delete_in_seconds=5)

async def health_check(request):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logging.info("Web server started on port 8080 for pings")

async def main():
    asyncio.create_task(start_web())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())