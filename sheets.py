"""
Google Sheets — botning yagona ma'lumotlar ombori.
Barcha kirim/chiqim yozuvlari faqat Google jadvalda saqlanadi:
qo'shish, balans hisoblash, tarix va o'chirish — hammasi shu modul orqali.

Ishlashi uchun kerak:
1. Google Cloud'da xizmat hisobi (service account) va uning JSON kaliti
2. Google Sheets jadvalini shu xizmat hisobi bilan (Editor huquqi bilan) ulashish
3. GOOGLE_SHEET_ID va GOOGLE_CREDENTIALS_FILE (yoki GOOGLE_CREDENTIALS_JSON) sozlamalari

Kalitni ikki xil usulda berish mumkin:
- GOOGLE_CREDENTIALS_FILE — JSON kalit faylining yo'li (lokal kompyuterda qulay)
- GOOGLE_CREDENTIALS_JSON — JSON kalitning butun matni (Railway kabi serverlarda qulay)

To'liq sozlash yo'riqnomasi README.md faylida.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Jadval ustunlari (tartibi muhim)
SHEET_HEADERS = ["Sana", "User ID", "Foydalanuvchi", "Turi", "Summa", "Izoh"]

COL_DATE = 0
COL_USER_ID = 1
COL_USER_NAME = 2
COL_TYPE = 3
COL_AMOUNT = 4
COL_NOTE = 5

LABEL_INCOME = "Kirim"
LABEL_EXPENSE = "Chiqim"

_worksheet = None
_enabled = False


class SheetsError(Exception):
    """Google Sheets bilan ishlashda yuzaga kelgan xato."""


def _local_now() -> datetime:
    """Toshkent vaqti (server UTC'da ishlagani uchun aniq belgilaymiz)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tashkent"))
    except Exception:  # noqa: BLE001 - tzdata bo'lmasa ham ishlashi kerak
        return datetime.now(timezone(timedelta(hours=5)))


def is_enabled() -> bool:
    return _enabled


def init_sheets(credentials_file: str, sheet_id: str, credentials_json: str = "") -> bool:
    """Google Sheets bilan ulanishni ishga tushiradi.
    Kalit ikki usulda berilishi mumkin: credentials_json (JSON matni, ustuvor)
    yoki credentials_file (JSON fayl yo'li)."""
    global _worksheet, _enabled

    if not sheet_id or not (credentials_json or credentials_file):
        logger.error("Google Sheets sozlanmagan: GOOGLE_SHEET_ID yoki kalit berilmagan.")
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.error(
            "gspread/google-auth o'rnatilmagan. 'pip install -r requirements.txt' ni bajaring."
        )
        return False

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    try:
        if credentials_json:
            info = json.loads(credentials_json)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
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
        worksheet = client.open_by_key(sheet_id).sheet1

        # Sarlavha qatori yo'q yoki eski formatda bo'lsa — yangisini birinchi qatorga qo'yamiz
        if worksheet.row_values(1) != SHEET_HEADERS:
            worksheet.insert_row(SHEET_HEADERS, index=1)

        _worksheet = worksheet
        _enabled = True
        logger.info("Google Sheets bilan ulanish muvaffaqiyatli o'rnatildi.")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Google Sheets bilan ulanib bo'lmadi: {exc}")
        _enabled = False
        return False


def _parse_amount(raw) -> float:
    """Jadvaldan o'qilgan summani songa aylantiradi.
    '380 000', '380000', '12,5', 380000.0 — hammasini tushunadi."""
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return 0.0
    # bo'sh joylar (oddiy va uzilmas), apostroflar olib tashlanadi
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


def _same_user(cell_value, user_id: int) -> bool:
    """Jadvaldagi User ID katagi berilgan foydalanuvchiga tegishlimi."""
    text = str(cell_value).strip()
    if not text:
        return False
    if text.endswith(".0"):
        text = text[:-2]
    return text == str(user_id)


def _rows():
    """Jadvalning barcha qatorlari (sarlavhasiz), har biri (qator_raqami, qator) ko'rinishida."""
    if not _enabled or _worksheet is None:
        raise SheetsError("Google Sheets ulanmagan")
    try:
        values = _worksheet.get_all_values()
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(str(exc)) from exc
    # 1-qator sarlavha, shuning uchun 2-qatordan boshlaymiz (Sheets 1-based)
    return [(i, row) for i, row in enumerate(values, start=1) if i > 1]


def _user_rows(user_id: int):
    result = []
    for row_index, row in _rows():
        if len(row) <= COL_AMOUNT:
            continue
        if _same_user(row[COL_USER_ID], user_id):
            result.append((row_index, row))
    return result


def add_transaction(user_id: int, user_name: str, tx_type: str, amount: float, note: str = "") -> None:
    """Yangi kirim/chiqim yozuvini jadvalga qo'shadi."""
    if tx_type not in ("income", "expense"):
        raise ValueError("tx_type 'income' yoki 'expense' bo'lishi kerak")
    if amount <= 0:
        raise ValueError("Summa musbat son bo'lishi kerak")
    if not _enabled or _worksheet is None:
        raise SheetsError("Google Sheets ulanmagan")

    label = LABEL_INCOME if tx_type == "income" else LABEL_EXPENSE
    date_str = _local_now().strftime("%Y-%m-%d %H:%M")
    try:
        _worksheet.append_row([date_str, str(user_id), user_name, label, amount, note])
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(str(exc)) from exc


def get_balance(user_id: int) -> dict:
    """Foydalanuvchining jami kirimi, chiqimi va qoldig'i."""
    income = 0.0
    expense = 0.0
    for _, row in _user_rows(user_id):
        amount = _parse_amount(row[COL_AMOUNT])
        if row[COL_TYPE].strip() == LABEL_INCOME:
            income += amount
        elif row[COL_TYPE].strip() == LABEL_EXPENSE:
            expense += amount
    return {"income": income, "expense": expense, "balance": income - expense}


def get_history(user_id: int, limit: int = 10) -> list:
    """So'nggi yozuvlar (eng yangisi birinchi)."""
    rows = _user_rows(user_id)
    recent = rows[-limit:] if limit else rows
    result = []
    for _, row in reversed(recent):
        result.append(
            {
                "type": "income" if row[COL_TYPE].strip() == LABEL_INCOME else "expense",
                "amount": _parse_amount(row[COL_AMOUNT]),
                "note": row[COL_NOTE] if len(row) > COL_NOTE else "",
                "created_at": row[COL_DATE],
            }
        )
    return result


def delete_last(user_id: int) -> dict | None:
    """Foydalanuvchining eng oxirgi yozuvini o'chiradi.
    O'chirilgan yozuvni qaytaradi, yozuv bo'lmasa None."""
    rows = _user_rows(user_id)
    if not rows:
        return None
    row_index, row = rows[-1]
    try:
        _worksheet.delete_rows(row_index)
    except Exception as exc:  # noqa: BLE001
        raise SheetsError(str(exc)) from exc
    return {
        "type": "income" if row[COL_TYPE].strip() == LABEL_INCOME else "expense",
        "amount": _parse_amount(row[COL_AMOUNT]),
        "note": row[COL_NOTE] if len(row) > COL_NOTE else "",
        "created_at": row[COL_DATE],
    }
