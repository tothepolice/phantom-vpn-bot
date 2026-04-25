import asyncio
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

UTC = timezone.utc
DB_PATH = os.getenv("DB_PATH", "./vpn_bot.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
ADDITIONAL_ADMIN_IDS = {321238123}
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/IceFallVPN")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/IceFallVPNSupport")
STARS_PROVIDER_TOKEN = os.getenv("STARS_PROVIDER_TOKEN", "")
START_IMAGE_PATH = os.getenv(
    "START_IMAGE_PATH",
    "/Users/macbook/Desktop/фантом 1_upscayl_4x_upscayl-lite-4x.png",
)

LOCATIONS = [
    "🇱🇻 Латвия, Рига",
    "🇳🇱 Нидерланды, Амстердам",
    "🇩🇪 Германия, Франкфурт",
    "🇸🇪 Швеция, Стокгольм",
    "🇫🇮 Финляндия, Хельсинки",
    "🇵🇱 Польша, Вышкув",
    "🇺🇸 США, Лос-Анджелес",
    "🇹🇷 Турция, Стамбул",
    "🇷🇺 Россия, Санкт-Петербург",
    "🇷🇺 Россия, Москва",
]

PLAN_MAP = {
    "plan_30": ("1 месяц", 30, 200),
    "plan_90": ("3 месяца", 90, 500),
    "plan_180": ("6 месяцев", 180, 1000),
    "plan_365": ("1 Год", 365, 1900),
}

PAYMENT_METHODS = {
    "pay_sbp": "СБП",
    "pay_crypto": "Crypto Bot",
}

router = Router()


@dataclass
class User:
    id: int
    tg_id: int
    ref_code: str
    referred_by: Optional[int]
    balance_rub: int
    test_subscribed: int
    email: Optional[str]


class TopUpState(StatesGroup):
    waiting_amount = State()
    waiting_sbp_email = State()
    waiting_profile_email = State()


class NewsState(StatesGroup):
    waiting_message = State()
    waiting_confirm = State()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                email TEXT,
                ref_code TEXT NOT NULL UNIQUE,
                referred_by INTEGER,
                balance_rub INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(referred_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_key TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                amount_rub INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS referral_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_user_id INTEGER NOT NULL,
                referred_user_id INTEGER NOT NULL,
                reward_days INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(referrer_user_id, referred_user_id, reason),
                FOREIGN KEY(referrer_user_id) REFERENCES users(id),
                FOREIGN KEY(referred_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS balance_topups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_rub INTEGER NOT NULL,
                payment_method TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )

        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "balance_rub" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN balance_rub INTEGER NOT NULL DEFAULT 0")
        if "test_subscribed" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN test_subscribed INTEGER NOT NULL DEFAULT 0")
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")

def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def base36(num: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    out = []
    n = abs(num)
    while n:
        n, r = divmod(n, 36)
        out.append(alphabet[r])
    return "".join(reversed(out))


def make_ref_code(tg_id: int) -> str:
    return f"ice{base36(tg_id)}"


def upsert_user(msg: Message, referred_by_code: Optional[str]) -> User:
    with connect_db() as conn:
        tg_id = msg.from_user.id
        ref_code = make_ref_code(tg_id)
        conn.execute(
            """
            INSERT INTO users (tg_id, username, first_name, ref_code, referred_by, created_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (
                tg_id,
                msg.from_user.username,
                msg.from_user.first_name,
                ref_code,
                now_iso(),
            ),
        )

        row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        user = User(
            id=row["id"],
            tg_id=row["tg_id"],
            ref_code=row["ref_code"],
            referred_by=row["referred_by"],
            balance_rub=row["balance_rub"] or 0,
            test_subscribed=row["test_subscribed"] or 0,
            email=row["email"],
        )

        if referred_by_code and user.referred_by is None and referred_by_code != user.ref_code:
            ref_user = conn.execute(
                "SELECT id FROM users WHERE ref_code = ?", (referred_by_code,)
            ).fetchone()
            if ref_user:
                conn.execute(
                    "UPDATE users SET referred_by = ? WHERE id = ?",
                    (ref_user["id"], user.id),
                )
                user.referred_by = ref_user["id"]

        return user


def get_user_by_tg_id(tg_id: int) -> Optional[User]:
    with connect_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        if not row:
            return None
        return User(
            id=row["id"],
            tg_id=row["tg_id"],
            ref_code=row["ref_code"],
            referred_by=row["referred_by"],
            balance_rub=row["balance_rub"] or 0,
            test_subscribed=row["test_subscribed"] or 0,
            email=row["email"],
        )


def is_admin(user_id: int) -> bool:
    if user_id in ADDITIONAL_ADMIN_IDS:
        return True
    return bool(ADMIN_CHAT_ID and user_id == ADMIN_CHAT_ID)


def toggle_test_subscribed(tg_id: int) -> int:
    with connect_db() as conn:
        row = conn.execute(
            "SELECT test_subscribed FROM users WHERE tg_id = ?",
            (tg_id,),
        ).fetchone()
        current = int(row["test_subscribed"] or 0) if row else 0
        new_value = 0 if current else 1
        conn.execute(
            "UPDATE users SET test_subscribed = ? WHERE tg_id = ?",
            (new_value, tg_id),
        )
        return new_value


def set_user_email(tg_id: int, email: str) -> None:
    with connect_db() as conn:
        conn.execute(
            "UPDATE users SET email = ? WHERE tg_id = ?",
            (email, tg_id),
        )


def get_all_user_tg_ids() -> list[int]:
    with connect_db() as conn:
        rows = conn.execute("SELECT tg_id FROM users ORDER BY id").fetchall()
    return [int(row["tg_id"]) for row in rows]


def extend_subscription(user_id: int, days: int, kind: str, source: str) -> tuple[datetime, datetime]:
    with connect_db() as conn:
        row = conn.execute(
            """
            SELECT end_at FROM subscriptions
            WHERE user_id = ?
            ORDER BY end_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        now = datetime.now(UTC)
        start_at = now
        if row:
            candidate = parse_iso(row["end_at"])
            if candidate > now:
                start_at = candidate

        end_at = start_at + timedelta(days=days)
        conn.execute(
            """
            INSERT INTO subscriptions (user_id, start_at, end_at, kind, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, start_at.isoformat(), end_at.isoformat(), kind, source, now_iso()),
        )
        return start_at, end_at


def get_active_subscription(user_id: int) -> Optional[sqlite3.Row]:
    with connect_db() as conn:
        now = now_iso()
        return conn.execute(
            """
            SELECT * FROM subscriptions
            WHERE user_id = ? AND end_at > ?
            ORDER BY end_at DESC
            LIMIT 1
            """,
            (user_id, now),
        ).fetchone()


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Моя подписка",
                    icon_custom_emoji_id="6005570495603282482",
                    callback_data="my_sub",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Тарифы",
                    icon_custom_emoji_id="5776424837786374634",
                    callback_data="buy",
                ),
                InlineKeyboardButton(
                    text="Профиль",
                    icon_custom_emoji_id="6035084557378654059",
                    callback_data="profile",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="О сервисе",
                    icon_custom_emoji_id="5879785854284599288",
                    callback_data="about_service",
                )
            ],
        ]
    )


def plans_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, (name, _, price) in PLAN_MAP.items():
        kb.button(text=f"{name} — {price}₽", callback_data=f"{key}")
    kb.button(
        text="Назад",
        callback_data="back",
        icon_custom_emoji_id="5447389832781264371",
    )
    kb.adjust(1)
    return kb.as_markup()


def payment_methods_kb(plan_key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="СБП",
        icon_custom_emoji_id="5368446439800197476",
        callback_data=f"pay_sbp:{plan_key}",
    )
    kb.button(
        text="Crypto Bot",
        icon_custom_emoji_id="5201996850554486374",
        callback_data=f"pay_crypto:{plan_key}",
    )
    kb.button(
        text="Назад",
        callback_data="buy",
        icon_custom_emoji_id="5447389832781264371",
    )
    kb.adjust(1)
    return kb.as_markup()


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5447389832781264371",
                    callback_data="back",
                )
            ]
        ]
    )


def profile_kb(has_email: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Пополнить баланс",
                icon_custom_emoji_id="5927169041595634481",
                callback_data="balance_topup",
            )
        ]
    ]
    if has_email:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Изменить E - mail",
                    icon_custom_emoji_id="5845943483382110702",
                    callback_data="change_email",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                icon_custom_emoji_id="5447389832781264371",
                callback_data="back",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_sub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пополнить баланс",
                    icon_custom_emoji_id="5927169041595634481",
                    callback_data="balance_topup",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Инструкция",
                    icon_custom_emoji_id="6032742198179532882",
                    url="https://t.me/io_phantom/32",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5447389832781264371",
                    callback_data="back",
                )
            ],
        ]
    )


def no_sub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 Месяц - 149₽", callback_data="plan_30")],
            [InlineKeyboardButton(text="3 Месяца - 399₽", callback_data="plan_90")],
            [InlineKeyboardButton(text="6 Месяцев - 749₽", callback_data="plan_180")],
            [InlineKeyboardButton(text="1 Год - 1399₽", callback_data="plan_365")],
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5447389832781264371",
                    callback_data="back",
                )
            ],
        ]
    )


def tariffs_text() -> str:
    return (
        "<tg-emoji emoji-id=\"5399898266265475100\">🌐</tg-emoji> <b>Доступные страны:</b>\n\n"
        "<tg-emoji emoji-id=\"5409360418520967565\">🇩🇪</tg-emoji> Германия\n"
        "<tg-emoji emoji-id=\"5411124743841524806\">🇳🇱</tg-emoji> Нидерланды\n"
        "<tg-emoji emoji-id=\"5291847690940852675\">🇵🇱</tg-emoji> Польша\n"
        "<tg-emoji emoji-id=\"6026257901369168205\">🇺🇸</tg-emoji> США\n"
        "<tg-emoji emoji-id=\"5202196682497859879\">🇬🇧</tg-emoji> Великобритания\n"
        "<tg-emoji emoji-id=\"5269650286342846979\">🇱🇻</tg-emoji> Латвия\n\n"
        "<b>Выберите срок подписки</b>"
    )


def topup_methods_kb(amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="СБП",
                    icon_custom_emoji_id="5368446439800197476",
                    callback_data=f"topup_sbp:{amount}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Crypto Bot",
                    icon_custom_emoji_id="5201996850554486374",
                    callback_data=f"topup_crypto:{amount}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5447389832781264371",
                    callback_data="balance_topup",
                )
            ],
        ]
    )


def topup_email_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5447389832781264371",
                    callback_data="balance_topup",
                )
            ]
        ]
    )


def about_service_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Наш канал",
                    icon_custom_emoji_id="5875465628285931233",
                    url="https://t.me/io_phantom",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Поддержка",
                    icon_custom_emoji_id="5870755659774955152",
                    url="https://t.me/io_phantom?direct",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5447389832781264371",
                    callback_data="back",
                )
            ],
        ]
    )


def news_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отправить", callback_data="news_send")],
            [InlineKeyboardButton(text="Отмена", callback_data="news_cancel")],
        ]
    )


def news_intro_text() -> str:
    return (
        "<tg-emoji emoji-id=\"5370869711888194012\">📣</tg-emoji> Режим рассылки.\n"
        "<tg-emoji emoji-id=\"5424892643760937442\">📝</tg-emoji> Отправьте одним сообщением пост для рассылки:\n"
        "<tg-emoji emoji-id=\"5465300082628763143\">✍️</tg-emoji> Текст\n"
        "<tg-emoji emoji-id=\"5431456208487716895\">🖼️</tg-emoji> Файл / картинка / фото\n"
        "<tg-emoji emoji-id=\"5472009457200277262\">🔘</tg-emoji> Кнопки (если нужны)"
    )


def start_text() -> str:
    return (
        "<tg-emoji emoji-id=\"5418115103763494597\">👋</tg-emoji> Добро пожаловать в Phantom VPN!\n\n"
        "<tg-emoji emoji-id=\"5276089339967716971\">🛡</tg-emoji> Там, где блокируют новости и социальные сети, мы <b>открываем дорогу</b> к свободной информации и общению.\n\n"
        "<tg-emoji emoji-id=\"5399898266265475100\">🌐</tg-emoji> Стабильное и безопасное подключение к <b>Instagram</b>, <b>YouTube</b>, <b>Telegram</b>, <b>ChatGPT</b> и многим другим сервисам и услугам.\n\n"
        "<tg-emoji emoji-id=\"5339181821135431228\">⚡</tg-emoji> <b>Быстрая</b> настройка и доступ на всех платформах: Windows, macOS, iOS, Android.\n\n"
        "<tg-emoji emoji-id=\"5472100935708711380\">🚀</tg-emoji> <b>Высокая</b> скорость соединения и возможность посещения любых сайтов.\n\n"
        "<tg-emoji emoji-id=\"6257785230220858438\">📣</tg-emoji> "
        "<a href=\"https://t.me/io_phantom\">Наш канал</a>\n"
        "<tg-emoji emoji-id=\"5445128296276718145\">💬</tg-emoji> "
        "<a href=\"https://t.me/io_phantom?direct\">Поддержка</a>"
    )


async def send_start_card(message: Message) -> None:
    if os.path.exists(START_IMAGE_PATH):
        await message.answer_photo(
            photo=FSInputFile(START_IMAGE_PATH),
            caption=start_text(),
            reply_markup=main_menu_kb(),
        )
        return
    await message.answer(start_text(), reply_markup=main_menu_kb(), disable_web_page_preview=True)


async def send_start_card_to_chat(bot: Bot, chat_id: int) -> None:
    if os.path.exists(START_IMAGE_PATH):
        await bot.send_photo(
            chat_id=chat_id,
            photo=FSInputFile(START_IMAGE_PATH),
            caption=start_text(),
            reply_markup=main_menu_kb(),
        )
        return
    await bot.send_message(
        chat_id=chat_id,
        text=start_text(),
        reply_markup=main_menu_kb(),
        disable_web_page_preview=True,
    )


async def show_text_page(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    msg = callback.message
    if not msg:
        return
    if msg.photo:
        await msg.delete()
        await callback.bot.send_message(
            callback.from_user.id,
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return
    await msg.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)


@router.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject, bot: Bot) -> None:
    referral = None
    if command.args and command.args.startswith("ref_"):
        referral = command.args.removeprefix("ref_").strip()

    upsert_user(message, referral)
    await send_start_card(message)


@router.message(Command("menu"))
async def menu_cmd(message: Message) -> None:
    await send_start_card(message)


@router.message(Command("news"))
async def news_cmd(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await state.set_state(NewsState.waiting_message)
    await message.answer(news_intro_text())


@router.message(NewsState.waiting_message)
async def news_draft_received(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if (message.text or "").strip().startswith("/"):
        await message.answer("Пришлите контент поста, а не команду.")
        return

    await state.update_data(
        news_chat_id=message.chat.id,
        news_message_id=message.message_id,
    )
    await state.set_state(NewsState.waiting_confirm)
    await message.answer(
        "<tg-emoji emoji-id=\"5431449001532594346\">🦆</tg-emoji> Отправить всем пользователям?",
        reply_markup=news_confirm_kb(),
    )


@router.callback_query(F.data == "news_cancel")
async def news_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewsState.waiting_message)
    if callback.message:
        await callback.message.edit_text(news_intro_text())
    await callback.answer()


@router.callback_query(F.data == "news_send")
async def news_send(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    data = await state.get_data()
    from_chat_id = data.get("news_chat_id")
    message_id = data.get("news_message_id")
    if not from_chat_id or not message_id:
        await callback.answer("Черновик не найден", show_alert=True)
        return

    if callback.message:
        await callback.message.edit_text("Запускаю рассылку...")

    recipients = get_all_user_tg_ids()
    total = len(recipients)
    ok = 0
    blocked = 0
    failed = 0

    for tg_id in recipients:
        try:
            await callback.bot.copy_message(
                chat_id=tg_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            ok += 1
        except TelegramForbiddenError:
            blocked += 1
        except TelegramBadRequest:
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await state.clear()
    report = (
        "Рассылка завершена.\n"
        f"Всего: {total}\n"
        f"Успешно: {ok}\n"
        f"Заблокировали бота: {blocked}\n"
        f"Ошибки: {failed}"
    )
    await callback.bot.send_message(callback.from_user.id, report)
    await callback.answer()


@router.callback_query(F.data == "back")
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.delete()
    await send_start_card_to_chat(callback.bot, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "buy")
async def buy_sub(callback: CallbackQuery) -> None:
    await show_text_page(callback, tariffs_text(), plans_kb())
    await callback.answer()


@router.callback_query(F.data.in_(PLAN_MAP.keys()))
async def plan_selected(callback: CallbackQuery) -> None:
    plan_key = callback.data
    name, _, price = PLAN_MAP[plan_key]
    await show_text_page(
        callback,
        (
            "<tg-emoji emoji-id=\"5927169041595634481\">💳</tg-emoji> <b>Оплата подписки</b>\n\n"
            f"<tg-emoji emoji-id=\"5776213190387961618\">🕓</tg-emoji> Тариф: {name}\n"
            f"<tg-emoji emoji-id=\"5967390100357648692\">💵</tg-emoji> Стоимость: {price}₽\n\n"
            "После оплаты подписка оформится автоматически.\n"
            "Выберите способ оплаты:"
        ),
        payment_methods_kb(plan_key),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_"))
async def payment_method_selected(callback: CallbackQuery, bot: Bot) -> None:
    user = get_user_by_tg_id(callback.from_user.id)
    if not user:
        upsert_user(callback.message, None)
        user = get_user_by_tg_id(callback.from_user.id)

    method_code, plan_key = callback.data.split(":", 1)
    method_title = PAYMENT_METHODS.get(method_code)
    if plan_key not in PLAN_MAP or not method_title:
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    plan_name, days, amount = PLAN_MAP[plan_key]

    order_id = create_order(user.id, plan_key, method_code, amount)
    text = (
        f"Заявка #{order_id} создана.\n"
        f"Тариф: {plan_name} ({days} дн.)\n"
        f"Оплата: {method_title}\n"
        f"Сумма: {amount}₽\n\n"
        f"Для завершения оплаты напишите в поддержку: {SUPPORT_URL}"
    )
    await show_text_page(callback, text, back_kb())

    if ADMIN_CHAT_ID:
        await bot.send_message(
            ADMIN_CHAT_ID,
            (
                f"🧾 Новый заказ #{order_id}\n"
                f"User: {callback.from_user.id} @{callback.from_user.username or '-'}\n"
                f"Тариф: {plan_name}\n"
                f"Оплата: {method_title}\n"
                f"Сумма: {amount}₽"
            ),
        )

    await callback.answer()


@router.callback_query(F.data == "my_sub")
async def my_sub(callback: CallbackQuery) -> None:
    user = get_user_by_tg_id(callback.from_user.id)
    if not user:
        await callback.answer("Нажмите /start", show_alert=True)
        return

    if user.test_subscribed:
        text = (
            "<tg-emoji emoji-id=\"5332724926216428039\">🔐</tg-emoji> <b>Моя подписка</b>\n\n"
            "<tg-emoji emoji-id=\"6005570495603282482\">✅</tg-emoji> Статус: Активна\n"
            "<tg-emoji emoji-id=\"5936130851635990622\">📅</tg-emoji> Действует до: 27.04.2026\n"
            "<tg-emoji emoji-id=\"5994473545650934240\">📅</tg-emoji> Осталось: 3 дн.\n\n"
            "<tg-emoji emoji-id=\"5877465816030515018\">🔗</tg-emoji> Ссылка на подписку:\n"
            "<code>https://sub.aww.ink/r4bV6TwJj-W5F0Ym</code>\n\n"
            "<tg-emoji emoji-id=\"5879585266426973039\">📊</tg-emoji> Трафик: Безлимитный"
        )
        await show_text_page(callback, text, my_sub_kb())
        await callback.answer()
        return

    text = (
        "<tg-emoji emoji-id=\"5420282000663669874\">✖</tg-emoji> У вас нет подписки\n\n"
        f"{tariffs_text()}"
    )
    await show_text_page(callback, text, plans_kb())
    await callback.answer()


@router.callback_query(F.data == "locations")
async def locations(callback: CallbackQuery) -> None:
    lines = "\n".join(f"• {item}" for item in LOCATIONS)
    await show_text_page(
        callback,
        f"<b>Текущие локации:</b>\n{lines}\n\n❗️После изменений обновляйте подписку в приложении.",
        back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "guide")
async def guide(callback: CallbackQuery) -> None:
    text = (
        "<b>Инструкция</b>\n"
        "1. Нажмите «Купить подписку» и выберите тариф.\n"
        "2. Оплатите удобным способом.\n"
        "3. После подтверждения получите конфиг/доступ.\n"
        "4. Если не работает, обновите подписку в приложении."
    )
    await show_text_page(callback, text, back_kb())
    await callback.answer()


@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery) -> None:
    user = get_user_by_tg_id(callback.from_user.id)
    if not user:
        await callback.answer("Нажмите /start", show_alert=True)
        return

    text = (
        "<tg-emoji emoji-id=\"5879770735999717115\">👤</tg-emoji> <b>Профиль</b>\n\n"
        f"<tg-emoji emoji-id=\"5936017305585586269\">🆔</tg-emoji> ID: <code>{callback.from_user.id}</code>\n"
        f"<tg-emoji emoji-id=\"5771887475421090729\">👤</tg-emoji> Username: @{callback.from_user.username or '-'}\n"
        f"<tg-emoji emoji-id=\"5769403330761593044\">💰</tg-emoji> Баланс: {user.balance_rub}₽"
    )
    if user.email:
        text += (
            "\n\n"
            f"<tg-emoji emoji-id=\"5967280668885913944\">✉️</tg-emoji> E-mail: {user.email}"
        )
    await show_text_page(callback, text, profile_kb(bool(user.email)))
    await callback.answer()


@router.callback_query(F.data == "change_email")
async def change_email(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUpState.waiting_profile_email)
    text = (
        "<tg-emoji emoji-id=\"5444856076954520455\">🧾</tg-emoji> "
        "Укажите новый адрес электронной почты"
    )
    await show_text_page(callback, text, topup_email_kb())
    await callback.answer()


@router.callback_query(F.data == "balance_topup")
async def balance_topup(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUpState.waiting_amount)
    text = (
        "<tg-emoji emoji-id=\"5927169041595634481\">💳</tg-emoji> <b>Пополнение баланса</b>\n\n"
        "<tg-emoji emoji-id=\"5967390100357648692\">💵</tg-emoji> Введите сумму пополнения:"
    )
    await show_text_page(callback, text, back_kb())
    await callback.answer()


@router.message(TopUpState.waiting_amount)
async def topup_amount_input(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("Введите сумму числом, например: 100")
        return

    amount = int(raw)
    if amount <= 0:
        await message.answer("Сумма должна быть больше 0")
        return

    await state.clear()
    text = (
        "<tg-emoji emoji-id=\"5927169041595634481\">💳</tg-emoji> <b>Пополнение баланса</b>\n\n"
        f"<tg-emoji emoji-id=\"5967390100357648692\">💵</tg-emoji> Сумма к оплате: <b>{amount}₽</b>\n\n"
        "Выберите способ оплаты:"
    )
    await message.answer(text, reply_markup=topup_methods_kb(amount))


def create_balance_topup(user_id: int, amount: int, method: str) -> int:
    with connect_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO balance_topups (user_id, amount_rub, payment_method, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (user_id, amount, method, now_iso()),
        )
        return int(cur.lastrowid)


@router.callback_query(F.data.startswith("topup_"))
async def topup_method_selected(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    user = get_user_by_tg_id(callback.from_user.id)
    if not user:
        upsert_user(callback.message, None)
        user = get_user_by_tg_id(callback.from_user.id)

    method_code, raw_amount = callback.data.split(":", 1)
    if not raw_amount.isdigit():
        await callback.answer("Некорректная сумма", show_alert=True)
        return
    amount = int(raw_amount)
    if amount <= 0:
        await callback.answer("Некорректная сумма", show_alert=True)
        return

    method_title = "СБП" if method_code == "topup_sbp" else "Crypto Bot"
    if method_code == "topup_sbp":
        if user.email:
            topup_id = create_balance_topup(user.id, amount, "СБП")
            text = (
                f"Заявка на пополнение #{topup_id} создана.\n"
                f"Сумма: {amount}₽\n"
                "Способ оплаты: СБП\n"
                f"E-mail для чека: {user.email}\n\n"
                "Для завершения оплаты напишите в поддержку: https://t.me/dovko"
            )
            await show_text_page(callback, text, back_kb())
            if ADMIN_CHAT_ID:
                await bot.send_message(
                    ADMIN_CHAT_ID,
                    (
                        f"💳 Новое пополнение #{topup_id}\n"
                        f"User: {callback.from_user.id} @{callback.from_user.username or '-'}\n"
                        f"Сумма: {amount}₽\n"
                        "Способ: СБП\n"
                        f"E-mail: {user.email}"
                    ),
                )
            await callback.answer()
            return

        await state.set_state(TopUpState.waiting_sbp_email)
        await state.update_data(topup_amount=amount)
        text = (
            "<tg-emoji emoji-id=\"5444856076954520455\">🧾</tg-emoji> "
            "Укажите адрес электронной почты для отправки чека"
        )
        await show_text_page(callback, text, topup_email_kb())
        await callback.answer()
        return

    topup_id = create_balance_topup(user.id, amount, method_title)

    text = (
        f"Заявка на пополнение #{topup_id} создана.\n"
        f"Сумма: {amount}₽\n"
        f"Способ оплаты: {method_title}\n\n"
        "Для завершения оплаты напишите в поддержку: https://t.me/dovko"
    )
    await show_text_page(callback, text, back_kb())

    if ADMIN_CHAT_ID:
        await bot.send_message(
            ADMIN_CHAT_ID,
            (
                f"💳 Новое пополнение #{topup_id}\n"
                f"User: {callback.from_user.id} @{callback.from_user.username or '-'}\n"
                f"Сумма: {amount}₽\n"
                f"Способ: {method_title}"
            ),
        )

    await callback.answer()


@router.message(TopUpState.waiting_sbp_email)
async def topup_sbp_email_input(message: Message, state: FSMContext, bot: Bot) -> None:
    email = (message.text or "").strip()
    if not re.fullmatch(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        await message.answer("Введите корректный e-mail, например: name@example.com")
        return

    data = await state.get_data()
    amount = int(data.get("topup_amount") or 0)
    if amount <= 0:
        await state.clear()
        await message.answer("Сумма не найдена. Нажмите «Пополнить баланс» еще раз.")
        return

    user = get_user_by_tg_id(message.from_user.id)
    if not user:
        upsert_user(message, None)
        user = get_user_by_tg_id(message.from_user.id)

    set_user_email(message.from_user.id, email)
    topup_id = create_balance_topup(user.id, amount, "СБП")
    await state.clear()

    text = (
        f"Заявка на пополнение #{topup_id} создана.\n"
        f"Сумма: {amount}₽\n"
        "Способ оплаты: СБП\n"
        f"E-mail для чека: {email}\n\n"
        "Для завершения оплаты напишите в поддержку: https://t.me/dovko"
    )
    await message.answer(text, reply_markup=back_kb())

    if ADMIN_CHAT_ID:
        await bot.send_message(
            ADMIN_CHAT_ID,
            (
                f"💳 Новое пополнение #{topup_id}\n"
                f"User: {message.from_user.id} @{message.from_user.username or '-'}\n"
                f"Сумма: {amount}₽\n"
                "Способ: СБП\n"
                f"E-mail: {email}"
            ),
        )


@router.message(TopUpState.waiting_profile_email)
async def profile_email_input(message: Message, state: FSMContext) -> None:
    email = (message.text or "").strip()
    if not re.fullmatch(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        await message.answer("Введите корректный e-mail, например: name@example.com")
        return

    set_user_email(message.from_user.id, email)
    await state.clear()
    await message.answer(
        "<tg-emoji emoji-id=\"5420190616644513892\">✅</tg-emoji> E-mail обновлен.",
        reply_markup=back_kb(),
    )


@router.callback_query(F.data == "about_service")
async def about_service(callback: CallbackQuery) -> None:
    text = (
        "<tg-emoji emoji-id=\"5420596907665817195\">ℹ️</tg-emoji> "
        "Phantom VPN — современный сервис для безопасного и анонимного доступа в Интернет.\n\n"
        "<tg-emoji emoji-id=\"5420317404079090840\">🛡️</tg-emoji> "
        "Мы используем современные технологии обхода блокировок, чтобы Вы могли свободно пользоваться любимыми сайтами, сервисами и приложениями.\n\n"
        "<tg-emoji emoji-id=\"5417834766953113073\">📢</tg-emoji> "
        "Подписывайтесь на наш канал, чтобы не пропустить важные новости и обновления!\n\n"
        "<tg-emoji emoji-id=\"5420252773411220019\">💬</tg-emoji> "
        "Возникли вопросы или проблемы? Напишите в поддержку — мы поможем в любое время!"
    )
    await show_text_page(callback, text, about_service_kb())
    await callback.answer()


@router.callback_query(F.data == "have_sub")
async def have_sub(callback: CallbackQuery) -> None:
    text = (
        "<tg-emoji emoji-id=\"5332724926216428039\">🔐</tg-emoji> <b>Моя подписка</b>\n\n"
        "<tg-emoji emoji-id=\"6005570495603282482\">✅</tg-emoji> Статус: Активна\n"
        "<tg-emoji emoji-id=\"5936130851635990622\">📅</tg-emoji> Действует до: 27.04.2026\n"
        "<tg-emoji emoji-id=\"5994473545650934240\">📅</tg-emoji> Осталось: 3 дн.\n\n"
        "<tg-emoji emoji-id=\"5877465816030515018\">🔗</tg-emoji> Ссылка на подписку:\n"
        "<code>https://sub.aww.ink/r4bV6TwJj-W5F0Ym</code>\n\n"
        "<tg-emoji emoji-id=\"5879585266426973039\">📊</tg-emoji> Трафик: Безлимитный"
    )
    await show_text_page(callback, text, back_kb())
    await callback.answer()


@router.callback_query(F.data == "ref")
async def referral_info(callback: CallbackQuery, bot: Bot) -> None:
    user = get_user_by_tg_id(callback.from_user.id)
    if not user:
        await callback.answer("Нажмите /start", show_alert=True)
        return

    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{user.ref_code}" if me.username else ""
    total_refs, paid_refs = get_ref_stats(user.id)

    text = (
        "<b>Реферальная система</b>\n"
        f"Всего приглашено: {total_refs}\n"
        f"Оплативших: {paid_refs}\n"
        "Награда: +3 дня за каждого оплатившего реферала.\n\n"
    )
    if ref_link:
        text += f"Ваша ссылка:\n<code>{ref_link}</code>"

    await show_text_page(callback, text, back_kb())
    await callback.answer()


def get_ref_stats(user_id: int) -> tuple[int, int]:
    with connect_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE referred_by = ?", (user_id,)
        ).fetchone()["c"]
        paid = conn.execute(
            """
            SELECT COUNT(DISTINCT u.id) c
            FROM users u
            JOIN subscriptions s ON s.user_id = u.id
            WHERE u.referred_by = ? AND s.kind = 'paid'
            """,
            (user_id,),
        ).fetchone()["c"]
    return total, paid


def create_order(user_id: int, plan_key: str, payment_method: str, amount: int) -> int:
    with connect_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders (user_id, plan_key, payment_method, amount_rub, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (user_id, plan_key, payment_method, amount, now_iso()),
        )
        return int(cur.lastrowid)


@router.message(Command("approve"))
async def approve_payment(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /approve <order_id>")
        return

    try:
        order_id = int(command.args.strip())
    except ValueError:
        await message.answer("order_id должен быть числом")
        return

    with connect_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            await message.answer("Заказ не найден")
            return
        if order["status"] != "pending":
            await message.answer("Заказ уже обработан")
            return

        plan_name, days, _ = PLAN_MAP[order["plan_key"]]
        _, end_at = extend_subscription(order["user_id"], days, "paid", f"order:{order_id}")
        conn.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))

    await message.answer(f"Заказ #{order_id} подтвержден. Подписка до {fmt_dt(end_at)}")


@router.message(Command("reject"))
async def reject_payment(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /reject <order_id>")
        return

    try:
        order_id = int(command.args.strip())
    except ValueError:
        await message.answer("order_id должен быть числом")
        return

    with connect_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            await message.answer("Заказ не найден")
            return
        if order["status"] != "pending":
            await message.answer("Заказ уже обработан")
            return
        conn.execute("UPDATE orders SET status = 'rejected' WHERE id = ?", (order_id,))

    await message.answer(f"Заказ #{order_id} отклонен")


@router.message(Command("giftref"))
async def reward_referrals(message: Message, command: CommandObject, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return

    reward_days = 3
    if command.args:
        try:
            reward_days = int(command.args.strip())
        except ValueError:
            await message.answer("Использование: /giftref [days]")
            return

    rewarded = 0
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT o.id AS order_id, u.id AS user_id, u.referred_by AS referrer_id
            FROM orders o
            JOIN users u ON u.id = o.user_id
            WHERE o.status = 'paid' AND u.referred_by IS NOT NULL
            """
        ).fetchall()

        for row in rows:
            exists = conn.execute(
                """
                SELECT 1 FROM referral_rewards
                WHERE referrer_user_id = ? AND referred_user_id = ? AND reason = ?
                """,
                (row["referrer_id"], row["user_id"], f"order:{row['order_id']}"),
            ).fetchone()
            if exists:
                continue

            conn.execute(
                """
                INSERT INTO referral_rewards
                (referrer_user_id, referred_user_id, reward_days, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["referrer_id"],
                    row["user_id"],
                    reward_days,
                    f"order:{row['order_id']}",
                    now_iso(),
                ),
            )
            extend_subscription(
                row["referrer_id"], reward_days, "referral", f"order:{row['order_id']}"
            )
            rewarded += 1

    await message.answer(f"Выдано реферальных наград: {rewarded}")


@router.message(Command("test_subscribed"))
async def test_subscribed_toggle(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    user = get_user_by_tg_id(message.from_user.id)
    if not user:
        upsert_user(message, None)

    new_value = toggle_test_subscribed(message.from_user.id)
    if new_value:
        await message.answer("Тестовый статус: подписчик включен.")
        return
    await message.answer("Тестовый статус: подписчик выключен.")


def validate_env() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN")


async def main() -> None:
    validate_env()
    init_db()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
