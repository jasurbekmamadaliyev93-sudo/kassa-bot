"""
Google Sheets — botning yagona ma'lumotlar ombori.

Tuzilishi:
- Har bir foydalanuvchi uchun ALOHIDA list (tab), nomi foydalanuvchi ismi bilan.
  Ustunlari: Sana | Hisob | Turi | Summa | Izoh | Usta
- "_Users"    — xizmat listi: User ID | Ism | List nomi
- "_Hisoblar" — xizmat listi: User ID | Hisob | Valyuta
- "_Ustalar"  — xizmat listi: User ID | Hisob | Usta | Kelishilgan | Izoh

Har bir foydalanuvchining bir nechta hisobi bo'lishi mumkin (masalan "Imzo showroom"
va "Shaxsiy"), ular bir-biriga umuman aralashmaydi. Har bir hisob ichida ustalar
bo'yicha kelishuv yuritiladi: to'lov oddiy CHIQIM sifatida yoziladi (ya'ni hisob
balansidan ayriladi) va "Usta" ustuni orqali kimga berilgani belgilanadi.
"""
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

USER_HEADERS = ["Sana", "Hisob", "Turi", "Summa", "Izoh", "Usta"]
USER_HEADERS_LEGACY = ["Sana", "Hisob", "Turi", "Summa", "Izoh"]   # "Usta" ustunisiz eski format
COL_DATE, COL_ACCOUNT, COL_TYPE, COL_AMOUNT, COL_NOTE, COL_MASTER = 0, 1, 2, 3, 4, 5

USERS_SHEET = "_Users"
USERS_HEADERS = ["User ID", "Ism", "List nomi"]

ACCOUNTS_SHEET = "_Hisoblar"
ACCOUNTS_HEADERS = ["User ID", "Hisob", "Valyuta"]
ACCOUNTS_HEADERS_LEGACY = ["User ID", "Hisob"]      # eski format (valyutasiz)

MASTERS_SHEET = "_Ustalar"
MASTERS_HEADERS = ["User ID", "Hisob", "Usta", "Kelishilgan", "Izoh"]

DEFAULT_CURRENCY = "so'm"
CURRENCIES = ["so'm", "$"]

DEFAULT_ACCOUNTS = ["Shaxsiy"]   # yangi foydalanuvchida faqat shu ochiladi,
                                 # qolganini o'zi qo'shadi

LABEL_INCOME = "Kirim"
LABEL_EXPENSE = "Chiqim"

MAX_ACCOUNT_NAME = 40
MAX_ACCOUNTS = 20

_spreadsheet = None
_enabled = False
_lock = threading.RLock()          # bir vaqtda bir nechta so'rov kelishi mumkin
_user_ws_cache: dict[int, object] = {}
_accounts_cache: dict[int, list] = {}
_masters_cache: dict[tuple, list] = {}      # (user_id, hisob) -> [(usta, kelishilgan)]


class SheetsError(Exception):
    """Google Sheets bilan ishlashda yuzaga kelgan xato."""


class AccountError(Exception):
    """Hisob bilan bog'liq mantiqiy xato (foydalanuvchiga ko'rsatiladi)."""


def _local_now() -> datetime:
    """Toshkent vaqti (server UTC'da ishlaydi, shuning uchun aniq belgilaymiz)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tashkent"))
    except Exception:  # noqa: BLE001
        return datetime.now(timezone(timedelta(hours=5)))


def is_enabled() -> bool:
    return _enabled


# --------------------------------------------------------------------------
# Ishga tushirish
# --------------------------------------------------------------------------

def init_sheets(credentials_file: str, sheet_id: str, credentials_json: str = "") -> bool:
    global _spreadsheet, _enabled

    if not sheet_id or not (credentials_json or credentials_file):
        logger.error("Google Sheets sozlanmagan: GOOGLE_SHEET_ID yoki kalit berilmagan.")
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.error("gspread/google-auth o'rnatilmagan. 'pip install -r requirements.txt' ni bajaring.")
        return False

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    try:
        if credentials_json:
            creds = Credentials.from_service_account_info(json.loads(credentials_json), scopes=scopes)
        else:
            if not os.path.exists(credentials_file):
                logger.error(f"Google kalit fayli topilmadi: {credentials_file}")
                return False
            creds = Credentials.from_service_account_file(credentials_file, scopes=scopes)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(f"Google kaliti noto'g'ri formatda: {exc}")
        return False

    try:
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_id)
        _spreadsheet = spreadsheet
        _ensure_service_sheet(USERS_SHEET, USERS_HEADERS)
        _ensure_service_sheet(ACCOUNTS_SHEET, ACCOUNTS_HEADERS, ACCOUNTS_HEADERS_LEGACY)
        _ensure_service_sheet(MASTERS_SHEET, MASTERS_HEADERS)
        _enabled = True
        logger.info("Google Sheets bilan ulanish muvaffaqiyatli o'rnatildi.")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Google Sheets bilan ulanib bo'lmadi: {exc}")
        _spreadsheet = None
        _enabled = False
        return False


def _ensure_service_sheet(title: str, headers: list, legacy: list | None = None):
    """Xizmat listi mavjudligini (va sarlavhasini) ta'minlaydi.
    Eski sarlavha topilsa — uni JOYIDA yangilaydi, ma'lumot qatorlari surilmaydi."""
    try:
        ws = _spreadsheet.worksheet(title)
    except Exception:  # noqa: BLE001 - WorksheetNotFound
        ws = _spreadsheet.add_worksheet(title=title, rows=200, cols=len(headers))
        ws.append_row(headers)
        return ws

    first = ws.row_values(1)
    if first == headers:
        return ws
    if legacy and first == legacy:
        ws.delete_rows(1)
        ws.insert_row(headers, index=1)
        logger.info(f"«{title}» listi sarlavhasi yangi formatga o'tkazildi.")
        return ws
    ws.insert_row(headers, index=1)
    return ws


AMOUNT_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}


def _apply_amount_format(ws) -> None:
    """Summa ustunini '20 000' ko'rinishida ko'rsatadi.
    Qiymat baribir SON bo'lib qoladi — jadvalda formulalar ishlayveradi."""
    try:
        ws.format("D2:D", AMOUNT_FORMAT)
    except Exception as exc:  # noqa: BLE001 - format bo'lmasa ham bot ishlashda davom etsin
        logger.warning(f"Summa ustuni formatini qo'llab bo'lmadi: {exc}")


def _check_ready():
    if not _enabled or _spreadsheet is None:
        raise SheetsError("Google Sheets ulanmagan")


# --------------------------------------------------------------------------
# Foydalanuvchi listi
# --------------------------------------------------------------------------

_INVALID_TITLE_CHARS = set("[]*?/\\:'")


def _safe_title(name: str, user_id: int) -> str:
    cleaned = "".join(ch for ch in (name or "") if ch not in _INVALID_TITLE_CHARS).strip()
    cleaned = " ".join(cleaned.split())[:60]
    if not cleaned or cleaned.startswith("_"):
        cleaned = f"User {user_id}"
    return cleaned


def _existing_titles() -> set:
    return {ws.title for ws in _spreadsheet.worksheets()}


def _lookup_user_title(user_id: int) -> str | None:
    ws = _spreadsheet.worksheet(USERS_SHEET)
    for row in ws.get_all_values()[1:]:
        if len(row) >= 3 and row[0].strip() == str(user_id):
            return row[2].strip() or None
    return None


def _register_user(user_id: int, name: str, title: str):
    _spreadsheet.worksheet(USERS_SHEET).append_row([str(user_id), name, title])


def get_user_sheet(user_id: int, user_name: str):
    """Foydalanuvchining listini qaytaradi, bo'lmasa yaratadi."""
    _check_ready()
    with _lock:
        cached = _user_ws_cache.get(user_id)
        if cached is not None:
            return cached

        try:
            title = _lookup_user_title(user_id)
            if title:
                try:
                    ws = _spreadsheet.worksheet(title)
                except Exception:  # noqa: BLE001 - list qo'lda o'chirilgan bo'lsa
                    ws = _spreadsheet.add_worksheet(title=title, rows=1000, cols=len(USER_HEADERS))
                    ws.append_row(USER_HEADERS)
            else:
                title = _safe_title(user_name, user_id)
                taken = _existing_titles()
                if title in taken:
                    title = f"{title} ({user_id})"[:95]
                ws = _spreadsheet.add_worksheet(title=title, rows=1000, cols=len(USER_HEADERS))
                ws.append_row(USER_HEADERS)
                _register_user(user_id, user_name, title)
                # Hisoblar get_accounts() ichida bir marta yaratiladi — bu yerda takrorlamaymiz

            first = ws.row_values(1)
            if first != USER_HEADERS:
                if first == USER_HEADERS_LEGACY:
                    # Faqat oxiriga ustun qo'shamiz — mavjud qatorlar joyida qoladi
                    ws.update_cell(1, len(USER_HEADERS), USER_HEADERS[-1])
                    logger.info(f"«{title}» listiga «Usta» ustuni qo'shildi.")
                else:
                    ws.insert_row(USER_HEADERS, index=1)

            _apply_amount_format(ws)
            _user_ws_cache[user_id] = ws
            return ws
        except SheetsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc


# --------------------------------------------------------------------------
# Hisoblar
# --------------------------------------------------------------------------

def _set_accounts(user_id: int, accounts: list):
    ws = _spreadsheet.worksheet(ACCOUNTS_SHEET)
    for acc in accounts:
        ws.append_row([str(user_id), acc, DEFAULT_CURRENCY])
    _accounts_cache[user_id] = [(a, DEFAULT_CURRENCY) for a in accounts]


def get_accounts_full(user_id: int, user_name: str = "") -> list:
    """[(hisob nomi, valyuta), ...] ro'yxati (bo'lmasa standartlari yaratiladi)."""
    _check_ready()
    with _lock:
        cached = _accounts_cache.get(user_id)
        if cached is not None:
            return list(cached)
        try:
            ws = _spreadsheet.worksheet(ACCOUNTS_SHEET)
            found, seen = [], set()
            for row in ws.get_all_values()[1:]:
                if len(row) < 2 or row[0].strip() != str(user_id):
                    continue
                acc = row[1].strip()
                cur = (row[2].strip() if len(row) > 2 and row[2].strip() else DEFAULT_CURRENCY)
                if acc and acc.casefold() not in seen:
                    seen.add(acc.casefold())
                    found.append((acc, cur))
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc

        if not found:
            _set_accounts(user_id, DEFAULT_ACCOUNTS)
            found = [(a, DEFAULT_CURRENCY) for a in DEFAULT_ACCOUNTS]
        _accounts_cache[user_id] = found
        return list(found)


def get_accounts(user_id: int, user_name: str = "") -> list:
    """Faqat hisob nomlari (tugmalar uchun)."""
    return [a for a, _ in get_accounts_full(user_id, user_name)]


def get_currency(user_id: int, user_name: str, account: str) -> str:
    """Hisobning valyutasi (topilmasa standart)."""
    for a, cur in get_accounts_full(user_id, user_name):
        if a.casefold() == account.casefold():
            return cur or DEFAULT_CURRENCY
    return DEFAULT_CURRENCY


def set_account_currency(user_id: int, user_name: str, account: str, currency: str) -> None:
    """Mavjud hisobning valyutasini o'zgartiradi."""
    _check_ready()
    if currency not in CURRENCIES:
        raise AccountError(f"Noma'lum valyuta: {currency}")
    with _lock:
        existing = get_accounts_full(user_id, user_name)
        match = next((a for a, _ in existing if a.casefold() == account.casefold()), None)
        if match is None:
            raise AccountError(f"«{account}» nomli hisob topilmadi.")
        try:
            ws = _spreadsheet.worksheet(ACCOUNTS_SHEET)
            for idx, row in enumerate(ws.get_all_values(), start=1):
                if idx == 1:
                    continue
                if (len(row) >= 2 and row[0].strip() == str(user_id)
                        and row[1].strip().casefold() == match.casefold()):
                    ws.update_cell(idx, 3, currency)
                    break
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc
        _accounts_cache[user_id] = [
            (a, currency if a.casefold() == match.casefold() else c) for a, c in existing
        ]


def add_account(user_id: int, user_name: str, name: str, currency: str = DEFAULT_CURRENCY) -> str:
    """Yangi hisob qo'shadi. Muvaffaqiyatli bo'lsa hisob nomini qaytaradi."""
    _check_ready()
    clean = " ".join((name or "").split())
    if not clean:
        raise AccountError("Hisob nomi bo'sh bo'lishi mumkin emas.")
    if len(clean) > MAX_ACCOUNT_NAME:
        raise AccountError(f"Hisob nomi juda uzun (ko'pi bilan {MAX_ACCOUNT_NAME} ta belgi).")

    with _lock:
        existing = get_accounts_full(user_id, user_name)
        if len(existing) >= MAX_ACCOUNTS:
            raise AccountError(f"Hisoblar soni chegarasi ({MAX_ACCOUNTS} ta) to'ldi.")
        if any(a.casefold() == clean.casefold() for a, _ in existing):
            raise AccountError(f"«{clean}» nomli hisob allaqachon bor.")
        if currency not in CURRENCIES:
            currency = DEFAULT_CURRENCY
        try:
            _spreadsheet.worksheet(ACCOUNTS_SHEET).append_row([str(user_id), clean, currency])
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc
        _accounts_cache[user_id] = existing + [(clean, currency)]
        return clean


def count_records(user_id: int, user_name: str, account: str) -> int:
    ws = get_user_sheet(user_id, user_name)
    try:
        rows = ws.get_all_values()[1:]
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(str(exc)) from exc
    return sum(
        1 for r in rows
        if len(r) > COL_AMOUNT and r[COL_ACCOUNT].strip().casefold() == account.casefold()
    )


def delete_account(user_id: int, user_name: str, name: str) -> None:
    """Hisobni o'chiradi. Yozuvlari bo'lsa o'chirishga ruxsat bermaydi
    (pul ma'lumoti sezdirmay yo'qolib qolmasligi uchun)."""
    _check_ready()
    with _lock:
        existing = get_accounts_full(user_id, user_name)
        match = next((a for a, _ in existing if a.casefold() == name.casefold()), None)
        if match is None:
            raise AccountError(f"«{name}» nomli hisob topilmadi.")
        if len(existing) <= 1:
            raise AccountError("Kamida bitta hisob qolishi kerak.")

        n = count_records(user_id, user_name, match)
        if n:
            raise AccountError(
                f"«{match}» hisobida {n} ta yozuv bor — o'chirib bo'lmaydi.\n"
                f"Avval o'sha yozuvlarni jadvaldan o'chiring."
            )

        try:
            ws = _spreadsheet.worksheet(ACCOUNTS_SHEET)
            targets = [
                idx for idx, row in enumerate(ws.get_all_values(), start=1)
                if idx > 1 and len(row) >= 2 and row[0].strip() == str(user_id)
                and row[1].strip().casefold() == match.casefold()
            ]
            for idx in reversed(targets):   # pastdan yuqoriga — raqamlar surilmasligi uchun
                ws.delete_rows(idx)
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc
        _accounts_cache[user_id] = [(a, c) for a, c in existing if a.casefold() != match.casefold()]


# --------------------------------------------------------------------------
# Yozuvlar
# --------------------------------------------------------------------------

def _parse_amount(raw) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return 0.0
    for ch in (" ", " ", " ", "'", "`"):
        text = text.replace(ch, "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _rows_of(user_id: int, user_name: str, account: str | None = None):
    ws = get_user_sheet(user_id, user_name)
    try:
        values = ws.get_all_values()
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(str(exc)) from exc
    result = []
    for idx, row in enumerate(values, start=1):
        if idx == 1 or len(row) <= COL_AMOUNT:
            continue
        if account is not None and row[COL_ACCOUNT].strip().casefold() != account.casefold():
            continue
        result.append((idx, row))
    return ws, result


def _fmt(amount: float) -> str:
    if abs(amount - round(amount)) < 0.005:
        return f"{round(amount):,.0f}".replace(",", " ")
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


def ensure_can_spend(user_id: int, user_name: str, account: str, amount: float) -> None:
    """Chiqim qoldiqdan oshsa AccountError ko'taradi — balans manfiyga tushmaydi."""
    current = get_balance(user_id, user_name, account)["balance"]
    if amount > current + 0.001:      # kasr xatoliklariga kichik yo'l qo'yiladi
        cur = get_currency(user_id, user_name, account)
        raise AccountError(
            f"«{account}» hisobida buncha mablag' yo'q.\n"
            f"Mavjud qoldiq: <b>{_fmt(current)} {cur}</b>, "
            f"siz esa {_fmt(amount)} {cur} chiqim qilmoqchisiz."
        )


def add_transaction(user_id: int, user_name: str, account: str,
                    tx_type: str, amount: float, note: str = "", master: str = "") -> None:
    """Yangi yozuv qo'shadi.
    Chiqim hisobdagi qoldiqdan oshsa, AccountError ko'tariladi — balans hech qachon
    manfiyga (qarzga) tushmaydi."""
    if tx_type not in ("income", "expense"):
        raise ValueError("tx_type 'income' yoki 'expense' bo'lishi kerak")
    if amount <= 0:
        raise ValueError("Summa musbat son bo'lishi kerak")

    if tx_type == "expense":
        ensure_can_spend(user_id, user_name, account, amount)

    ws = get_user_sheet(user_id, user_name)
    label = LABEL_INCOME if tx_type == "income" else LABEL_EXPENSE
    date_str = _local_now().strftime("%Y-%m-%d %H:%M")
    try:
        ws.append_row([date_str, account, label, amount, note, master])
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(str(exc)) from exc


def get_balance(user_id: int, user_name: str, account: str) -> dict:
    _, rows = _rows_of(user_id, user_name, account)
    income = expense = 0.0
    for _, row in rows:
        amount = _parse_amount(row[COL_AMOUNT])
        if row[COL_TYPE].strip() == LABEL_INCOME:
            income += amount
        elif row[COL_TYPE].strip() == LABEL_EXPENSE:
            expense += amount
    return {"income": income, "expense": expense, "balance": income - expense}


def get_all_balances(user_id: int, user_name: str) -> dict:
    """Barcha hisoblar: {hisob nomi: {income, expense, balance, currency}}."""
    accounts = get_accounts_full(user_id, user_name)
    _, rows = _rows_of(user_id, user_name, None)
    result = {a: {"income": 0.0, "expense": 0.0, "balance": 0.0, "currency": c}
              for a, c in accounts}
    by_fold = {a.casefold(): a for a, _ in accounts}
    for _, row in rows:
        key = by_fold.get(row[COL_ACCOUNT].strip().casefold())
        if key is None:
            continue
        amount = _parse_amount(row[COL_AMOUNT])
        if row[COL_TYPE].strip() == LABEL_INCOME:
            result[key]["income"] += amount
        elif row[COL_TYPE].strip() == LABEL_EXPENSE:
            result[key]["expense"] += amount
    for v in result.values():
        v["balance"] = v["income"] - v["expense"]
    return result


def get_history(user_id: int, user_name: str, account: str, limit: int = 10) -> list:
    _, rows = _rows_of(user_id, user_name, account)
    recent = rows[-limit:] if limit else rows
    return [
        {
            "type": "income" if row[COL_TYPE].strip() == LABEL_INCOME else "expense",
            "amount": _parse_amount(row[COL_AMOUNT]),
            "note": row[COL_NOTE] if len(row) > COL_NOTE else "",
            "created_at": row[COL_DATE],
            "account": row[COL_ACCOUNT],
            "master": row[COL_MASTER] if len(row) > COL_MASTER else "",
        }
        for _, row in reversed(recent)
    ]


def delete_last(user_id: int, user_name: str, account: str) -> dict | None:
    with _lock:
        ws, rows = _rows_of(user_id, user_name, account)
        if not rows:
            return None
        row_index, row = rows[-1]
        try:
            ws.delete_rows(row_index)
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc
        return {
            "type": "income" if row[COL_TYPE].strip() == LABEL_INCOME else "expense",
            "amount": _parse_amount(row[COL_AMOUNT]),
            "note": row[COL_NOTE] if len(row) > COL_NOTE else "",
            "created_at": row[COL_DATE],
            "account": row[COL_ACCOUNT],
        }


# --------------------------------------------------------------------------
# Ustalar (hisob ichidagi kelishuvlar)
# --------------------------------------------------------------------------

MAX_MASTER_NAME = 50


def get_masters(user_id: int, user_name: str, account: str) -> list:
    """[(usta nomi, kelishilgan summa), ...] — berilgan hisob ichida."""
    _check_ready()
    key = (user_id, account.casefold())
    with _lock:
        cached = _masters_cache.get(key)
        if cached is not None:
            return list(cached)
        try:
            ws = _spreadsheet.worksheet(MASTERS_SHEET)
            rows = ws.get_all_values()[1:]
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc

        found, seen = [], set()
        for row in rows:
            if len(row) < 4 or row[0].strip() != str(user_id):
                continue
            if row[1].strip().casefold() != account.casefold():
                continue
            name = row[2].strip()
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                found.append((name, _parse_amount(row[3])))
        _masters_cache[key] = found
        return list(found)


def add_master(user_id: int, user_name: str, account: str,
               name: str, agreed: float, note: str = "") -> str:
    """Hisob ichiga yangi usta/xizmat qo'shadi (kelishilgan summa bilan)."""
    _check_ready()
    clean = " ".join((name or "").split())
    if not clean:
        raise AccountError("Usta nomi bo'sh bo'lishi mumkin emas.")
    if len(clean) > MAX_MASTER_NAME:
        raise AccountError(f"Nom juda uzun (ko'pi bilan {MAX_MASTER_NAME} ta belgi).")
    if agreed < 0:
        raise AccountError("Kelishilgan summa manfiy bo'lishi mumkin emas.")

    with _lock:
        if any(m.casefold() == clean.casefold() for m, _ in get_masters(user_id, user_name, account)):
            raise AccountError(f"«{clean}» allaqachon ro'yxatda bor.")
        try:
            _spreadsheet.worksheet(MASTERS_SHEET).append_row(
                [str(user_id), account, clean, agreed, note])
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc
        _masters_cache.pop((user_id, account.casefold()), None)
        return clean


def set_master_agreed(user_id: int, user_name: str, account: str,
                      name: str, agreed: float) -> None:
    """Kelishilgan summani o'zgartiradi."""
    _check_ready()
    if agreed < 0:
        raise AccountError("Kelishilgan summa manfiy bo'lishi mumkin emas.")
    with _lock:
        match = next((m for m, _ in get_masters(user_id, user_name, account)
                      if m.casefold() == name.casefold()), None)
        if match is None:
            raise AccountError(f"«{name}» topilmadi.")
        try:
            ws = _spreadsheet.worksheet(MASTERS_SHEET)
            for idx, row in enumerate(ws.get_all_values(), start=1):
                if idx == 1 or len(row) < 3:
                    continue
                if (row[0].strip() == str(user_id)
                        and row[1].strip().casefold() == account.casefold()
                        and row[2].strip().casefold() == match.casefold()):
                    ws.update_cell(idx, 4, agreed)
                    break
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc
        _masters_cache.pop((user_id, account.casefold()), None)


def pay_master(user_id: int, user_name: str, account: str,
               name: str, amount: float, note: str = "") -> None:
    """Ustaga to'lov — oddiy CHIQIM sifatida yoziladi, ya'ni hisob balansidan ayriladi."""
    match = next((m for m, _ in get_masters(user_id, user_name, account)
                  if m.casefold() == name.casefold()), None)
    if match is None:
        raise AccountError(f"«{name}» topilmadi.")
    add_transaction(user_id, user_name, account, "expense", amount, note, master=match)
    _masters_cache.pop((user_id, account.casefold()), None)


def get_master_report(user_id: int, user_name: str, account: str) -> list:
    """Har bir usta bo'yicha: kelishilgan / berilgan / qolgan."""
    masters = get_masters(user_id, user_name, account)
    _, rows = _rows_of(user_id, user_name, account)

    paid: dict[str, float] = {m.casefold(): 0.0 for m, _ in masters}
    for _, row in rows:
        if len(row) <= COL_MASTER:
            continue
        tag = row[COL_MASTER].strip().casefold()
        if tag and tag in paid and row[COL_TYPE].strip() == LABEL_EXPENSE:
            paid[tag] += _parse_amount(row[COL_AMOUNT])

    result = []
    for m, agreed in masters:
        p = paid.get(m.casefold(), 0.0)
        result.append({"name": m, "agreed": agreed, "paid": p, "left": agreed - p})
    return result


def delete_master(user_id: int, user_name: str, account: str, name: str) -> None:
    """Ustani ro'yxatdan o'chiradi. To'lovi bo'lsa ruxsat berilmaydi."""
    _check_ready()
    with _lock:
        rep = next((r for r in get_master_report(user_id, user_name, account)
                    if r["name"].casefold() == name.casefold()), None)
        if rep is None:
            raise AccountError(f"«{name}» topilmadi.")
        if rep["paid"] > 0:
            raise AccountError(
                f"«{rep['name']}» ga {_fmt(rep['paid'])} to'lov qilingan — o'chirib bo'lmaydi.\n"
                f"Avval o'sha to'lovlarni jadvaldan o'chiring."
            )
        try:
            ws = _spreadsheet.worksheet(MASTERS_SHEET)
            targets = [idx for idx, row in enumerate(ws.get_all_values(), start=1)
                       if idx > 1 and len(row) >= 3 and row[0].strip() == str(user_id)
                       and row[1].strip().casefold() == account.casefold()
                       and row[2].strip().casefold() == name.casefold()]
            for idx in reversed(targets):
                ws.delete_rows(idx)
        except Exception as exc:  # noqa: BLE001
            raise SheetsError(str(exc)) from exc
        _masters_cache.pop((user_id, account.casefold()), None)
