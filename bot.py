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

# «👷 Ustalar» bo'limi faqat shu ID'lardagi foydalanuvchilarga ko'rinadi.
# Railway'da MASTERS_USER_IDS o'zgaruvchisiga vergul bilan yozing: "725743391"
def _parse_ids(raw: str) -> set:
    return {int(p) for p in (raw or "").replace(" ", "").split(",") if p.isdigit()}

MASTERS_USER_IDS = _parse_ids(os.getenv("MASTERS_USER_IDS", ""))
if not MASTERS_USER_IDS:
    try:
        import config as _cfg
        MASTERS_USER_IDS = _parse_ids(str(getattr(_cfg, "MASTERS_USER_IDS", "")))
    except ImportError:
        pass


def can_use_masters(user_id: int) -> bool:
    return user_id in MASTERS_USER_IDS

def menu_for(message: Message) -> ReplyKeyboardMarkup:
    """Asosiy menyu — «Ustalar» faqat ruxsat berilganlarda ko'rinadi."""
    rows = [
        [BTN_INCOME, BTN_EXPENSE],
        [BTN_BALANCE, BTN_HISTORY],
        [BTN_UNDO, BTN_ACCOUNTS],
    ]
    if can_use_masters(message.from_user.id):
        rows.append([BTN_MASTERS])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in r] for r in rows],
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
    choose_tx_currency = State()      # qaysi valyutada kiritilyapti
    enter_amount = State()            # summa kiritish (faqat raqam)
    enter_rate = State()              # kurs (valyuta hisobnikidan farq qilsa)
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
    m_currency = State()     # kelishuv valyutasi
    m_acc_pay = State()      # to'lov uchun hisob
    m_pick_pay = State()     # to'lov qilinadigan usta
    m_pay_amount = State()   # to'lov summasi
    m_pay_rate = State()     # kurs (valyutalar farq qilsa)
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
        asl = ""
        if r.get("orig") and r.get("rate"):
            asl_cur = "so'm" if currency == "$" else "$"
            asl = f" [{format_money(r['orig'], asl_cur)}]"
        usta = f" · {r['master']}" if r.get("master") else ""
        lines.append(f"{sign} {format_money(r['amount'], currency)}{asl}{note}{usta}"
                     f"  <i>({r['created_at']})</i>")
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
    await message.answer("Bekor qilindi.", reply_markup=menu_for(message))


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
        reply_markup=menu_for(message),
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
        reply_markup=menu_for(message),
    )


# --- Kirim / chiqim qo'shish ---

@dp.message(StateFilter(None), F.text.in_({BTN_INCOME, BTN_EXPENSE}))
async def start_transaction(message: Message, state: FSMContext) -> None:
    tx_type = "income" if message.text == BTN_INCOME else "expense"
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
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
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return

    chosen = next((a for a in accounts if a == (message.text or "")), None)
    if chosen is None:
        await message.answer(
            "Iltimos, quyidagi tugmalardan hisobni tanlang.",
            reply_markup=account_keyboard(accounts),
        )
        return

    try:
        a_cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                        message.from_user.full_name, chosen)
    except sheets.SheetsError:
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return

    await state.update_data(account=chosen, a_cur=a_cur)
    await state.set_state(Flow.choose_tx_currency)
    await message.answer(
        f"«{chosen}» — summani qaysi valyutada kiritasiz?\n"
        f"<i>(hisob {a_cur} da yuritiladi)</i>",
        reply_markup=currency_menu,
    )


@dp.message(Flow.choose_tx_currency)
async def picked_tx_currency(message: Message, state: FSMContext) -> None:
    cur = (message.text or "").strip()
    if cur not in sheets.CURRENCIES:
        await message.answer("Valyutani tugmalardan tanlang.", reply_markup=currency_menu)
        return
    data = await state.get_data()
    await state.update_data(tx_cur=cur)
    await state.set_state(Flow.enter_amount)
    word = "oldingiz" if data["tx_type"] == "income" else "berdingiz/sarfladingiz"
    await message.answer(
        f"«{data['account']}» — qancha pul {word} ({cur})?\n"
        f"Faqat summani kiriting: <code>5 000 000</code>\n"
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
    await state.update_data(amount=amount)

    # Valyuta hisobnikidan farq qilsa — kurs so'raymiz
    if data["tx_cur"] != data["a_cur"]:
        await state.set_state(Flow.enter_rate)
        await message.answer(
            f"Summa: <b>{format_money(amount, data['tx_cur'])}</b>\n\n"
            f"O'sha kungi kurs qancha edi? (1 $ necha so'm)\n"
            f"Masalan: <code>11900</code>",
            reply_markup=cancel_menu,
        )
        return

    await _finish_amount(message, state, amount, amount, None)


@dp.message(Flow.enter_rate)
async def process_rate(message: Message, state: FSMContext) -> None:
    rate = parse_amount(message.text or "")
    if rate is None or rate < 100:
        await message.answer("Kursni to'g'ri kiriting. Masalan: <code>11900</code>")
        return
    data = await state.get_data()
    try:
        acc_amount = round(
            sheets.convert(data["amount"], data["tx_cur"], data["a_cur"], rate), 2)
    except sheets.AccountError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    if acc_amount <= 0:
        await message.answer("Summa juda kichik chiqdi. Boshqa qiymat kiriting.")
        return
    await _finish_amount(message, state, data["amount"], acc_amount, rate)


async def _finish_amount(message: Message, state: FSMContext,
                         orig: float, acc_amount: float, rate) -> None:
    """Summa tayyor — chiqim bo'lsa qoldiqni tekshirib, izoh so'raymiz."""
    data = await state.get_data()
    if data["tx_type"] == "expense":
        try:
            await asyncio.to_thread(
                sheets.ensure_can_spend, message.from_user.id,
                message.from_user.full_name, data["account"], acc_amount,
            )
        except sheets.AccountError as exc:
            await message.answer(f"⚠️ {exc}\n\nBoshqa summa kiriting yoki bekor qiling.")
            await state.set_state(Flow.enter_amount)
            return
        except sheets.SheetsError as exc:
            logger.warning(f"Qoldiqni tekshirib bo'lmadi: {exc}")
            await state.clear()
            await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
            return

    await state.update_data(acc_amount=acc_amount, rate=rate)
    await state.set_state(Flow.enter_note)

    if rate is None:
        satr = f"Summa: <b>{format_money(acc_amount, data['a_cur'])}</b>"
    else:
        satr = (f"{format_money(orig, data['tx_cur'])} ÷ {format_money(rate)} = "
                f"<b>{format_money(acc_amount, data['a_cur'])}</b>"
                if data["tx_cur"] == "so'm" else
                f"{format_money(orig, data['tx_cur'])} × {format_money(rate)} = "
                f"<b>{format_money(acc_amount, data['a_cur'])}</b>")
    await message.answer(
        f"{satr}\n\nEndi izoh yozing (masalan: <code>transport</code>),\n"
        f"yoki «{BTN_NO_NOTE}» tugmasini bosing.",
        reply_markup=note_menu,
    )


@dp.message(Flow.enter_note)
async def process_note(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    note = "" if text == BTN_NO_NOTE else text[:200]

    data = await state.get_data()
    tx_type, account = data["tx_type"], data["account"]
    amount, rate = data["acc_amount"], data.get("rate")
    orig = data["amount"] if rate is not None else None

    try:
        await asyncio.to_thread(
            sheets.add_transaction,
            message.from_user.id, message.from_user.full_name,
            account, tx_type, amount, note, "", orig, rate,
        )
    except sheets.AccountError as exc:
        # Izoh yozayotgan paytda qoldiq o'zgargan bo'lsa
        await state.clear()
        await message.answer(f"⚠️ {exc}", reply_markup=menu_for(message))
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Jadvalga yozib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return

    await state.clear()
    cur = data["a_cur"]
    label = "Kirim" if tx_type == "income" else "Chiqim"
    icon = "➕" if tx_type == "income" else "➖"
    note_text = f" ({note})" if note else ""
    asl = "" if orig is None else f"\n<i>({format_money(orig, data['tx_cur'])}, kurs {format_money(rate)})</i>"
    await message.answer(
        f"{icon} <b>{account}</b> — {label} qo'shildi: "
        f"{format_money(amount, cur)}{note_text}{asl}",
        reply_markup=menu_for(message),
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
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return
    await message.answer(format_all_balances(balances), reply_markup=menu_for(message))


# --- Tarix ---

@dp.message(StateFilter(None), F.text == BTN_HISTORY)
async def start_history(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
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
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return
    await state.clear()
    await message.answer(format_history(chosen, records, cur), reply_markup=menu_for(message))


# --- Oxirgi yozuvni o'chirish ---

@dp.message(StateFilter(None), F.text == BTN_UNDO)
async def start_undo(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
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
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return

    await state.clear()
    if deleted is None:
        await message.answer(f"«{chosen}» hisobida o'chiriladigan yozuv yo'q.", reply_markup=menu_for(message))
        return
    cur = await asyncio.to_thread(
        sheets.get_currency, message.from_user.id, message.from_user.full_name, chosen
    )
    label = "Kirim" if deleted["type"] == "income" else "Chiqim"
    await message.answer(
        f"O'chirildi: <b>{chosen}</b> — {label} {format_money(deleted['amount'], cur)}",
        reply_markup=menu_for(message),
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
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return
    listed = "\n".join(f"• {a} — <i>{c}</i>" for a, c in full)
    await message.answer(f"⚙️ <b>Hisoblaringiz</b>\n\n{listed}", reply_markup=accounts_menu)


@dp.message(F.text == BTN_BACK)
async def back_to_main(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Asosiy menyu", reply_markup=menu_for(message))


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
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return

    await state.clear()
    await message.answer(
        f"✅ «{created}» hisobi qo'shildi ({currency}).", reply_markup=menu_for(message)
    )


# --- Valyutani o'zgartirish ---

@dp.message(F.text == BTN_CURRENCY)
async def start_change_currency(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
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
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
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
        await message.answer(f"⚠️ {exc}", reply_markup=menu_for(message))
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Valyutani o'zgartirib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return

    await state.clear()
    await message.answer(
        f"💱 «{account}» endi <b>{currency}</b> da yuritiladi.", reply_markup=menu_for(message)
    )


@dp.message(F.text == BTN_DEL_ACCOUNT)
async def start_delete_account(message: Message, state: FSMContext) -> None:
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
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
        await message.answer(f"⚠️ {exc}", reply_markup=menu_for(message))
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Hisobni o'chirib bo'lmadi: {exc}")
        await state.clear()
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
        return

    await state.clear()
    await message.answer(f"🗑 «{chosen}» hisobi o'chirildi.", reply_markup=menu_for(message))


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


def format_master_report(account: str, rows: list, acc_cur: str) -> str:
    """Har bir usta O'Z kelishuv valyutasida ko'rsatiladi; jami ham valyuta bo'yicha ajratiladi."""
    if not rows:
        return (f"«{account}» hisobida hozircha usta yo'q.\n"
                f"«{BTN_ADD_MASTER}» orqali qo'shing.")
    lines = [f"👷 <b>{account}</b> — ustalar\n"]
    tot: dict[str, dict] = {}
    paid_acc_total = 0.0
    for r in rows:
        cur = r.get("currency", acc_cur)
        t = tot.setdefault(cur, {"agreed": 0.0, "paid": 0.0})
        t["agreed"] += r["agreed"]; t["paid"] += r["paid"]
        paid_acc_total += r.get("paid_account", 0.0)

        if r["left"] < -0.005:
            qolgan = f"⚠️ ortiqcha to'langan: {format_money(-r['left'], cur)}"
        elif r["left"] < 0.005:
            qolgan = "✅ to'liq to'langan"
        else:
            qolgan = f"<b>Qolgan: {format_money(r['left'], cur)}</b>"
        kassa = ("" if cur == acc_cur or not r.get("paid_account") else
                 f"  <i>(kassadan {format_money(r['paid_account'], acc_cur)})</i>")
        lines.append(
            f"\n<b>{r['name']}</b>\n"
            f"  Kelishilgan: {format_money(r['agreed'], cur)}\n"
            f"  Berilgan: {format_money(r['paid'], cur)}{kassa}\n"
            f"  {qolgan}"
        )

    lines.append("\n—————————————")
    for cur, t in tot.items():
        lines.append(
            f"Jami kelishilgan: {format_money(t['agreed'], cur)}\n"
            f"Jami berilgan: {format_money(t['paid'], cur)}\n"
            f"<b>Jami qolgan: {format_money(t['agreed'] - t['paid'], cur)}</b>"
        )
    lines.append(f"\n<i>Kassadan jami chiqqan: {format_money(paid_acc_total, acc_cur)}</i>")
    return "\n".join(lines)


async def _pick_account(message: Message, state: FSMContext, next_state, question: str) -> None:
    """Hisob tanlash bosqichini boshlaydi."""
    try:
        accounts = await load_accounts(message)
    except sheets.SheetsError as exc:
        logger.warning(f"Hisoblarni o'qib bo'lmadi: {exc}")
        await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message))
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
    if not can_use_masters(message.from_user.id):
        return
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
    if not can_use_masters(message.from_user.id):
        return
    await _pick_account(message, state, Flow.m_acc_add, "Usta qaysi hisobga qo'shilsin?")


@dp.message(Flow.m_acc_add)
async def m_got_account(message: Message, state: FSMContext) -> None:
    try:
        chosen = await _resolve_account(message)
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return
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
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return
    await state.update_data(m_name=name)
    await state.set_state(Flow.m_currency)
    await message.answer(
        f"«{name}» bilan qaysi valyutada kelishilgan?\n"
        f"<i>(hisob valyutasi — {cur})</i>",
        reply_markup=currency_menu,
    )


@dp.message(Flow.m_currency)
async def m_got_currency(message: Message, state: FSMContext) -> None:
    cur = (message.text or "").strip()
    if cur not in sheets.CURRENCIES:
        await message.answer("Valyutani tugmalardan tanlang.", reply_markup=currency_menu)
        return
    data = await state.get_data()
    await state.update_data(m_cur=cur)
    await state.set_state(Flow.m_agreed)
    await message.answer(
        f"«{data['m_name']}» bilan kelishilgan summa qancha ({cur})?\n"
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
        cur = data["m_cur"]
        created = await asyncio.to_thread(
            sheets.add_master, message.from_user.id, message.from_user.full_name,
            data["m_account"], data["m_name"], agreed, cur,
        )
    except sheets.AccountError as exc:
        await state.set_state(Flow.m_name)
        await message.answer(f"⚠️ {exc}\n\nBoshqa nom yozing.", reply_markup=cancel_menu)
        return
    except sheets.SheetsError as exc:
        logger.warning(f"Ustani qo'shib bo'lmadi: {exc}")
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return

    await state.clear()
    await message.answer(
        f"✅ <b>{created}</b> qo'shildi.\n"
        f"Hisob: {data['m_account']} · Kelishilgan: {format_money(agreed, cur)}",
        reply_markup=menu_for(message),
    )


# --- To'lov berish ---

@dp.message(F.text == BTN_PAY_MASTER)
async def m_start_pay(message: Message, state: FSMContext) -> None:
    if not can_use_masters(message.from_user.id):
        return
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
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return

    if not masters:
        await state.clear()
        await message.answer(f"«{chosen}» hisobida usta yo'q. Avval «{BTN_ADD_MASTER}» qiling.",
                             reply_markup=masters_menu)
        return
    await state.update_data(m_account=chosen)
    await state.set_state(Flow.m_pick_pay)
    await message.answer("Kimga to'lov berilyapti?",
                         reply_markup=master_keyboard([m for m, _, _ in masters]))


@dp.message(Flow.m_pick_pay)
async def m_pay_pick(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        masters = await asyncio.to_thread(sheets.get_masters, message.from_user.id,
                                          message.from_user.full_name, data["m_account"])
        names = [m for m, _, _ in masters]
        chosen = next((m for m in names if m == (message.text or "")), None)
        if chosen is None:
            await message.answer("Iltimos, tugmalardan tanlang.",
                                 reply_markup=master_keyboard(names))
            return
        rep = next(r for r in await asyncio.to_thread(
            sheets.get_master_report, message.from_user.id,
            message.from_user.full_name, data["m_account"]) if r["name"] == chosen)
        a_cur = await asyncio.to_thread(sheets.get_currency, message.from_user.id,
                                        message.from_user.full_name, data["m_account"])
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return

    m_cur = rep["currency"]
    await state.update_data(m_name=chosen, m_cur=m_cur, a_cur=a_cur)
    await state.set_state(Flow.m_pay_amount)
    kassa = ("" if m_cur == a_cur else
             f"\n<i>Kassadan chiqqan: {format_money(rep['paid_account'], a_cur)}</i>")
    await message.answer(
        f"<b>{chosen}</b>  <i>(kelishuv {m_cur} da)</i>\n"
        f"Kelishilgan: {format_money(rep['agreed'], m_cur)}\n"
        f"Berilgan: {format_money(rep['paid'], m_cur)}{kassa}\n"
        f"Qolgan: <b>{format_money(rep['left'], m_cur)}</b>\n\n"
        f"Qancha to'lov berilyapti ({m_cur})? Faqat raqam yozing.",
        reply_markup=cancel_menu,
    )


@dp.message(Flow.m_pay_amount)
async def m_pay_amount(message: Message, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("Faqat summani kiriting. Masalan: <code>5 000 000</code>")
        return
    data = await state.get_data()
    await state.update_data(m_amount=amount)

    # Kelishuv valyutasi hisob valyutasidan farq qilsa — kurs kerak
    if data["m_cur"] != data["a_cur"]:
        await state.set_state(Flow.m_pay_rate)
        await message.answer(
            f"To'lov: <b>{format_money(amount, data['m_cur'])}</b>\n\n"
            f"O'sha kungi kurs qancha edi? (1 $ necha so'm)\n"
            f"Masalan: <code>11900</code>",
            reply_markup=cancel_menu,
        )
        return

    try:
        await asyncio.to_thread(sheets.ensure_can_spend, message.from_user.id,
                                message.from_user.full_name, data["m_account"], amount)
    except sheets.AccountError as exc:
        await message.answer(f"⚠️ {exc}\n\nBoshqa summa kiriting yoki bekor qiling.")
        return
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return

    await state.update_data(m_rate=None)
    await state.set_state(Flow.m_pay_note)
    await message.answer("Izoh yozing (masalan: <code>1-bosqich uchun</code>) "
                         f"yoki «{BTN_NO_NOTE}» bosing.", reply_markup=note_menu)


@dp.message(Flow.m_pay_rate)
async def m_pay_rate(message: Message, state: FSMContext) -> None:
    rate = parse_amount(message.text or "")
    if rate is None or rate < 100:
        await message.answer("Kursni to'g'ri kiriting. Masalan: <code>11900</code>")
        return
    data = await state.get_data()
    try:
        acc_amount = round(sheets.convert(data["m_amount"], data["m_cur"], data["a_cur"], rate), 2)
        await asyncio.to_thread(sheets.ensure_can_spend, message.from_user.id,
                                message.from_user.full_name, data["m_account"], acc_amount)
    except sheets.AccountError as exc:
        await message.answer(f"⚠️ {exc}\n\nBoshqa kurs yoki summa kiriting.")
        return
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return

    await state.update_data(m_rate=rate, m_acc_amount=acc_amount)
    await state.set_state(Flow.m_pay_note)
    await message.answer(
        f"{format_money(data['m_amount'], data['m_cur'])} ÷ {format_money(rate)} = "
        f"<b>{format_money(acc_amount, data['a_cur'])}</b>\n"
        f"<i>Kassadan shuncha ayriladi.</i>\n\n"
        f"Izoh yozing yoki «{BTN_NO_NOTE}» bosing.",
        reply_markup=note_menu,
    )


@dp.message(Flow.m_pay_note)
async def m_pay_note(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    note = "" if text == BTN_NO_NOTE else text[:200]
    data = await state.get_data()
    try:
        res = await asyncio.to_thread(
            sheets.pay_master, message.from_user.id, message.from_user.full_name,
            data["m_account"], data["m_name"], data["m_amount"], note, data.get("m_rate"),
        )
        rep = next(r for r in await asyncio.to_thread(
            sheets.get_master_report, message.from_user.id,
            message.from_user.full_name, data["m_account"]) if r["name"] == data["m_name"])
        bal = await asyncio.to_thread(sheets.get_balance, message.from_user.id,
                                      message.from_user.full_name, data["m_account"])
    except sheets.AccountError as exc:
        await state.clear(); await message.answer(f"⚠️ {exc}", reply_markup=menu_for(message)); return
    except sheets.SheetsError as exc:
        logger.warning(f"To'lovni yozib bo'lmadi: {exc}")
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return

    await state.clear()
    m_cur, a_cur = res["master_currency"], res["account_currency"]
    qolgan = ("✅ to'liq to'langan" if abs(rep["left"]) < 0.005 else
              f"Qolgan: <b>{format_money(rep['left'], m_cur)}</b>")
    kassa = ("" if m_cur == a_cur else
             f" <i>(kassadan {format_money(res['account_amount'], a_cur)})</i>")
    await message.answer(
        f"💵 <b>{data['m_name']}</b> ga to'lov berildi: "
        f"{format_money(res['master_amount'], m_cur)}{kassa}\n"
        f"{qolgan}\n\n"
        f"«{data['m_account']}» qoldig'i: <b>{format_money(bal['balance'], a_cur)}</b>",
        reply_markup=menu_for(message),
    )


# --- Hisobot ---

@dp.message(F.text == BTN_MASTER_REPORT)
async def m_start_report(message: Message, state: FSMContext) -> None:
    if not can_use_masters(message.from_user.id):
        return
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
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return
    await state.clear()
    await message.answer(format_master_report(chosen, rows, cur), reply_markup=menu_for(message))


# --- Kelishuvni o'zgartirish ---

@dp.message(F.text == BTN_EDIT_AGREED)
async def m_start_edit(message: Message, state: FSMContext) -> None:
    if not can_use_masters(message.from_user.id):
        return
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
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return
    if not masters:
        await state.clear()
        await message.answer(f"«{chosen}» hisobida usta yo'q.", reply_markup=masters_menu)
        return
    await state.update_data(m_account=chosen)
    await state.set_state(Flow.m_pick_edit)
    await message.answer("Kimning kelishuvi o'zgarsin?",
                         reply_markup=master_keyboard([m for m, _, _ in masters]))


@dp.message(Flow.m_pick_edit)
async def m_edit_pick(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        masters = await asyncio.to_thread(sheets.get_masters, message.from_user.id,
                                          message.from_user.full_name, data["m_account"])
        names = [m for m, _, _ in masters]
        chosen = next((m for m in names if m == (message.text or "")), None)
        if chosen is None:
            await message.answer("Iltimos, tugmalardan tanlang.",
                                 reply_markup=master_keyboard(names))
            return
        eski, cur = next((a, c) for m, a, c in masters if m == chosen)
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return

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
        cur = await asyncio.to_thread(sheets.get_master_currency, message.from_user.id,
                                      message.from_user.full_name, data["m_account"], data["m_name"])
    except sheets.AccountError as exc:
        await state.clear(); await message.answer(f"⚠️ {exc}", reply_markup=menu_for(message)); return
    except sheets.SheetsError:
        await state.clear(); await message.answer(SHEETS_ERROR_TEXT, reply_markup=menu_for(message)); return

    await state.clear()
    await message.answer(
        f"✏️ <b>{data['m_name']}</b> kelishuvi: {format_money(agreed, cur)}",
        reply_markup=menu_for(message),
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
