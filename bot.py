"""
"Mening hamyonim" — kirim-chiqim hisobini yurituvchi Telegram bot.

- Har bir foydalanuvchi uchun Google jadvalda ALOHIDA list ochiladi (ismi bilan).
- Har bir foydalanuvchi bir nechta hisob yuritishi mumkin (masalan "Imzo showroom"
  va "Shaxsiy") — ular bir-biriga umuman aralashmaydi.
- Har bir kirim/chiqimda qaysi hisobga yozilishi so'raladi.

Ishga tushirish: python bot.py
"""
import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

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
BTN_ACCOUNTS = "⚙️ Hisoblarim"
BTN_CANCEL = "❌ Bekor qilish"
BTN_ADD_ACCOUNT = "➕ Yangi hisob"
BTN_DEL_ACCOUNT = "🗑 Hisobni o'chirish"
BTN_BACK = "⬅️ Orqaga"
BTN_NO_NOTE = "➡️ Izohsiz saqlash"

MENU_WORDS = {
    BTN_INCOME, BTN_EXPENSE, BTN_BALANCE, BTN_HISTORY, BTN_UNDO,
    BTN_ACCOUNTS, BTN_CANCEL, BTN_ADD_ACCOUNT, BTN_DEL_ACCOUNT, BTN_BACK, BTN_NO_NOTE,
}

SHEETS_ERROR_TEXT = "⚠️ Jadval bilan bog'lanib bo'lmadi. Biroz kutib, qayta urinib ko'ring."

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_INCOME), KeyboardButton(text=BTN_EXPENSE)],
        [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_HISTORY)],
        [KeyboardButton(text=BTN_UNDO), KeyboardButton(text=BTN_ACCOUNTS)],
    ],
    resize_keyboard=True,
)

cancel_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True
)

note_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_NO_NOTE)], [KeyboardButton(text=BTN_CANCEL)]],
    resize_keyboard=True,
)

accounts_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ADD_ACCOUNT), KeyboardButton(text=BTN_DEL_ACCOUNT)],
        [KeyboardButton(text=BTN_BACK)],
    ],
    resize_keyboard=True,
)


def account_keyboard(accounts: list) -> ReplyKeyboardMarkup:
    """Hisoblar ro'yxatidan tugmalar (ikkitadan bir qatorga) + Bekor qilish."""
    rows, buf = [], []
    for acc in accounts:
        buf.append(KeyboardButton(text=acc))
        if len(buf) == 2:
            rows.append(buf)
            buf = []
    if buf:
        rows.append(buf)
    rows.append([KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


class Flow(StatesGroup):
    choose_account_add = State()      # kirim/chiqim uchun hisob tanlash
    enter_amount = State()            # summa kiritish (faqat raqam)
    enter_note = State()              # izoh kiritish (alohida qadam)
    choose_account_history = State()  # tarix uchun hisob tanlash
    choose_account_undo = State()     # o'chirish uchun hisob tanlash
    enter_new_account = State()       # yangi hisob nomi
    choose_account_delete = State()   # o'chiriladigan hisobni tanlash


AMOUNT_RE = re.compile(r"^[\d\s.,']+$")


def parse_amount(text: str) -> float | None:
    """Faqat summa qabul qilinadi. '20 000' yoki '20000' — ha; '20000 taksi' — yo'q."""
    raw = (text or "").strip()
    if not raw or not AMOUNT_RE.match(raw):
        return None
    for ch in (" ", " ", " ", "'", "`"):
        raw = raw.replace(ch, "")
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount if amount > 0 else None


def format_money(amount: float) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def format_all_balances(balances: dict) -> str:
    if not balances:
        return "Hisoblar topilmadi."
    lines = ["💰 <b>Balans</b>"]
    total = 0.0
    for account, b in balances.items():
        total += b["balance"]
        lines.append(
            f"\n<b>{account}</b>\n"
            f"  ➕ Kirim: {format_money(b['income'])} so'm\n"
            f"  ➖ Chiqim: {format_money(b['expense'])} so'm\n"
            f"  <b>Qoldiq: {format_money(b['balance'])} so'm</b>"
        )
    if len(balances) > 1:
        lines.append(f"\n—————————————\n<b>Umumiy qoldiq: {format_money(total)} so'm</b>")
    return "\n".join(lines)


def format_history(account: str, records: list) -> str:
    if not records:
        return f"«{account}» hisobida hozircha yozuv yo'q."
    lines = [f"📜 <b>{account}</b> — oxirgi yozuvlar\n"]
    for r in records:
        sign = "➕" if r["type"] == "income" else "➖"
        note = f" — {r['note']}" if r["note"] else ""
        lines.append(f"{sign} {format_money(r['amount'])} so'm{note}  <i>({r['created_at']})</i>")
    return "\n".join(lines)


dp = Dispatcher(storage=MemoryStorage())


async def load_accounts(message: Message) -> list:
    return await asyncio.to_thread(
        sheets.get_accounts, message.from_user.id, message.from_user.full_name
    )


# --- Bekor qilish (istalgan holatda ishlaydi, shuning uchun birinchi) ---

@dp.message(F.text == BTN_CANCEL)
async def cancel_action(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_menu)


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT)
        return
    await message.answer(
        "Assalomu alaykum! 👋\n\n"
        "Men — <b>Mening hamyonim</b>. Olgan va bergan pullaringizni hisob-kitob qilib beraman.\n\n"
        f"Sizning hisoblaringiz: <b>{'</b>, <b>'.join(accounts)}</b>\n"
        "Yangi hisob qo'shish uchun ⚙️ Hisoblarim bo'limiga kiring.",
        reply_markup=main_menu,
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Qo'llanma</b>\n\n"
        f"{BTN_INCOME} — olgan pulingizni yozish\n"
        f"{BTN_EXPENSE} — bergan/sarflagan pulingizni yozish\n"
        f"{BTN_BALANCE} — barcha hisoblar bo'yicha balans\n"
        f"{BTN_HISTORY} — tanlangan hisobning so'nggi yozuvlari\n"
        f"{BTN_UNDO} — oxirgi yozuvni bekor qilish\n"
        f"{BTN_ACCOUNTS} — hisob qo'shish yoki o'chirish\n\n"
        "Har bir yozuv qaysi hisobga tegishli ekani so'raladi — hisoblar "
        "bir-biriga aralashmaydi.\n\n"
        "❗️ Chiqim hisobdagi qoldiqdan oshmaydi — balans hech qachon manfiy bo'lmaydi.\n\n"
        "Avval summa, keyin izoh alohida so'raladi. Izoh shart emas — "
        f"«{BTN_NO_NOTE}» tugmasi bilan o'tkazib yuborish mumkin.",
        reply_markup=main_menu,
    )


# --- Kirim / chiqim qo'shish ---

@dp.message(StateFilter(None), F.text.in_({BTN_INCOME, BTN_EXPENSE}))
async def start_transaction(message: Message, state: FSMContext) -> None:
    tx_type = "income" if message.text == BTN_INCOME else "expense"
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return

    await state.set_state(Flow.choose_account_add)
    await state.update_data(tx_type=tx_type)
    word = "Kirim" if tx_type == "income" else "Chiqim"
    await message.answer(f"{word} qaysi hisobga yozilsin?", reply_markup=account_keyboard(accounts))


@dp.message(Flow.choose_account_add)
async def picked_account_add(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return

    chosen = next((a for a in accounts if a == (message.text or "")), None)
    if chosen is None:
        await message.answer(
            "Iltimos, quyidagi tugmalardan hisobni tanlang.",
            reply_markup=account_keyboard(accounts),
        )
        return

    data = await state.get_data()
    await state.set_state(Flow.enter_amount)
    await state.update_data(account=chosen)
    word = "oldingiz" if data["tx_type"] == "income" else "berdingiz/sarfladingiz"
    await message.answer(
        f"«{chosen}» — qancha pul {word}?\n"
        f"Faqat summani kiriting: <code>50000</code>\n"
        f"<i>(izohni keyingi qadamda so'rayman)</i>",
        reply_markup=cancel_menu,
    )


@dp.message(Flow.enter_amount)
async def process_amount(message: Message, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "Faqat summani kiriting, izohsiz. Masalan: <code>50000</code> yoki <code>50 000</code>"
        )
        return

    data = await state.get_data()

    # Chiqim bo'lsa — izoh so'rashdan OLDIN qoldiqni tekshiramiz
    if data["tx_type"] == "expense":
        try:
            await asyncio.to_thread(
                sheets.ensure_can_spend,
                message.from_user.id, message.from_user.full_name,
                data["account"], amount,
            )
        except sheets.AccountError as exc:
            await message.answer(f"⚠️ {exc}\n\nBoshqa summa kiriting yoki bekor qiling.")
            return
        except sheets.SheetsError as exc:
            logger.warning(f"Qoldiqni tekshirib bo'lmadi: {exc}")
            await state.clear()
            await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
            return

    await state.update_data(amount=amount)
    await state.set_state(Flow.enter_note)
    await message.answer(
        f"Summa: <b>{format_money(amount)} so'm</b>\n\n"
        f"Endi izoh yozing (masalan: <code>transport</code>),\n"
        f"yoki «{BTN_NO_NOTE}» tugmasini bosing.",
        reply_markup=note_menu,
    )


@dp.message(Flow.enter_note)
async def process_note(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    note = "" if text == BTN_NO_NOTE else text[:200]

    data = await state.get_data()
    tx_type, account, amount = data["tx_type"], data["account"], data["amount"]

    try:
        await asyncio.to_thread(
            sheets.add_transaction,
            message.from_user.id, message.from_user.full_name,
            account, tx_type, amount, note,
        )
    except sheets.AccountError as exc:
        # Izoh yozayotgan paytda qoldiq o'zgargan bo'lsa
        await state.clear()
        await message.answer(f"⚠️ {exc}", reply_markup=main_menu)
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Jadvalga yozib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return

    await state.clear()
    label = "Kirim" if tx_type == "income" else "Chiqim"
    icon = "➕" if tx_type == "income" else "➖"
    note_text = f" ({note})" if note else ""
    await message.answer(
        f"{icon} <b>{account}</b> — {label} qo'shildi: {format_money(amount)} so'm{note_text}",
        reply_markup=main_menu,
    )


# --- Balans (barcha hisoblar birdan) ---

@dp.message(StateFilter(None), F.text == BTN_BALANCE)
async def show_balance(message: Message) -> None:
    try:
        balances = await asyncio.to_thread(
            sheets.get_all_balances, message.from_user.id, message.from_user.full_name
        )
    except sheets.SheetsError as exc:
        logger.warning(f"Balansni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    await message.answer(format_all_balances(balances), reply_markup=main_menu)


# --- Tarix ---

@dp.message(StateFilter(None), F.text == BTN_HISTORY)
async def start_history(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    await state.set_state(Flow.choose_account_history)
    await message.answer("Qaysi hisob tarixini ko'rsatay?", reply_markup=account_keyboard(accounts))


@dp.message(Flow.choose_account_history)
async def picked_account_history(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
        chosen = next((a for a in accounts if a == (message.text or "")), None)
        if chosen is None:
            await message.answer("Iltimos, tugmalardan tanlang.", reply_markup=account_keyboard(accounts))
            return
        records = await asyncio.to_thread(
            sheets.get_history, message.from_user.id, message.from_user.full_name, chosen, 10
        )
    except sheets.SheetsError as exc:
        logger.warning(f"Tarixni o'qib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    await state.clear()
    await message.answer(format_history(chosen, records), reply_markup=main_menu)


# --- Oxirgi yozuvni o'chirish ---

@dp.message(StateFilter(None), F.text == BTN_UNDO)
async def start_undo(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    await state.set_state(Flow.choose_account_undo)
    await message.answer(
        "Qaysi hisobning oxirgi yozuvi o'chirilsin?", reply_markup=account_keyboard(accounts)
    )


@dp.message(Flow.choose_account_undo)
async def picked_account_undo(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
        chosen = next((a for a in accounts if a == (message.text or "")), None)
        if chosen is None:
            await message.answer("Iltimos, tugmalardan tanlang.", reply_markup=account_keyboard(accounts))
            return
        deleted = await asyncio.to_thread(
            sheets.delete_last, message.from_user.id, message.from_user.full_name, chosen
        )
    except sheets.SheetsError as exc:
        logger.warning(f"Yozuvni o'chirib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return

    await state.clear()
    if deleted is None:
        await message.answer(f"«{chosen}» hisobida o'chiriladigan yozuv yo'q.", reply_markup=main_menu)
        return
    label = "Kirim" if deleted["type"] == "income" else "Chiqim"
    await message.answer(
        f"O'chirildi: <b>{chosen}</b> — {label} {format_money(deleted['amount'])} so'm",
        reply_markup=main_menu,
    )


# --- Hisoblarni boshqarish ---

@dp.message(StateFilter(None), F.text == BTN_ACCOUNTS)
async def show_accounts(message: Message) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    listed = "\n".join(f"• {a}" for a in accounts)
    await message.answer(f"⚙️ <b>Hisoblaringiz</b>\n\n{listed}", reply_markup=accounts_menu)


@dp.message(F.text == BTN_BACK)
async def back_to_main(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Asosiy menyu", reply_markup=main_menu)


@dp.message(F.text == BTN_ADD_ACCOUNT)
async def start_add_account(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.enter_new_account)
    await message.answer(
        "Yangi hisob nomini yozing (masalan: <code>Do'kon</code> yoki <code>Mashina</code>):",
        reply_markup=cancel_menu,
    )


@dp.message(Flow.enter_new_account)
async def process_new_account(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if name in MENU_WORDS:
        await message.answer("Bu nom menyu tugmasi bilan bir xil. Boshqa nom tanlang.")
        return
    try:
        created = await asyncio.to_thread(
            sheets.add_account, message.from_user.id, message.from_user.full_name, name
        )
    except sheets.AccountError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Hisob qo'shib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return

    await state.clear()
    await message.answer(f"✅ «{created}» hisobi qo'shildi.", reply_markup=main_menu)


@dp.message(F.text == BTN_DEL_ACCOUNT)
async def start_delete_account(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    await state.set_state(Flow.choose_account_delete)
    await message.answer("Qaysi hisob o'chirilsin?", reply_markup=account_keyboard(accounts))


@dp.message(Flow.choose_account_delete)
async def process_delete_account(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
        chosen = next((a for a in accounts if a == (message.text or "")), None)
        if chosen is None:
            await message.answer("Iltimos, tugmalardan tanlang.", reply_markup=account_keyboard(accounts))
            return
        await asyncio.to_thread(
            sheets.delete_account, message.from_user.id, message.from_user.full_name, chosen
        )
    except sheets.AccountError as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}", reply_markup=main_menu)
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Hisobni o'chirib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return

    await state.clear()
    await message.answer(f"🗑 «{chosen}» hisobi o'chirildi.", reply_markup=main_menu)


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi.")
    if not sheets.init_sheets(GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_JSON):
        raise SystemExit("Google Sheets bilan ulanib bo'lmadi — yuqoridagi xatoga qarang.")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
