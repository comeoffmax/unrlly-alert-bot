"""
unrlly Alert Bot
Напоминает команде отправить апдейт клиенту каждые 3 дня.
Команды: /add_project, /list, /done, /snooze
"""

import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["ALERT_BOT_TOKEN"]
# ID рабочего чата команды
TEAM_CHAT_ID = int(os.environ["TEAM_CHAT_ID"])
TZ = ZoneInfo("Europe/Moscow")

# Состояния диалога добавления проекта
WAITING_NAME, WAITING_CLIENT, WAITING_DEADLINE = range(3)

# Хранилище проектов в памяти (JSON-файл для персистентности)
DATA_FILE = "projects.json"


def load_projects() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_projects(projects: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m")
    except Exception:
        return iso


# ── Команды ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *unrlly Alert Bot*\n\n"
        "Напоминаю отправлять апдейты клиентам каждые 3 дня.\n\n"
        "Команды:\n"
        "/add — добавить проект\n"
        "/list — активные проекты\n"
        "/help — справка"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/add — добавить проект\n"
        "/list — список активных\n"
        "Кнопки под напоминанием:\n"
        "  ✅ Отправил апдейт — сбросить таймер\n"
        "  ⏰ Отложить на 1 день\n"
        "  🏁 Завершить проект"
    )


# ── Добавление проекта (ConversationHandler) ─────────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Название проекта:")
    return WAITING_NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_project"] = {"name": update.message.text}
    await update.message.reply_text("Имя клиента (как обращаться):")
    return WAITING_CLIENT


async def add_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_project"]["client"] = update.message.text
    await update.message.reply_text(
        "Дедлайн проекта (ДД.ММ или напиши «нет»):"
    )
    return WAITING_DEADLINE


async def add_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    projects = load_projects()

    proj = context.user_data["new_project"]
    proj_id = f"p{len(projects) + 1:03d}"

    # Парсим дедлайн
    deadline_iso = None
    if raw.lower() not in ("нет", "-", ""):
        try:
            parts = raw.split(".")
            d, m = int(parts[0]), int(parts[1])
            y = datetime.now(TZ).year
            deadline_iso = datetime(y, m, d, tzinfo=TZ).isoformat()
        except Exception:
            pass

    now = datetime.now(TZ)
    projects[proj_id] = {
        "id": proj_id,
        "name": proj["name"],
        "client": proj["client"],
        "deadline": deadline_iso,
        "last_update": now.isoformat(),
        "next_alert": (now + timedelta(days=3)).isoformat(),
        "active": True,
        "added_by": update.effective_user.first_name,
    }
    save_projects(projects)

    deadline_str = fmt_date(deadline_iso) if deadline_iso else "не указан"
    await update.message.reply_text(
        f"✅ Проект *{proj['name']}* добавлен.\n"
        f"Клиент: {proj['client']}\n"
        f"Дедлайн: {deadline_str}\n"
        f"Первый напоминатель: через 3 дня",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# ── Список проектов ───────────────────────────────────────────────────────────

async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    projects = load_projects()
    active = [p for p in projects.values() if p.get("active")]

    if not active:
        await update.message.reply_text("Нет активных проектов.")
        return

    lines = ["*Активные проекты:*\n"]
    now = datetime.now(TZ)
    for p in active:
        next_alert = datetime.fromisoformat(p["next_alert"])
        days_left = (next_alert - now).days
        deadline = f" | дедлайн {fmt_date(p['deadline'])}" if p.get("deadline") else ""
        status = "🔴 сегодня" if days_left <= 0 else f"через {days_left} дн."
        lines.append(f"• *{p['name']}* ({p['client']}){deadline}\n  ↳ апдейт {status}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Плановые напоминания ──────────────────────────────────────────────────────

async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Запускается каждые 30 минут через job_queue."""
    projects = load_projects()
    now = datetime.now(TZ)
    changed = False

    for proj_id, p in projects.items():
        if not p.get("active"):
            continue
        next_alert = datetime.fromisoformat(p["next_alert"])
        if now >= next_alert:
            await send_alert(context, proj_id, p)
            changed = True

    if changed:
        save_projects(projects)


async def send_alert(context, proj_id: str, p: dict):
    deadline = f"\n📅 Дедлайн: {fmt_date(p['deadline'])}" if p.get("deadline") else ""
    last = fmt_date(p["last_update"])

    text = (
        f"⏰ *Напоминание: апдейт клиенту*\n\n"
        f"Проект: *{p['name']}*\n"
        f"Клиент: {p['client']}{deadline}\n"
        f"Последний апдейт: {last}\n\n"
        f"Отправь короткое сообщение — что сделано, что дальше."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Апдейт отправлен", callback_data=f"sent_{proj_id}"),
            InlineKeyboardButton("⏰ +1 день", callback_data=f"snooze_{proj_id}"),
        ],
        [InlineKeyboardButton("🏁 Проект завершён", callback_data=f"done_{proj_id}")]
    ])

    await context.bot.send_message(
        chat_id=TEAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, proj_id = query.data.split("_", 1)
    projects = load_projects()

    if proj_id not in projects:
        await query.edit_message_text("Проект не найден.")
        return

    p = projects[proj_id]
    now = datetime.now(TZ)
    name = query.from_user.first_name

    if action == "sent":
        p["last_update"] = now.isoformat()
        p["next_alert"] = (now + timedelta(days=3)).isoformat()
        save_projects(projects)
        await query.edit_message_text(
            f"✅ *{p['name']}* — апдейт зафиксирован ({name}).\n"
            f"Следующее напоминание через 3 дня.",
            parse_mode="Markdown"
        )

    elif action == "snooze":
        p["next_alert"] = (now + timedelta(days=1)).isoformat()
        save_projects(projects)
        await query.edit_message_text(
            f"⏰ *{p['name']}* — отложено на 1 день ({name}).",
            parse_mode="Markdown"
        )

    elif action == "done":
        p["active"] = False
        save_projects(projects)
        await query.edit_message_text(
            f"🏁 *{p['name']}* — завершён ({name}). Молодцы!",
            parse_mode="Markdown"
        )


# ── Запуск ────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Добавление проекта через диалог
    conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            WAITING_CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_client)],
            WAITING_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deadline)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", list_projects))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_callback))

    # Проверка алертов каждые 30 минут
    app.job_queue.run_repeating(check_alerts, interval=1800, first=60)

    logger.info("Alert bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
