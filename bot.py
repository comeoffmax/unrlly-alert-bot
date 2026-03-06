"""
unrlly Alert Bot
Напоминает команде отправить апдейт клиенту каждые 3 дня.
Команды: /add, /list, /edit, /delete, /cancel
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
TEAM_CHAT_ID   = int(os.environ["TEAM_CHAT_ID"])
TZ = ZoneInfo("Europe/Moscow")

(
    WAITING_NAME, WAITING_CLIENT, WAITING_DEADLINE,
    EDIT_PICK_FIELD, EDIT_WAITING_VALUE,
) = range(5)

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


def generate_id(projects: dict) -> str:
    pid = f"p{int(datetime.now(TZ).timestamp())}"
    while pid in projects:
        pid += "x"
    return pid


def parse_deadline(raw: str):
    if raw.lower() in ("нет", "-", ""):
        return None, None
    try:
        parts = raw.split(".")
        d, m = int(parts[0]), int(parts[1])
        y = datetime.now(TZ).year
        candidate = datetime(y, m, d, tzinfo=TZ)
        if candidate < datetime.now(TZ):
            candidate = datetime(y + 1, m, d, tzinfo=TZ)
        return candidate.isoformat(), None
    except Exception:
        return None, "Не распознал дату. Формат: ДД.ММ (например 25.04). Сохранил без дедлайна."


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m")
    except Exception:
        return iso


def project_summary(p: dict, now: datetime) -> str:
    next_alert = datetime.fromisoformat(p["next_alert"])
    days_left  = (next_alert - now).days
    deadline   = f" | до {fmt_date(p['deadline'])}" if p.get("deadline") else ""
    if days_left <= 0:
        status = "🔴 апдейт сегодня"
    elif days_left == 1:
        status = "🟡 завтра"
    else:
        status = f"🟢 через {days_left} дн."
    return f"• *{p['name']}* ({p['client']}){deadline}\n  ↳ {status}"


# ── /start, /help ─────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *unrlly Alert Bot*\n\n"
        "Напоминаю отправлять апдейты клиентам каждые 3 дня.\n\n"
        "/add — добавить проект\n"
        "/list — активные проекты\n"
        "/edit — изменить проект\n"
        "/delete — удалить проект\n"
        "/help — справка",
        parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/add — добавить проект\n"
        "/list — список активных\n"
        "/edit — изменить название, клиента или дедлайн\n"
        "/delete — удалить проект\n"
        "/cancel — отменить текущий диалог\n\n"
        "Кнопки под напоминанием:\n"
        "  ✅ Апдейт отправлен — сбросить таймер на 3 дня\n"
        "  ⏰ +1 день — отложить\n"
        "  🏁 Завершить проект"
    )


# ── /add ─────────────────────────────────────────────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_project", None)
    await update.message.reply_text("Название проекта:")
    return WAITING_NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_project"] = {"name": update.message.text.strip()}
    await update.message.reply_text("Имя клиента (как обращаться):")
    return WAITING_CLIENT


async def add_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_project"]["client"] = update.message.text.strip()
    await update.message.reply_text("Дедлайн проекта (ДД.ММ или напиши «нет»):")
    return WAITING_DEADLINE


async def add_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw      = update.message.text.strip()
    projects = load_projects()
    proj     = context.user_data.get("new_project", {})

    if not proj.get("name"):
        await update.message.reply_text("Что-то пошло не так. Начни заново — /add")
        return ConversationHandler.END

    proj_id = generate_id(projects)
    deadline_iso, err = parse_deadline(raw)
    if err:
        await update.message.reply_text(err)

    now = datetime.now(TZ)
    projects[proj_id] = {
        "id":          proj_id,
        "name":        proj["name"],
        "client":      proj["client"],
        "deadline":    deadline_iso,
        "last_update": now.isoformat(),
        "next_alert":  (now + timedelta(days=3)).isoformat(),
        "active":      True,
        "added_by":    update.effective_user.first_name,
    }
    save_projects(projects)
    context.user_data.pop("new_project", None)

    deadline_str = fmt_date(deadline_iso) if deadline_iso else "не указан"
    await update.message.reply_text(
        f"✅ Проект *{proj['name']}* добавлен.\n"
        f"Клиент: {proj['client']}\n"
        f"Дедлайн: {deadline_str}\n"
        f"Первое напоминание: через 3 дня",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ── /list ─────────────────────────────────────────────────────────────────────

async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    projects = load_projects()
    active   = [p for p in projects.values() if p.get("active")]

    if not active:
        await update.message.reply_text("Нет активных проектов. Добавь через /add")
        return

    now   = datetime.now(TZ)
    lines = ["*Активные проекты:*\n"]
    for p in sorted(active, key=lambda x: x["next_alert"]):
        lines.append(project_summary(p, now))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /edit ─────────────────────────────────────────────────────────────────────

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    projects = load_projects()
    active   = [p for p in projects.values() if p.get("active")]

    if not active:
        await update.message.reply_text("Нет активных проектов.")
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✏️ {p['name']} ({p['client']})", callback_data=f"editpick_{p['id']}")]
        for p in sorted(active, key=lambda x: x["next_alert"])
    ])
    await update.message.reply_text("Какой проект редактировать?", reply_markup=keyboard)
    return EDIT_PICK_FIELD


async def edit_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    proj_id  = query.data.split("_", 1)[1]
    projects = load_projects()

    if proj_id not in projects:
        await query.edit_message_text("Проект не найден.")
        return ConversationHandler.END

    p = projects[proj_id]
    context.user_data["edit_proj_id"] = proj_id

    deadline_str = fmt_date(p["deadline"]) if p.get("deadline") else "нет"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Название: {p['name']}",   callback_data="editfield_name")],
        [InlineKeyboardButton(f"Клиент: {p['client']}",   callback_data="editfield_client")],
        [InlineKeyboardButton(f"Дедлайн: {deadline_str}", callback_data="editfield_deadline")],
        [InlineKeyboardButton("❌ Отмена",                callback_data="editfield_cancel")],
    ])
    await query.edit_message_text(
        f"Редактирую *{p['name']}*. Что меняем?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    return EDIT_PICK_FIELD


async def edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    field = query.data.split("_", 1)[1]

    if field == "cancel":
        context.user_data.pop("edit_proj_id", None)
        context.user_data.pop("edit_field", None)
        await query.edit_message_text("Отменено.")
        return ConversationHandler.END

    context.user_data["edit_field"] = field
    prompts = {
        "name":     "Новое название проекта:",
        "client":   "Новое имя клиента:",
        "deadline": "Новый дедлайн (ДД.ММ или «нет» чтобы убрать):",
    }
    await query.edit_message_text(prompts[field])
    return EDIT_WAITING_VALUE


async def edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value    = update.message.text.strip()
    proj_id  = context.user_data.get("edit_proj_id")
    field    = context.user_data.get("edit_field")
    projects = load_projects()

    if not proj_id or proj_id not in projects or not field:
        await update.message.reply_text("Что-то пошло не так. Попробуй /edit заново.")
        return ConversationHandler.END

    p = projects[proj_id]

    if field == "name":
        p["name"] = value
        confirm = f"Название → *{value}*"
    elif field == "client":
        p["client"] = value
        confirm = f"Клиент → *{value}*"
    elif field == "deadline":
        deadline_iso, err = parse_deadline(value)
        if err:
            await update.message.reply_text(err)
        p["deadline"] = deadline_iso
        confirm = f"Дедлайн → *{fmt_date(deadline_iso) if deadline_iso else 'убран'}*"
    else:
        confirm = "Сохранено."

    save_projects(projects)
    context.user_data.pop("edit_proj_id", None)
    context.user_data.pop("edit_field", None)

    await update.message.reply_text(
        f"✅ *{p['name']}* обновлён.\n{confirm}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ── /delete ───────────────────────────────────────────────────────────────────

async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    projects = load_projects()
    active   = [p for p in projects.values() if p.get("active")]

    if not active:
        await update.message.reply_text("Нет активных проектов.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🗑 {p['name']} ({p['client']})", callback_data=f"delask_{p['id']}")]
        for p in sorted(active, key=lambda x: x["next_alert"])
    ])
    await update.message.reply_text("Какой проект удалить?", reply_markup=keyboard)


async def delete_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    proj_id  = query.data.split("_", 1)[1]
    projects = load_projects()

    if proj_id not in projects:
        await query.edit_message_text("Проект не найден.")
        return

    p = projects[proj_id]
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, удалить", callback_data=f"delconfirm_{proj_id}"),
        InlineKeyboardButton("❌ Отмена",      callback_data="delcancel_x"),
    ]])
    await query.edit_message_text(
        f"Удалить *{p['name']}* ({p['client']})?\nЭто действие необратимо.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, proj_id = query.data.split("_", 1)

    if action == "delcancel":
        await query.edit_message_text("Отменено.")
        return

    projects = load_projects()
    if proj_id not in projects:
        await query.edit_message_text("Проект не найден.")
        return

    name = projects[proj_id]["name"]
    del projects[proj_id]
    save_projects(projects)

    await query.edit_message_text(f"🗑 Проект *{name}* удалён.", parse_mode="Markdown")


# ── Плановые напоминания ──────────────────────────────────────────────────────

async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    projects = load_projects()
    now      = datetime.now(TZ)
    changed  = False

    for proj_id, p in list(projects.items()):
        if not p.get("active"):
            continue
        next_alert = datetime.fromisoformat(p["next_alert"])
        if now >= next_alert:
            await send_alert(context, proj_id, p)
            p["next_alert"] = (now + timedelta(days=3)).isoformat()
            changed = True

    if changed:
        save_projects(projects)


async def send_alert(context, proj_id: str, p: dict):
    deadline = f"\n📅 Дедлайн: {fmt_date(p['deadline'])}" if p.get("deadline") else ""
    last     = fmt_date(p["last_update"])
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
            InlineKeyboardButton("⏰ +1 день",          callback_data=f"snooze_{proj_id}"),
        ],
        [InlineKeyboardButton("🏁 Проект завершён", callback_data=f"done_{proj_id}")]
    ])
    await context.bot.send_message(
        chat_id=TEAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def alert_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, proj_id = query.data.split("_", 1)
    projects = load_projects()

    if proj_id not in projects:
        await query.edit_message_text("⚠️ Проект не найден — возможно уже удалён.")
        return

    p    = projects[proj_id]
    now  = datetime.now(TZ)
    name = query.from_user.first_name

    if action == "sent":
        p["last_update"] = now.isoformat()
        p["next_alert"]  = (now + timedelta(days=3)).isoformat()
        save_projects(projects)
        await query.edit_message_text(
            f"✅ *{p['name']}* — апдейт зафиксирован ({name}).\nСледующее напоминание через 3 дня.",
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


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_project", None)
    context.user_data.pop("edit_proj_id", None)
    context.user_data.pop("edit_field", None)
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# ── Запуск ────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            WAITING_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            WAITING_CLIENT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_client)],
            WAITING_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deadline)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_PICK_FIELD: [
                CallbackQueryHandler(edit_pick_callback,  pattern=r"^editpick_"),
                CallbackQueryHandler(edit_field_callback, pattern=r"^editfield_"),
            ],
            EDIT_WAITING_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_save),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(add_conv)
    app.add_handler(edit_conv)
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_cmd))
    app.add_handler(CommandHandler("list",   list_projects))
    app.add_handler(CommandHandler("delete", delete_start))
    app.add_handler(CallbackQueryHandler(delete_ask_callback,     pattern=r"^delask_"))
    app.add_handler(CallbackQueryHandler(delete_confirm_callback, pattern=r"^delconfirm_|^delcancel_"))
    app.add_handler(CallbackQueryHandler(alert_button_callback,   pattern=r"^(sent|snooze|done)_"))

    app.job_queue.run_repeating(check_alerts, interval=1800, first=60)

    logger.info("Alert bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
