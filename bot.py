"""
Kirim-chiqim hisobini yurituvchi Telegram bot.
Ishga tushirish: python3 bot.py
Token BOT_TOKEN muhit o'zgaruvchisidan yoki config.py fayldan olinadi.
"""
import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

import database as db
import sheets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Sozlamalar ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

if not BOT_TOKEN or not GOOGLE_SHEET_ID:
    try:
        import config
        BOT_TOKEN = BOT_TOKEN or getattr(config, "BOT_TOKEN", "")
        GOOGLE_CREDENTIALS_FILE = getattr(config, "GOOGLE_CREDENTIALS_FILE", GOOGLE_CREDENTIALS_FILE)
        GOOGLE_CREDENTIALS_JSON = GOOGLE_CREDENTIALS_JSON or getattr(config, "GOOGLE_CREDENTIALS_JSON", "")
        GOOGLE_SHEET_ID = GOOGLE_SHEET_ID or getattr(config, "GOOGLE_SHEET_ID", "")
    except ImportError:
        pass

BTN_INCOME = "➕ Kirim qo'shish"
BTN_EXPENSE = "➖ Chiqim qo'shish"
BTN_BALANCE = "💰 Balans"
BTN_HISTORY = "📜 Tarix"
BTN_UNDO = "🗑 Oxirgisini o'chirish"
BTN_CANCEL = "❌ Bekor qilish"

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_INCOME), KeyboardButton(text=BTN_EXPENSE)],
        [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_HISTORY)],
        [KeyboardButton(text=BTN_UNDO)],
    ],
    resize_keyboard=True,
)

cancel_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
    resize_keyboard=True,
)


class AddTransaction(StatesGroup):
    waiting_for_amount = State()


AMOUNT_RE = re.compile(r"^\s*([\d\s.,]+)\s*(.*)$")


def parse_amount_and_note(text: str) -> tuple[float, str] | None:
    """'50000 oylik maosh' -> (50000.0, 'oylik maosh'). Notogri bo'lsa None."""
    match = AMOUNT_RE.match(text)
    if not match:
        return None
    raw_amount = match.group(1).replace(" ", "").replace(",", "")
    note = match.group(2).strip()
    try:
        amount = float(raw_amount)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount, note


def format_money(amount: float) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def format_balance(bal: dict) -> str:
    return (
        f"💰 <b>Balans</b>\n\n"
        f"➕ Jami kirim: {format_money(bal['income'])} so'm\n"
        f"➖ Jami chiqim: {format_money(bal['expense'])} so'm\n"
        f"—————————————\n"
        f"<b>Qoldiq: {format_money(bal['balance'])} so'm</b>"
    )


def format_history(records: list) -> str:
    if not records:
        return "Hozircha hech qanday yozuv yo'q."
    lines = ["📜 <b>Oxirgi yozuvlar</b>\n"]
    for r in records:
        sign = "➕" if r["type"] == "income" else "➖"
        date = r["created_at"].replace("T", " ")[:16]
        note = f" — {r['note']}" if r["note"] else ""
        lines.append(f"{sign} {format_money(r['amount'])} so'm{note}  <i>({date})</i>")
    return "\n".join(lines)


dp = Dispatcher(storage=MemoryStorage())


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Assalomu alaykum! 👋\n\n"
        "Men — shaxsiy kassa botiman. Olgan va bergan pullaringizni hisob-kitob qilib beraman.\n\n"
        "Quyidagi menyudan foydalaning:",
        reply_markup=main_menu,
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Qo'llanma</b>\n\n"
        f"{BTN_INCOME} — kirim (olgan pulingiz) qo'shish\n"
        f"{BTN_EXPENSE} — chiqim (bergan/sarflagan pulingiz) qo'shish\n"
        f"{BTN_BALANCE} — joriy balansni ko'rish\n"
        f"{BTN_HISTORY} — so'nggi yozuvlar tarixi\n"
        f"{BTN_UNDO} — oxirgi yozuvni bekor qilish\n\n"
        "Summani kiritishda izoh ham yozishingiz mumkin, masalan:\n"
        "<code>50000 oylik maosh</code>",
        reply_markup=main_menu,
    )


@dp.message(F.text == BTN_CANCEL)
async def cancel_action(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_menu)


@dp.message(F.text == BTN_INCOME)
async def start_income(message: Message, state: FSMContext) -> None:
    await state.set_state(AddTransaction.waiting_for_amount)
    await state.update_data(tx_type="income")
    await message.answer(
        "Qancha pul oldingiz? Summani kiriting (xohlasangiz izoh bilan birga):\n"
        "Masalan: <code>200000 mijozdan to'lov</code>",
        reply_markup=cancel_menu,
    )


@dp.message(F.text == BTN_EXPENSE)
async def start_expense(message: Message, state: FSMContext) -> None:
    await state.set_state(AddTransaction.waiting_for_amount)
    await state.update_data(tx_type="expense")
    await message.answer(
        "Qancha pul berdingiz/sarfladingiz? Summani kiriting (xohlasangiz izoh bilan birga):\n"
        "Masalan: <code>50000 transport</code>",
        reply_markup=cancel_menu,
    )


@dp.message(AddTransaction.waiting_for_amount)
async def process_amount(message: Message, state: FSMContext) -> None:
    parsed = parse_amount_and_note(message.text or "")
    if parsed is None:
        await message.answer(
            "Summani to'g'ri kiriting. Masalan: <code>50000</code> yoki <code>50000 oziq-ovqat</code>",
        )
        return

    amount, note = parsed
    data = await state.get_data()
    tx_type = data["tx_type"]

    db.add_transaction(message.from_user.id, tx_type, amount, note)
    sheets.append_transaction(message.from_user.full_name, tx_type, amount, note)
    await state.clear()

    label = "Kirim" if tx_type == "income" else "Chiqim"
    icon = "➕" if tx_type == "income" else "➖"
    note_text = f" ({note})" if note else ""
    sheet_note = "" if sheets.is_enabled() else "\n\n⚠️ Google Sheets ulanmagan, faqat botda saqlandi."
    await message.answer(
        f"{icon} {label} qo'shildi: {format_money(amount)} so'm{note_text}{sheet_note}",
        reply_markup=main_menu,
    )


@dp.message(F.text == BTN_BALANCE)
async def show_balance(message: Message) -> None:
    bal = db.get_balance(message.from_user.id)
    await message.answer(format_balance(bal), reply_markup=main_menu)


@dp.message(F.text == BTN_HISTORY)
async def show_history(message: Message) -> None:
    records = db.get_history(message.from_user.id, limit=10)
    await message.answer(format_history(records), reply_markup=main_menu)


@dp.message(F.text == BTN_UNDO)
async def undo_last(message: Message) -> None:
    deleted = db.delete_last(message.from_user.id)
    if deleted is None:
        await message.answer("O'chiriladigan yozuv topilmadi.", reply_markup=main_menu)
        return
    sheets.delete_last_row_for_user()
    label = "Kirim" if deleted["type"] == "income" else "Chiqim"
    await message.answer(
        f"O'chirildi: {label} — {format_money(deleted['amount'])} so'm",
        reply_markup=main_menu,
    )


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN topilmadi. Uni BOT_TOKEN muhit o'zgaruvchisi orqali yoki "
            "config.py faylida BOT_TOKEN = '...' ko'rinishida bering."
        )

    db.init_db()
    sheets.init_sheets(GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_JSON)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
