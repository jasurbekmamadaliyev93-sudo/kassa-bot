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
BTN_CURRENCY = "💱 Valyutani o'zgartirish"
BTN_MASTERS = "👷 Ustalar"
BTN_ADD_MASTER = "➕ Yangi usta"
BTN_PAY_MASTER = "💵 To'lov berish"
BTN_MASTER_REPORT = "📋 Ustalar hisoboti"
BTN_EDIT_AGREED = "✏️ Kelishuvni o'zgartirish"

MENU_WORDS = {
    BTN_INCOME, BTN_EXPENSE, BTN_BALANCE, BTN_HISTORY, BTN_UNDO,
    BTN_ACCOUNTS, BTN_CANCEL, BTN_ADD_ACCOUNT, BTN_DEL_ACCOUNT, BTN_BACK, BTN_NO_NOTE, BTN_CURRENCY,
    BTN_MASTERS, BTN_ADD_MASTER, BTN_PAY_MASTER, BTN_MASTER_REPORT, BTN_EDIT_AGREED,
}

SHEETS_ERROR_TEXT = "⚠️ Jadval bilan bog'lanib bo'lmadi. Biroz kutib, qayta urinib ko'ring."

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_INCOME), KeyboardButton(text=BTN_EXPENSE)],
        [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_HISTORY)],
        [KeyboardButton(text=BTN_UNDO), KeyboardButton(text=BTN_ACCOUNTS)],
        [KeyboardButton(text=BTN_MASTERS)],
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
        [KeyboardButton(text=BTN_CURRENCY)],
        [KeyboardButton(text=BTN_BACK)],
    ],
    resize_keyboard=True,
)

masters_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ADD_MASTER), KeyboardButton(text=BTN_PAY_MASTER)],
        [KeyboardButton(text=BTN_MASTER_REPORT), KeyboardButton(text=BTN_EDIT_AGREED)],
        [KeyboardButton(text=BTN_BACK)],
    ],
    resize_keyboard=True,
)

currency_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=c) for c in sheets.CURRENCIES],
              [KeyboardButton(text=BTN_CANCEL)]],
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
    choose_new_currency = State()     # yangi hisob uchun valyuta
    choose_account_cur = State()      # valyutasi o'zgartiriladigan hisob
    choose_currency = State()         # yangi valyuta
    # --- Ustalar ---
    m_acc_add = State()      # yangi usta uchun hisob
    m_name = State()         # usta nomi
    m_agreed = State()       # kelishilgan summa
    m_acc_pay = State()      # to'lov uchun hisob
    m_pick_pay = State()     # to'lov qilinadigan usta
    m_pay_amount = State()   # to'lov summasi
    m_pay_note = State()     # to'lov izohi
    m_acc_report = State()   # hisobot uchun hisob
    m_acc_edit = State()     # kelishuvni o'zgartirish uchun hisob
    m_pick_edit = State()    # o'zgartiriladigan usta
    m_new_agreed = State()   # yangi kelishilgan summa


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


def format_money(amount: float, currency: str = "") -> str:
    """20000 -> '20 000 so'm'. Kasr qism bo'lsa ko'rsatiladi."""
    if abs(amount - round(amount)) < 0.005:
        s = f"{round(amount):,.0f}".replace(",", " ")
    else:
        s = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} {currency}".strip()


def format_all_balances(balances: dict) -> str:
    if not balances:
        return "Hisoblar topilmadi."
    lines = ["💰 <b>Balans</b>"]
    totals: dict[str, float] = {}
    for account, b in balances.items():
        cur = b.get("currency", "")
        totals[cur] = totals.get(cur, 0.0) + b["balance"]
        lines.append(
            f"\n<b>{account}</b>\n"
            f"  ➕ Kirim: {format_money(b['income'], cur)}\n"
            f"  ➖ Chiqim: {format_money(b['expense'], cur)}\n"
            f"  <b>Qoldiq: {format_money(b['balance'], cur)}</b>"
        )
    # Umumiy qoldiq HAR BIR VALYUTA uchun alohida — ular hech qachon qo'shilmaydi
    if len(balances) > 1:
        lines.append("\n—————————————")
        for cur, total in totals.items():
            lines.append(f"<b>Umumiy qoldiq: {format_money(total, cur)}</b>")
    return "\n".join(lines)


def format_history(account: str, records: list, currency: str = "") -> str:
    if not records:
        return f"«{account}» hisobida hozircha yozuv yo'q."
    lines = [f"📜 <b>{account}</b> — oxirgi yozuvlar\n"]
    for r in records:
        sign = "➕" if r["type"] == "income" else "➖"
        note = f" — {r['note']}" if r["note"] else ""
        lines.append(f"{sign} {format_money(r['amount'], currency)}{note}  <i>({r['created_at']})</i>")
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
        f"{BTN_ACCOUNTS} — hisob qo'shish, o'chirish, valyutasini o'zgartirish\n"
        f"{BTN_MASTERS} — usta/xizmat kelishuvlari va to'lovlari\n\n"
        "Har bir hisobning o'z valyutasi bor (so'm yoki $) — ular hech qachon "
        "bir-biriga qo'shilmaydi.\n\n"
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

    cur = await asyncio.to_thread(
        sheets.get_currency, message.from_user.id, message.from_user.full_name, data["account"]
    )
    await state.update_data(amount=amount)
    await state.set_state(Flow.enter_note)
    await message.answer(
        f"Summa: <b>{format_money(amount, cur)}</b>\n\n"
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
    cur = await asyncio.to_thread(
        sheets.get_currency, message.from_user.id, message.from_user.full_name, account
    )
    label = "Kirim" if tx_type == "income" else "Chiqim"
    icon = "➕" if tx_type == "income" else "➖"
    note_text = f" ({note})" if note else ""
    await message.answer(
        f"{icon} <b>{account}</b> — {label} qo'shildi: {format_money(amount, cur)}{note_text}",
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
        cur = await asyncio.to_thread(
            sheets.get_currency, message.from_user.id, message.from_user.full_name, chosen
        )
    except sheets.SheetsError as exc:
        logger.warning(f"Tarixni o'qib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    await state.clear()
    await message.answer(format_history(chosen, records, cur), reply_markup=main_menu)


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
    cur = await asyncio.to_thread(
        sheets.get_currency, message.from_user.id, message.from_user.full_name, chosen
    )
    label = "Kirim" if deleted["type"] == "income" else "Chiqim"
    await message.answer(
        f"O'chirildi: <b>{chosen}</b> — {label} {format_money(deleted['amount'], cur)}",
        reply_markup=main_menu,
    )


# --- Hisoblarni boshqarish ---

@dp.message(StateFilter(None), F.text == BTN_ACCOUNTS)
async def show_accounts(message: Message) -> None:
    try:
        full = await asyncio.to_thread(
            sheets.get_accounts_full, message.from_user.id, message.from_user.full_name
        )
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    listed = "\n".join(f"• {a} — <i>{c}</i>" for a, c in full)
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
    if not name:
        await message.answer("Hisob nomini yozing.")
        return

    await state.update_data(new_account=name)
    await state.set_state(Flow.choose_new_currency)
    await message.answer(
        f"«{name}» hisobi qaysi valyutada yuritiladi?", reply_markup=currency_menu
    )


@dp.message(Flow.choose_new_currency)
async def process_new_currency(message: Message, state: FSMContext) -> None:
    currency = (message.text or "").strip()
    if currency not in sheets.CURRENCIES:
        await message.answer("Valyutani tugmalardan tanlang.", reply_markup=currency_menu)
        return

    data = await state.get_data()
    try:
        created = await asyncio.to_thread(
            sheets.add_account, message.from_user.id, message.from_user.full_name,
            data["new_account"], currency,
        )
    except sheets.AccountError as exc:
        await state.set_state(Flow.enter_new_account)
        await message.answer(f"⚠️ {exc}\n\nBoshqa nom yozing.", reply_markup=cancel_menu)
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Hisob qo'shib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return

    await state.clear()
    await message.answer(
        f"✅ «{created}» hisobi qo'shildi ({currency}).", reply_markup=main_menu
    )


# --- Valyutani o'zgartirish ---

@dp.message(F.text == BTN_CURRENCY)
async def start_change_currency(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    await state.set_state(Flow.choose_account_cur)
    await message.answer("Qaysi hisobning valyutasi o'zgarsin?",
                         reply_markup=account_keyboard(accounts))


@dp.message(Flow.choose_account_cur)
async def picked_account_for_currency(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    chosen = next((a for a in accounts if a == (message.text or "")), None)
    if chosen is None:
        await message.answer("Iltimos, tugmalardan tanlang.",
                             reply_markup=account_keyboard(accounts))
        return
    await state.update_data(cur_account=chosen)
    await state.set_state(Flow.choose_currency)
    await message.answer(f"«{chosen}» qaysi valyutada yuritilsin?", reply_markup=currency_menu)


@dp.message(Flow.choose_currency)
async def process_change_currency(message: Message, state: FSMContext) -> None:
    currency = (message.text or "").strip()
    if currency not in sheets.CURRENCIES:
        await message.answer("Valyutani tugmalardan tanlang.", reply_markup=currency_menu)
        return
    data = await state.get_data()
    account = data["cur_account"]
    try:
        await asyncio.to_thread(
            sheets.set_account_currency, message.from_user.id,
            message.from_user.full_name, account, currency,
        )
    except sheets.AccountError as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}", reply_markup=main_menu)
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Valyutani o'zgartirib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return

    await state.clear()
    await message.answer(
        f"💱 «{account}» endi <b>{currency}</b> da yuritiladi.", reply_markup=main_menu
    )


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


# ==========================================================================
# USTALAR — hisob ichidagi kelishuvlar va to'lovlar
# ==========================================================================

def master_keyboard(masters: list) -> ReplyKeyboardMarkup:
    rows, buf = [], []
    for m in masters:
        buf.append(KeyboardButton(text=m))
        if len(buf) == 2:
            rows.append(buf); buf = []
    if buf:
        rows.append(buf)
    rows.append([KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def format_master_report(account: str, rows: list, cur: str) -> str:
    if not rows:
        return (f"«{account}» hisobida hozircha usta yo'q.\n"
                f"«{BTN_ADD_MASTER}» orqali qo'shing.")
    lines = [f"👷 <b>{account}</b> — ustalar\n"]
    t_agreed = t_paid = 0.0
    for r in rows:
        t_agreed += r["agreed"]; t_paid += r["paid"]
        if r["left"] < -0.005:
            qolgan = f"⚠️ ortiqcha to'langan: {format_money(-r['left'], cur)}"
        elif r["left"] < 0.005:
            qolgan = "✅ to'liq to'langan"
        else:
            qolgan = f"<b>Qolgan: {format_money(r['left'], cur)}</b>"
        lines.append(
            f"\n<b>{r['name']}</b>\n"
            f"  Kelishilgan: {format_money(r['agreed'], cur)}\n"
            f"  Berilgan: {format_money(r['paid'], cur)}\n"
            f"  {qolgan}"
        )
    lines.append(
        f"\n—————————————\n"
        f"Jami kelishilgan: {format_money(t_agreed, cur)}\n"
        f"Jami berilgan: {format_money(t_paid, cur)}\n"
        f"<b>Jami qolgan: {format_money(t_agreed - t_paid, cur)}</b>"
    )
    return "\n".join(lines)


async def _pick_account(message: Message, state: FSMContext, next_state, question: str) -> None:
    """Hisob tanlash bosqichini boshlaydi."""
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu)
        return
    await state.set_state(next_state)
    await message.answer(question, reply_markup=account_keyboard(accounts))


async def _resolve_account(message: Message):
    """Tanlangan hisobni tekshiradi; noto'g'ri bo'lsa None qaytaradi."""
    accounts = await load_accounts(message)
    chosen = next((a for a in accounts if a == (message.text or "")), None)
    if chosen is None:
        await message.answer("Iltimos, tugmalardan hisobni tanlang.",
                             reply_markup=account_keyboard(accounts))
    return chosen


@dp.message(StateFilter(None), F.text == BTN_MASTERS)
async def show_masters_menu(message: Message) -> None:
    await message.answer(
        "👷 <b>Ustalar</b>\n\n"
        "Bu yerda har bir hisob ichida usta va ko'rsatilgan xizmatlar bo'yicha "
        "kelishilgan summa va berilgan to'lovlar yuritiladi.\n"
        "Berilgan to'lov hisob balansidan avtomatik ayriladi.",
        reply_markup=masters_menu,
    )


# --- Yangi usta ---

@dp.message(F.text == BTN_ADD_MASTER)
async def m_start_add(message: Message, state: FSMContext) -> None:
    await _pick_account(message, state, Flow.m_acc_add, "Usta qaysi hisobga qo'shilsin?")


@dp.message(Flow.m_acc_add)
async def m_got_account(message: Message, state: FSMContext) -> None:
    try:
        chosen = await _resolve_account(message)
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return
    if chosen is None:
        return
    await state.update_data(m_account=chosen)
    await state.set_state(Flow.m_name)
    await message.answer(
        f"«{chosen}» — usta yoki xizmat nomini yozing\n"
        f"(masalan: <code>Gipsakarton usta Aziz</code>):",
        reply_markup=cancel_menu,
    )


@dp.message(Flow.m_name)
async def m_got_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or name in MENU_WORDS:
        await message.answer("Boshqa nom yozing.")
        return
    data = await state.get_data()
    try:
        cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                      message.from_user.full_name, data["m_account"])
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return
    await state.update_data(m_name=name)
    await state.set_state(Flow.m_agreed)
    await message.answer(
        f"«{name}» bilan kelishilgan summa qancha ({cur})?\n"
        f"Faqat raqam yozing. Kelishuv hali aniq bo'lmasa <code>0</code> yozing.",
        reply_markup=cancel_menu,
    )


@dp.message(Flow.m_agreed)
async def m_got_agreed(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    agreed = 0.0 if raw == "0" else parse_amount(raw)
    if agreed is None:
        await message.answer("Faqat raqam yozing. Masalan: <code>5000</code>")
        return
    data = await state.get_data()
    try:
        created = await asyncio.to_thread(
            sheets.add_master, message.from_user.id, message.from_user.full_name,
            data["m_account"], data["m_name"], agreed,
        )
        cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                      message.from_user.full_name, data["m_account"])
    except sheets.AccountError as exc:
        await state.set_state(Flow.m_name)
        await message.answer(f"⚠️ {exc}\n\nBoshqa nom yozing.", reply_markup=cancel_menu)
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Ustani qo'shib bo'lmadi: {exc}")
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return

    await state.clear()
    await message.answer(
        f"✅ <b>{created}</b> qo'shildi.\n"
        f"Hisob: {data['m_account']} · Kelishilgan: {format_money(agreed, cur)}",
        reply_markup=main_menu,
    )


# --- To'lov berish ---

@dp.message(F.text == BTN_PAY_MASTER)
async def m_start_pay(message: Message, state: FSMContext) -> None:
    await _pick_account(message, state, Flow.m_acc_pay, "To'lov qaysi hisobdan berilsin?")


@dp.message(Flow.m_acc_pay)
async def m_pay_account(message: Message, state: FSMContext) -> None:
    try:
        chosen = await _resolve_account(message)
        if chosen is None:
            return
        masters = await asyncio.to_thread(sheets.get_masters, message.from_user.id,
                                          message.from_user.full_name, chosen)
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return

    if not masters:
        await state.clear()
        await message.answer(f"«{chosen}» hisobida usta yo'q. Avval «{BTN_ADD_MASTER}» qiling.",
                             reply_markup=masters_menu)
        return
    await state.update_data(m_account=chosen)
    await state.set_state(Flow.m_pick_pay)
    await message.answer("Kimga to'lov berilyapti?",
                         reply_markup=master_keyboard([m for m, _ in masters]))


@dp.message(Flow.m_pick_pay)
async def m_pay_pick(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        masters = await asyncio.to_thread(sheets.get_masters, message.from_user.id,
                                          message.from_user.full_name, data["m_account"])
        names = [m for m, _ in masters]
        chosen = next((m for m in names if m == (message.text or "")), None)
        if chosen is None:
            await message.answer("Iltimos, tugmalardan tanlang.",
                                 reply_markup=master_keyboard(names))
            return
        rep = next(r for r in await asyncio.to_thread(
            sheets.get_master_report, message.from_user.id,
            message.from_user.full_name, data["m_account"]) if r["name"] == chosen)
        cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                      message.from_user.full_name, data["m_account"])
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return

    await state.update_data(m_name=chosen)
    await state.set_state(Flow.m_pay_amount)
    await message.answer(
        f"<b>{chosen}</b>\n"
        f"Kelishilgan: {format_money(rep['agreed'], cur)}\n"
        f"Berilgan: {format_money(rep['paid'], cur)}\n"
        f"Qolgan: <b>{format_money(rep['left'], cur)}</b>\n\n"
        f"Qancha to'lov berilyapti? Faqat raqam yozing.",
        reply_markup=cancel_menu,
    )


@dp.message(Flow.m_pay_amount)
async def m_pay_amount(message: Message, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("Faqat summani kiriting. Masalan: <code>500</code>")
        return
    data = await state.get_data()
    try:
        await asyncio.to_thread(sheets.ensure_can_spend, message.from_user.id,
                                message.from_user.full_name, data["m_account"], amount)
    except sheets.AccountError as exc:
        await message.answer(f"⚠️ {exc}\n\nBoshqa summa kiriting yoki bekor qiling.")
        return
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return

    await state.update_data(m_amount=amount)
    await state.set_state(Flow.m_pay_note)
    await message.answer("Izoh yozing (masalan: <code>1-bosqich uchun</code>) "
                         f"yoki «{BTN_NO_NOTE}» bosing.", reply_markup=note_menu)


@dp.message(Flow.m_pay_note)
async def m_pay_note(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    note = "" if text == BTN_NO_NOTE else text[:200]
    data = await state.get_data()
    try:
        await asyncio.to_thread(
            sheets.pay_master, message.from_user.id, message.from_user.full_name,
            data["m_account"], data["m_name"], data["m_amount"], note,
        )
        rep = next(r for r in await asyncio.to_thread(
            sheets.get_master_report, message.from_user.id,
            message.from_user.full_name, data["m_account"]) if r["name"] == data["m_name"])
        cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                      message.from_user.full_name, data["m_account"])
        bal = await asyncio.to_thread(sheets.get_balance, message.from_user.id,
                                      message.from_user.full_name, data["m_account"])
    except sheets.AccountError as exc:
        await state.clear(); await message.answer(f"⚠️ {exc}", reply_markup=main_menu); return
    except sheets.SheetsError as exc:
        logger.warning(f"To'lovni yozib bo'lmadi: {exc}")
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return

    await state.clear()
    qolgan = ("✅ to'liq to'langan" if abs(rep["left"]) < 0.005 else
              f"Qolgan: <b>{format_money(rep['left'], cur)}</b>")
    await message.answer(
        f"💵 <b>{data['m_name']}</b> ga to'lov berildi: "
        f"{format_money(data['m_amount'], cur)}\n"
        f"{qolgan}\n\n"
        f"«{data['m_account']}» qoldig'i: <b>{format_money(bal['balance'], cur)}</b>",
        reply_markup=main_menu,
    )


# --- Hisobot ---

@dp.message(F.text == BTN_MASTER_REPORT)
async def m_start_report(message: Message, state: FSMContext) -> None:
    await _pick_account(message, state, Flow.m_acc_report, "Qaysi hisob bo'yicha ko'rsatay?")


@dp.message(Flow.m_acc_report)
async def m_report(message: Message, state: FSMContext) -> None:
    try:
        chosen = await _resolve_account(message)
        if chosen is None:
            return
        rows = await asyncio.to_thread(sheets.get_master_report, message.from_user.id,
                                       message.from_user.full_name, chosen)
        cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                      message.from_user.full_name, chosen)
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return
    await state.clear()
    await message.answer(format_master_report(chosen, rows, cur), reply_markup=main_menu)


# --- Kelishuvni o'zgartirish ---

@dp.message(F.text == BTN_EDIT_AGREED)
async def m_start_edit(message: Message, state: FSMContext) -> None:
    await _pick_account(message, state, Flow.m_acc_edit, "Qaysi hisobdagi usta?")


@dp.message(Flow.m_acc_edit)
async def m_edit_account(message: Message, state: FSMContext) -> None:
    try:
        chosen = await _resolve_account(message)
        if chosen is None:
            return
        masters = await asyncio.to_thread(sheets.get_masters, message.from_user.id,
                                          message.from_user.full_name, chosen)
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return
    if not masters:
        await state.clear()
        await message.answer(f"«{chosen}» hisobida usta yo'q.", reply_markup=masters_menu)
        return
    await state.update_data(m_account=chosen)
    await state.set_state(Flow.m_pick_edit)
    await message.answer("Kimning kelishuvi o'zgarsin?",
                         reply_markup=master_keyboard([m for m, _ in masters]))


@dp.message(Flow.m_pick_edit)
async def m_edit_pick(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        masters = await asyncio.to_thread(sheets.get_masters, message.from_user.id,
                                          message.from_user.full_name, data["m_account"])
        names = [m for m, _ in masters]
        chosen = next((m for m in names if m == (message.text or "")), None)
        if chosen is None:
            await message.answer("Iltimos, tugmalardan tanlang.",
                                 reply_markup=master_keyboard(names))
            return
        cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                      message.from_user.full_name, data["m_account"])
        eski = next(a for m, a in masters if m == chosen)
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return

    await state.update_data(m_name=chosen)
    await state.set_state(Flow.m_new_agreed)
    await message.answer(
        f"<b>{chosen}</b> — hozirgi kelishuv: {format_money(eski, cur)}\n"
        f"Yangi summani yozing (faqat raqam):",
        reply_markup=cancel_menu,
    )


@dp.message(Flow.m_new_agreed)
async def m_edit_agreed(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    agreed = 0.0 if raw == "0" else parse_amount(raw)
    if agreed is None:
        await message.answer("Faqat raqam yozing.")
        return
    data = await state.get_data()
    try:
        await asyncio.to_thread(
            sheets.set_master_agreed, message.from_user.id, message.from_user.full_name,
            data["m_account"], data["m_name"], agreed,
        )
        cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                      message.from_user.full_name, data["m_account"])
    except sheets.AccountError as exc:
        await state.clear(); await message.answer(f"⚠️ {exc}", reply_markup=main_menu); return
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=main_menu); return

    await state.clear()
    await message.answer(
        f"✏️ <b>{data['m_name']}</b> kelishuvi: {format_money(agreed, cur)}",
        reply_markup=main_menu,
    )


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
