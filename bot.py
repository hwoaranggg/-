import os
import asyncio
import logging
import json
import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

import google.generativeai as genai
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, JobQueue
)

from database import Database

# ─── CONFIG ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MY_USER_ID     = int(os.environ["MY_USER_ID"])
TIMEZONE       = os.environ.get("TIMEZONE", "Europe/Amsterdam")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

db = Database()
TZ = ZoneInfo(TIMEZONE)

# ─── SYSTEM PROMPT ────────────────────────────────────────────────────────────
def build_system_prompt(user_id: int) -> str:
    profile   = db.get_profile(user_id)
    goals     = db.get_goals(user_id)
    habits    = db.get_habits(user_id)
    last_msgs = db.get_recent_messages(user_id, limit=30)
    now       = datetime.now(TZ)

    goals_text  = "\n".join(f"  • [{g['priority'].upper()}] {g['title']}: {g['description']}" for g in goals) or "  (не заданы)"
    habits_text = "\n".join(f"  • {h['time']} — {h['name']}" for h in habits) or "  (не заданы)"

    history_lines = []
    for m in last_msgs[-20:]:
        role = "Пользователь" if m["role"] == "user" else "Наставник"
        history_lines.append(f"{role}: {m['content']}")
    history_text = "\n".join(history_lines) or "(нет истории)"

    return f"""Ты — личный наставник и жизненный коуч. Не бот, не ассистент — живой наставник.

ХАРАКТЕР:
- Говоришь как умный близкий друг: тепло, честно, без пафоса
- Короткие живые ответы (2-5 предложений), без списков и bullet points
- Иногда шутишь, иногда жёстко говоришь правду — в зависимости от ситуации
- Помнишь всё что человек тебе рассказывал — его цели, страхи, прогресс
- Сам инициируешь темы если видишь что что-то важное давно не обсуждалось
- Никогда не говоришь "как ИИ" или "как языковая модель"

ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:
Имя: {profile.get('name', 'не указано')}
О себе: {profile.get('about', 'не рассказывал')}
Сфера работы: {profile.get('work', 'не указана')}

ТЕКУЩИЕ ЦЕЛИ:
{goals_text}

ЕЖЕДНЕВНЫЕ ПРИВЫЧКИ:
{habits_text}

ТЕКУЩАЯ ДАТА И ВРЕМЯ: {now.strftime('%A, %d %B %Y, %H:%M')}

ПОСЛЕДНИЕ СООБЩЕНИЯ (контекст):
{history_text}

ВАЖНЫЕ ПРАВИЛА:
1. Если человек сказал что выполнил привычку — искренне похвали, не формально
2. Если жалуется на усталость/прокрастинацию — сначала выслушай, потом помоги разобраться
3. Если давно не говорил о важной цели — спроси как дела с ней
4. Если человек говорит о проблеме на работе — задавай уточняющие вопросы
5. Отвечай на языке пользователя (русский)
6. Можешь использовать эмодзи, но не злоупотребляй
7. Иногда задавай один вопрос в конце, но не всегда"""

# ─── GEMINI CALL ──────────────────────────────────────────────────────────────
async def ask_gemini(user_id: int, user_message: str) -> str:
    try:
        system = build_system_prompt(user_id)
        full_prompt = f"{system}\n\nПользователь: {user_message}\nНаставник:"

        response = await asyncio.to_thread(
            model.generate_content,
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.85,
                max_output_tokens=600,
            )
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "Что-то пошло не так с моей стороны, попробуй ещё раз 🙏"

# ─── KEYBOARD ─────────────────────────────────────────────────────────────────
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🎯 Мои цели"), KeyboardButton("💪 Привычки")],
        [KeyboardButton("📊 Мой прогресс"), KeyboardButton("⚙️ Настройки")],
    ], resize_keyboard=True)

# ─── HANDLERS ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != MY_USER_ID:
        await update.message.reply_text("Этот бот личный 🔒")
        return

    db.ensure_user(uid)
    profile = db.get_profile(uid)

    if not profile.get("name"):
        await update.message.reply_text(
            "Привет! Я твой личный наставник 🧭\n\n"
            "Давай познакомимся. Как тебя зовут?",
            reply_markup=main_keyboard()
        )
        ctx.user_data["setup_step"] = "name"
    else:
        reply = await ask_gemini(uid, "[система: пользователь только что запустил бота, поприветствуй его и спроси как дела]")
        await update.message.reply_text(reply, reply_markup=main_keyboard())

async def cmd_goals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    goals = db.get_goals(uid)
    if not goals:
        await update.message.reply_text(
            "У тебя пока нет целей. Напиши мне свои цели в таком формате:\n\n"
            "/addgoal Запустить проект | Высокий | Хочу выпустить MVP до конца месяца\n\n"
            "Формат: /addgoal Название | Приоритет (высокий/средний/низкий) | Описание"
        )
    else:
        text = "🎯 *Твои текущие цели:*\n\n"
        for g in goals:
            prio_emoji = {"высокий": "🔴", "средний": "🟡", "низкий": "🟢"}.get(g["priority"], "⚪")
            text += f"{prio_emoji} *{g['title']}*\n_{g['description']}_\n\n"
        await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_addgoal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        parts = update.message.text.replace("/addgoal", "").strip().split("|")
        title = parts[0].strip()
        priority = parts[1].strip().lower() if len(parts) > 1 else "средний"
        description = parts[2].strip() if len(parts) > 2 else ""
        db.add_goal(uid, title, priority, description)
        reply = await ask_gemini(uid, f"[система: пользователь добавил новую цель '{title}'. Отреагируй искренне и задай уточняющий вопрос об этой цели]")
        await update.message.reply_text(reply)
    except Exception:
        await update.message.reply_text(
            "Формат: /addgoal Название | Приоритет | Описание\n"
            "Пример: /addgoal Похудеть на 5 кг | высокий | К лету привести себя в форму"
        )

async def cmd_removegoal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    title = update.message.text.replace("/removegoal", "").strip()
    if db.remove_goal(uid, title):
        await update.message.reply_text(f"Цель «{title}» удалена ✓")
    else:
        await update.message.reply_text("Цель не найдена. Проверь название через /goals")

async def cmd_habits(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    habits = db.get_habits(uid)
    if not habits:
        await update.message.reply_text(
            "Привычек пока нет. Добавь:\n\n"
            "/addhabit 07:00 | Зарядка 10 минут\n"
            "/addhabit 07:30 | Упражнения на осанку\n"
            "/addhabit 08:00 | Завтрак"
        )
    else:
        text = "💪 *Твои привычки:*\n\n"
        for h in habits:
            text += f"⏰ {h['time']} — {h['name']}\n"
        await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_addhabit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        parts = update.message.text.replace("/addhabit", "").strip().split("|")
        habit_time = parts[0].strip()
        name = parts[1].strip()
        db.add_habit(uid, habit_time, name)
        await update.message.reply_text(f"Добавил: {habit_time} — {name} ✓\nБуду напоминать каждый день!")
    except Exception:
        await update.message.reply_text("Формат: /addhabit 07:00 | Зарядка 10 минут")

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отмечаем выполненную привычку: /done зарядка"""
    uid = update.effective_user.id
    habit_name = update.message.text.replace("/done", "").strip()
    db.mark_habit_done(uid, habit_name)
    reply = await ask_gemini(uid, f"[система: пользователь только что выполнил привычку '{habit_name}'. Искренне поздравь его — коротко и по-живому]")
    await update.message.reply_text(reply)

async def cmd_progress(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    reply = await ask_gemini(uid, "[система: пользователь хочет услышать анализ своего прогресса. Проанализируй его цели и привычки, скажи честно что идёт хорошо и что стоит улучшить. Говори как наставник, не как отчёт]")
    await update.message.reply_text(reply)

async def cmd_setname(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.message.text.replace("/setname", "").strip()
    if name:
        db.update_profile(uid, "name", name)
        await update.message.reply_text(f"Запомнил — {name} ✓")

async def cmd_setwork(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    work = update.message.text.replace("/setwork", "").strip()
    if work:
        db.update_profile(uid, "work", work)
        await update.message.reply_text(f"Запомнил сферу: {work} ✓")

async def cmd_setabout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    about = update.message.text.replace("/setabout", "").strip()
    if about:
        db.update_profile(uid, "about", about)
        reply = await ask_gemini(uid, f"[система: пользователь рассказал о себе: '{about}'. Отреагируй тепло и задай один уточняющий вопрос]")
        await update.message.reply_text(reply)

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧭 *Команды наставника:*\n\n"
        "*Профиль:*\n"
        "/setname Имя — как тебя зовут\n"
        "/setwork Сфера — чем занимаешься\n"
        "/setabout Текст — расскажи о себе\n\n"
        "*Цели:*\n"
        "/goals — посмотреть цели\n"
        "/addgoal Название | Приоритет | Описание\n"
        "/removegoal Название — удалить цель\n\n"
        "*Привычки:*\n"
        "/habits — список привычек\n"
        "/addhabit 07:00 | Название\n"
        "/done Привычка — отметить выполненной\n\n"
        "*Прочее:*\n"
        "/progress — анализ прогресса\n\n"
        "Или просто пиши мне — я всегда тут 💬",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != MY_USER_ID:
        return

    db.ensure_user(uid)
    text = update.message.text

    # Setup flow
    setup_step = ctx.user_data.get("setup_step")
    if setup_step == "name":
        db.update_profile(uid, "name", text)
        ctx.user_data["setup_step"] = "work"
        await update.message.reply_text(f"Отлично, {text}! Чем ты занимаешься — работа, проекты?")
        return
    elif setup_step == "work":
        db.update_profile(uid, "work", text)
        ctx.user_data["setup_step"] = "about"
        await update.message.reply_text("Расскажи немного о себе — цели в жизни, что сейчас важно, над чем работаешь.")
        return
    elif setup_step == "about":
        db.update_profile(uid, "about", text)
        ctx.user_data.pop("setup_step", None)
        reply = await ask_gemini(uid, f"[система: пользователь только что познакомился с тобой и рассказал о себе: '{text}'. Поприветствуй его тепло, скажи пару слов о том как будете работать вместе, и задай первый важный вопрос о его главной цели]")
        await update.message.reply_text(reply, reply_markup=main_keyboard())
        return

    # Button handlers
    if text == "🎯 Мои цели":
        await cmd_goals(update, ctx)
        return
    elif text == "💪 Привычки":
        await cmd_habits(update, ctx)
        return
    elif text == "📊 Мой прогресс":
        await cmd_progress(update, ctx)
        return
    elif text == "⚙️ Настройки":
        await update.message.reply_text(
            "Настройки профиля:\n/setname Имя\n/setwork Сфера работы\n/setabout Расскажи о себе"
        )
        return

    # Regular chat
    db.save_message(uid, "user", text)
    await update.message.chat.send_action("typing")
    reply = await ask_gemini(uid, text)
    db.save_message(uid, "assistant", reply)
    await update.message.reply_text(reply)

# ─── REMINDERS ────────────────────────────────────────────────────────────────
async def send_habit_reminders(ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)
    current_time = now.strftime("%H:%M")
    habits = db.get_habits(MY_USER_ID)

    for habit in habits:
        if habit["time"] == current_time:
            if not db.is_habit_done_today(MY_USER_ID, habit["name"]):
                reply = await ask_gemini(
                    MY_USER_ID,
                    f"[система: сейчас {current_time}, время для привычки '{habit['name']}'. "
                    f"Напомни об этом коротко и по-живому. Можешь добавить мотивацию если уместно]"
                )
                await ctx.bot.send_message(chat_id=MY_USER_ID, text=reply)

async def morning_checkin(ctx: ContextTypes.DEFAULT_TYPE):
    reply = await ask_gemini(
        MY_USER_ID,
        "[система: доброе утро! Начни новый день — поприветствуй пользователя, "
        "упомяни один из его приоритетов или целей и задай заряжающий вопрос на день]"
    )
    await ctx.bot.send_message(chat_id=MY_USER_ID, text=reply)

async def evening_checkin(ctx: ContextTypes.DEFAULT_TYPE):
    reply = await ask_gemini(
        MY_USER_ID,
        "[система: вечерний чекин. Спроси как прошёл день, что удалось сделать, "
        "что было сложным. Говори тепло, как друг в конце дня]"
    )
    await ctx.bot.send_message(chat_id=MY_USER_ID, text=reply)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("goals",      cmd_goals))
    app.add_handler(CommandHandler("addgoal",    cmd_addgoal))
    app.add_handler(CommandHandler("removegoal", cmd_removegoal))
    app.add_handler(CommandHandler("habits",     cmd_habits))
    app.add_handler(CommandHandler("addhabit",   cmd_addhabit))
    app.add_handler(CommandHandler("done",       cmd_done))
    app.add_handler(CommandHandler("progress",   cmd_progress))
    app.add_handler(CommandHandler("setname",    cmd_setname))
    app.add_handler(CommandHandler("setwork",    cmd_setwork))
    app.add_handler(CommandHandler("setabout",   cmd_setabout))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduled jobs
    jq = app.job_queue
    # Check habits every minute
    jq.run_repeating(send_habit_reminders, interval=60, first=5)
    # Morning check-in at 07:00
    jq.run_daily(morning_checkin, time=time(hour=7, minute=0, tzinfo=TZ))
    # Evening check-in at 21:00
    jq.run_daily(evening_checkin, time=time(hour=21, minute=0, tzinfo=TZ))

    logger.info("Наставник запущен 🧭")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
