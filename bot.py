import asyncio
import html
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0"))
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow")
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/evening_bot.sqlite3")
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")
if not OWNER_TELEGRAM_ID:
    raise RuntimeError("Не задан OWNER_TELEGRAM_ID")

Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("EveryEveningBot")

INTRO_TEXT = (
    "<b>Вечерняя часть 11-го Шага</b>\n\n"
    "<i>«Молитва помогает при усердии и соответствующем отношении к ней».</i>\n\n"
    "Когда мы ложимся спать, мы конструктивно пересматриваем прожитый день.\n\n"
    "Отвечай честно, но без самобичевания. Здесь не требуется литературный текст — "
    "достаточно нескольких ясных предложений."
)

QUESTIONS = [
    "Не были ли мы в течение дня злобными?",
    "Не были ли мы в течение дня эгоистичными?",
    "Не были ли мы в течение дня нечестными?",
    "Испытывали ли мы в течение дня страх?",
    "Должны ли мы извиниться перед кем-то?",
    "Может быть, мы что-то затаили про себя?",
    "Есть ли что-то, что следует обсудить с кем-либо?",
    "Проявляли ли мы в течение дня любовь и доброту ко всем окружающим?",
    "Что мы могли бы сделать лучше?",
    "Думали ли мы в течение дня в основном только о себе?",
    "Думали ли мы о том, что можем сделать для других, о нашем вкладе в общее течение жизни?",
    "Благодарности Богу за сегодня — перечисли не меньше 10 примеров.",
]

CLOSING_TEXT = (
    "<i>«Не нужно только поддаваться беспокойству, угрызениям совести или мрачным "
    "размышлениям, ибо в этом случае наши возможности приносить пользу другим уменьшаются».</i>\n\n"
    "Вспомнив события прожитого дня, мы просим прощения у Бога и спрашиваем Его, "
    "как нам исправить наши ошибки."
)

TZ_OPTIONS = [
    "Europe/Moscow",
    "Europe/Helsinki",
    "Europe/Kaliningrad",
    "Europe/Samara",
    "Asia/Yekaterinburg",
    "Asia/Omsk",
    "Asia/Novosibirsk",
    "Asia/Irkutsk",
    "Asia/Vladivostok",
]

router = Router()
scheduler = AsyncIOScheduler(timezone=timezone.utc)


class EveningForm(StatesGroup):
    answering = State()


class ReminderForm(StatesGroup):
    waiting_time = State()
    waiting_timezone = State()


@dataclass
class UserRecord:
    telegram_id: int
    full_name: str
    username: str | None
    sponsor_id: int | None
    timezone: str
    reminder_time: str | None
    reminder_enabled: bool


async def db_execute(query: str, params: tuple = ()) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(query, params)
        await db.commit()


async def db_fetchone(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        return await cur.fetchone()


async def db_fetchall(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        return await cur.fetchall()


async def init_db() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                username TEXT,
                sponsor_id INTEGER,
                timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                reminder_time TEXT,
                reminder_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invites (
                token TEXT PRIMARY KEY,
                sponsor_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                used_by INTEGER,
                used_at TEXT
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                sponsor_id INTEGER,
                answers_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reports_user_date
                ON reports(user_id, created_at);
            """
        )
        await db.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def upsert_user(message: Message, sponsor_id: int | None = None) -> None:
    existing = await db_fetchone(
        "SELECT sponsor_id FROM users WHERE telegram_id = ?",
        (message.from_user.id,),
    )
    final_sponsor = sponsor_id
    if existing and existing["sponsor_id"] and sponsor_id is None:
        final_sponsor = existing["sponsor_id"]

    await db_execute(
        """
        INSERT INTO users (
            telegram_id, full_name, username, sponsor_id, timezone,
            reminder_time, reminder_enabled, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, NULL, 0, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            full_name=excluded.full_name,
            username=excluded.username,
            sponsor_id=COALESCE(excluded.sponsor_id, users.sponsor_id),
            updated_at=excluded.updated_at
        """,
        (
            message.from_user.id,
            message.from_user.full_name,
            message.from_user.username,
            final_sponsor,
            DEFAULT_TIMEZONE,
            now_iso(),
            now_iso(),
        ),
    )


async def get_user(user_id: int) -> UserRecord | None:
    row = await db_fetchone("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
    if not row:
        return None
    return UserRecord(
        telegram_id=row["telegram_id"],
        full_name=row["full_name"],
        username=row["username"],
        sponsor_id=row["sponsor_id"],
        timezone=row["timezone"],
        reminder_time=row["reminder_time"],
        reminder_enabled=bool(row["reminder_enabled"]),
    )


def main_keyboard(is_owner: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🌙 Вечерняя часть")],
        [KeyboardButton(text="⏰ Настроить напоминание"), KeyboardButton(text="🕰 Часовой пояс")],
    ]
    if is_owner:
        rows.append([KeyboardButton(text="🔗 Пригласить подопечного"), KeyboardButton(text="👥 Подопечные")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def question_keyboard(index: int) -> InlineKeyboardMarkup:
    buttons = [[
        InlineKeyboardButton(text="Пропустить", callback_data="skip"),
        InlineKeyboardButton(text="Отменить", callback_data="cancel_evening"),
    ]]
    if index > 0:
        buttons.insert(0, [InlineKeyboardButton(text="← Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_question(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    index = data["index"]
    await message.answer(
        f"<b>Вопрос {index + 1} из {len(QUESTIONS)}</b>\n\n{html.escape(QUESTIONS[index])}",
        reply_markup=question_keyboard(index),
    )


async def begin_evening(
    message: Message,
    state: FSMContext,
    user_id: int | None = None,
) -> None:
    actual_user_id = user_id or message.from_user.id
    if user_id is None:
        await upsert_user(message)
    await state.clear()
    await state.set_state(EveningForm.answering)
    await state.update_data(index=0, answers=[], user_id=actual_user_id)
    await message.answer(INTRO_TEXT)
    await send_question(message, state)


async def finish_evening(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    answers = data["answers"]
    user_id = data["user_id"]
    user = await get_user(user_id)
    if user is None:
        await state.clear()
        await message.answer("Профиль не найден. Нажми /start и начни заново.")
        return

    import json
    await db_execute(
        """
        INSERT INTO reports(user_id, sponsor_id, answers_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            user.telegram_id,
            user.sponsor_id,
            json.dumps(answers, ensure_ascii=False),
            now_iso(),
        ),
    )

    report_lines = [
        f"<b>Вечерний отчёт — {html.escape(user.full_name)}</b>",
        f"<i>{datetime.now(ZoneInfo(user.timezone)).strftime('%d.%m.%Y %H:%M')}</i>",
        "",
    ]
    for i, (question, answer) in enumerate(zip(QUESTIONS, answers), start=1):
        report_lines.append(f"<b>{i}. {html.escape(question)}</b>")
        report_lines.append(html.escape(answer or "—"))
        report_lines.append("")

    report = "\n".join(report_lines)
    await message.answer("Спасибо. Вечерняя часть завершена.\n\n" + CLOSING_TEXT)
    await message.answer(report)

    if user.sponsor_id:
        try:
            await bot.send_message(
                user.sponsor_id,
                "📩 <b>Получен вечерний отчёт подопечного</b>\n\n" + report,
            )
            await message.answer("Ответы отправлены твоему спонсору.")
        except Exception:
            logger.exception("Не удалось отправить отчёт спонсору %s", user.sponsor_id)
            await message.answer(
                "Отчёт сохранён, но отправить его спонсору не удалось. "
                "Возможно, спонсор ещё не запускал бота."
            )
    else:
        await message.answer(
            "Спонсор пока не подключён. Ответы сохранены и показаны только тебе."
        )

    await state.clear()


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot) -> None:
    sponsor_id = None
    if command.args and command.args.startswith("invite_"):
        token = command.args.removeprefix("invite_")
        invite = await db_fetchone(
            "SELECT * FROM invites WHERE token = ? AND used_by IS NULL",
            (token,),
        )
        if invite:
            sponsor_id = invite["sponsor_id"]
            await db_execute(
                "UPDATE invites SET used_by=?, used_at=? WHERE token=?",
                (message.from_user.id, now_iso(), token),
            )

    await upsert_user(message, sponsor_id=sponsor_id)
    text = (
        "Привет. Я помогу спокойно и последовательно пройти вечернюю часть "
        "11-го Шага.\n\n"
        "Ответы не публикуются в группе. Они отправляются только подключённому "
        "спонсору и сохраняются в базе бота."
    )
    if sponsor_id:
        text += "\n\n✅ Ты подключён к своему спонсору."
        try:
            await bot.send_message(
                sponsor_id,
                f"✅ Подопечный подключился: <b>{html.escape(message.from_user.full_name)}</b>",
            )
        except Exception:
            pass

    await message.answer(
        text,
        reply_markup=main_keyboard(message.from_user.id == OWNER_TELEGRAM_ID),
    )


@router.message(Command("my_id"))
async def cmd_my_id(message: Message) -> None:
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("evening"))
@router.message(F.text == "🌙 Вечерняя часть")
async def cmd_evening(message: Message, state: FSMContext) -> None:
    await begin_evening(message, state)


@router.message(EveningForm.answering, F.text)
async def process_answer(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    answers = data["answers"]
    index = data["index"]
    answers.append(message.text.strip())
    index += 1

    if index >= len(QUESTIONS):
        await state.update_data(answers=answers, index=index)
        await finish_evening(message, state, bot)
        return

    await state.update_data(answers=answers, index=index)
    await send_question(message, state)


@router.callback_query(EveningForm.answering, F.data == "skip")
async def callback_skip(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    data = await state.get_data()
    answers = data["answers"]
    index = data["index"]
    answers.append("Пропущено")
    index += 1

    if index >= len(QUESTIONS):
        await state.update_data(answers=answers, index=index)
        await finish_evening(callback.message, state, bot)
        return

    await state.update_data(answers=answers, index=index)
    await send_question(callback.message, state)


@router.callback_query(EveningForm.answering, F.data == "back")
async def callback_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    answers = data["answers"]
    index = data["index"]
    if index > 0:
        index -= 1
        if answers:
            answers.pop()
        await state.update_data(answers=answers, index=index)
    await send_question(callback.message, state)


@router.callback_query(EveningForm.answering, F.data == "cancel_evening")
async def callback_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.answer("Вечерняя часть отменена. Можно вернуться к ней позже.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Текущий диалог отменён.")


@router.message(Command("invite"))
@router.message(F.text == "🔗 Пригласить подопечного")
async def cmd_invite(message: Message, bot: Bot) -> None:
    if message.from_user.id != OWNER_TELEGRAM_ID:
        await message.answer("Эта команда доступна владельцу бота.")
        return
    token = secrets.token_urlsafe(12)
    await db_execute(
        "INSERT INTO invites(token, sponsor_id, created_at) VALUES (?, ?, ?)",
        (token, message.from_user.id, now_iso()),
    )
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=invite_{token}"
    await message.answer(
        "Отправь эту персональную ссылку одному подопечному:\n\n"
        f"<code>{html.escape(link)}</code>\n\n"
        "После первого использования ссылка станет недействительной."
    )


@router.message(Command("sponsees"))
@router.message(F.text == "👥 Подопечные")
async def cmd_sponsees(message: Message) -> None:
    if message.from_user.id != OWNER_TELEGRAM_ID:
        await message.answer("Эта команда доступна владельцу бота.")
        return
    rows = await db_fetchall(
        "SELECT full_name, username, telegram_id, reminder_time, timezone "
        "FROM users WHERE sponsor_id = ? ORDER BY full_name",
        (message.from_user.id,),
    )
    if not rows:
        await message.answer("Подопечные пока не подключены.")
        return
    lines = ["<b>Подопечные:</b>", ""]
    for row in rows:
        username = f"@{row['username']}" if row["username"] else "без username"
        reminder = (
            f"{row['reminder_time']} ({row['timezone']})"
            if row["reminder_time"]
            else "напоминание не настроено"
        )
        lines.append(
            f"• {html.escape(row['full_name'])} — {html.escape(username)}\n"
            f"  <code>{row['telegram_id']}</code>, {html.escape(reminder)}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("my_sponsor"))
async def cmd_my_sponsor(message: Message) -> None:
    user = await get_user(message.from_user.id)
    if not user or not user.sponsor_id:
        await message.answer("Спонсор не подключён.")
        return
    sponsor = await get_user(user.sponsor_id)
    name = sponsor.full_name if sponsor else str(user.sponsor_id)
    await message.answer(f"Твои ответы отправляются: <b>{html.escape(name)}</b>.")


@router.message(Command("reminder"))
@router.message(F.text == "⏰ Настроить напоминание")
async def cmd_reminder(message: Message, state: FSMContext) -> None:
    await upsert_user(message)
    await state.set_state(ReminderForm.waiting_time)
    await message.answer(
        "Во сколько напоминать каждый вечер?\n\n"
        "Отправь время в формате <code>22:30</code>."
    )


@router.message(ReminderForm.waiting_time, F.text)
async def set_reminder_time(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    try:
        parsed = datetime.strptime(raw, "%H:%M")
    except ValueError:
        await message.answer("Не понял время. Пример правильного формата: <code>22:30</code>.")
        return

    normalized = parsed.strftime("%H:%M")
    user = await get_user(message.from_user.id)
    tz_name = user.timezone if user else DEFAULT_TIMEZONE
    await db_execute(
        """
        UPDATE users SET reminder_time=?, reminder_enabled=1, updated_at=?
        WHERE telegram_id=?
        """,
        (normalized, now_iso(), message.from_user.id),
    )
    await schedule_user_reminder(message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Напоминание установлено на <b>{normalized}</b>, "
        f"часовой пояс <code>{html.escape(tz_name)}</code>."
    )


def timezone_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tz, callback_data=f"tz:{tz}")]
            for tz in TZ_OPTIONS
        ]
    )


@router.message(Command("timezone"))
@router.message(F.text == "🕰 Часовой пояс")
async def cmd_timezone(message: Message) -> None:
    await upsert_user(message)
    await message.answer("Выбери часовой пояс:", reply_markup=timezone_keyboard())


@router.callback_query(F.data.startswith("tz:"))
async def callback_timezone(callback: CallbackQuery) -> None:
    tz_name = callback.data.removeprefix("tz:")
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        await callback.answer("Неизвестный часовой пояс", show_alert=True)
        return
    await db_execute(
        "UPDATE users SET timezone=?, updated_at=? WHERE telegram_id=?",
        (tz_name, now_iso(), callback.from_user.id),
    )
    await schedule_user_reminder(callback.from_user.id)
    await callback.answer("Часовой пояс сохранён")
    await callback.message.answer(f"✅ Часовой пояс: <code>{html.escape(tz_name)}</code>")


@router.message(Command("stop_reminder"))
async def cmd_stop_reminder(message: Message) -> None:
    await db_execute(
        "UPDATE users SET reminder_enabled=0, updated_at=? WHERE telegram_id=?",
        (now_iso(), message.from_user.id),
    )
    scheduler.remove_job(f"reminder:{message.from_user.id}") if scheduler.get_job(
        f"reminder:{message.from_user.id}"
    ) else None
    await message.answer("Ежедневное напоминание выключено.")


async def reminder_job(user_id: int) -> None:
    try:
        await bot.send_message(
            user_id,
            "🌙 Время спокойно пересмотреть прожитый день.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="Начать вечернюю часть", callback_data="start_evening")
                ]]
            ),
        )
    except Exception:
        logger.exception("Ошибка отправки напоминания пользователю %s", user_id)


@router.callback_query(F.data == "start_evening")
async def callback_start_evening(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await begin_evening(callback.message, state, user_id=callback.from_user.id)


async def schedule_user_reminder(user_id: int) -> None:
    job_id = f"reminder:{user_id}"
    existing = scheduler.get_job(job_id)
    if existing:
        scheduler.remove_job(job_id)

    user = await get_user(user_id)
    if not user or not user.reminder_enabled or not user.reminder_time:
        return

    hour, minute = map(int, user.reminder_time.split(":"))
    scheduler.add_job(
        reminder_job,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(user.timezone)),
        args=[user_id],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600,
    )


async def restore_reminders() -> None:
    rows = await db_fetchall(
        "SELECT telegram_id FROM users WHERE reminder_enabled=1 AND reminder_time IS NOT NULL"
    )
    for row in rows:
        await schedule_user_reminder(row["telegram_id"])


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    if message.from_user.id != OWNER_TELEGRAM_ID:
        await message.answer("Эта команда доступна владельцу бота.")
        return
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = await db_fetchall(
        """
        SELECT u.full_name, MAX(r.created_at) AS completed_at
        FROM users u
        LEFT JOIN reports r ON r.user_id=u.telegram_id AND r.created_at >= ?
        WHERE u.sponsor_id=?
        GROUP BY u.telegram_id, u.full_name
        ORDER BY u.full_name
        """,
        (start, message.from_user.id),
    )
    lines = ["<b>Вечерняя часть сегодня:</b>", ""]
    for row in rows:
        status = "✅ завершена" if row["completed_at"] else "— пока нет"
        lines.append(f"• {html.escape(row['full_name'])}: {status}")
    await message.answer("\n".join(lines))


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, bot: Bot) -> None:
    if message.from_user.id != OWNER_TELEGRAM_ID:
        await message.answer("Эта команда доступна владельцу бота.")
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer("Использование: <code>/broadcast текст сообщения</code>")
        return
    rows = await db_fetchall(
        "SELECT telegram_id FROM users WHERE sponsor_id=?",
        (message.from_user.id,),
    )
    sent = 0
    for row in rows:
        try:
            await bot.send_message(row["telegram_id"], html.escape(text))
            sent += 1
        except Exception:
            logger.exception("Не удалось отправить рассылку %s", row["telegram_id"])
    await message.answer(f"Отправлено: {sent}.")


@router.message(Command("delete_me"))
async def cmd_delete_me(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db_execute("DELETE FROM reports WHERE user_id=?", (message.from_user.id,))
    await db_execute("DELETE FROM users WHERE telegram_id=?", (message.from_user.id,))
    job_id = f"reminder:{message.from_user.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    await message.answer("Твой профиль, настройки и сохранённые ответы удалены.")


async def cleanup_old_reports() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    await db_execute("DELETE FROM reports WHERE created_at < ?", (cutoff,))


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="evening", description="Начать вечернюю часть"),
            BotCommand(command="reminder", description="Настроить напоминание"),
            BotCommand(command="timezone", description="Выбрать часовой пояс"),
            BotCommand(command="stop_reminder", description="Выключить напоминание"),
            BotCommand(command="my_sponsor", description="Кому отправляются ответы"),
            BotCommand(command="cancel", description="Отменить текущий опрос"),
            BotCommand(command="delete_me", description="Удалить свои данные"),
        ]
    )


async def main() -> None:
    global bot
    await init_db()
    bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    scheduler.add_job(
        cleanup_old_reports,
        trigger=CronTrigger(hour=4, minute=10, timezone=timezone.utc),
        id="cleanup",
        replace_existing=True,
    )
    scheduler.start()
    await restore_reminders()
    await set_commands(bot)

    logger.info("EveryEveningBot запущен")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
