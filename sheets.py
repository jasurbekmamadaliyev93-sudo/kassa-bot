"""
Google Sheets bilan ishlash uchun yordamchi funksiyalar.
Har bir kirim/chiqim yozuvi shu modul orqali Google jadvaliga qo'shiladi.

Ishlashi uchun kerak:
1. Google Cloud'da xizmat hisobi (service account) va uning JSON kaliti (masalan: credentials.json)
2. Google Sheets jadvalini shu xizmat hisobi bilan (Editor huquqi bilan) ulashish
3. GOOGLE_SHEET_ID va GOOGLE_CREDENTIALS_FILE (yoki GOOGLE_CREDENTIALS_JSON) sozlamalarini
   config.py yoki muhit o'zgaruvchilarida berish

Kalitni ikki xil usulda berish mumkin:
- GOOGLE_CREDENTIALS_FILE — JSON kalit faylining yo'li (lokal kompyuterda ishlatish uchun qulay)
- GOOGLE_CREDENTIALS_JSON — JSON kalit faylining butun matni (Railway kabi serverlarda muhit
  o'zgaruvchisi sifatida berish uchun qulay, fayl kerak emas)

To'liq sozlash yo'riqnomasi README.md faylida.
"""
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

SHEET_HEADERS = ["Sana", "Foydalanuvchi", "Turi", "Summa", "Izoh"]

_worksheet = None
_enabled = False


def is_enabled() -> bool:
    return _enabled


def init_sheets(credentials_file: str, sheet_id: str, credentials_json: str = "") -> bool:
    """Google Sheets bilan ulanishni ishga tushiradi.
    Kalit ikki usulda berilishi mumkin: credentials_json (JSON matnining o'zi, ustuvor)
    yoki credentials_file (JSON fayl yo'li).
    Muvaffaqiyatli bo'lsa True, sozlamalar yo'q yoki xato bo'lsa False qaytaradi
    (bot Google Sheets ishlamasa ham SQLite bilan ishlashda davom etadi)."""
    global _worksheet, _enabled

    if not sheet_id or not (credentials_json or credentials_file):
        logger.info("Google Sheets sozlanmagan — faqat lokal bazada saqlanadi.")
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.warning(
            "gspread/google-auth o'rnatilmagan. 'pip install -r requirements.txt' ni qayta bajaring."
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
                logger.warning(f"Google kalit fayli topilmadi: {credentials_file}")
                return False
            creds = Credentials.from_service_account_file(credentials_file, scopes=scopes)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"GOOGLE_CREDENTIALS_JSON noto'g'ri formatda: {exc}")
        return False

    try:
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_id)
        worksheet = spreadsheet.sheet1

        # Agar sarlavha qatori yo'q yoki noto'g'ri bo'lsa, uni birinchi qatorga qo'shamiz
        # (mavjud ma'lumotlar bo'lsa ham, ular pastga suriladi — hech narsa yo'qolmaydi)
        if worksheet.row_values(1) != SHEET_HEADERS:
            worksheet.insert_row(SHEET_HEADERS, index=1)

        _worksheet = worksheet
        _enabled = True
        logger.info("Google Sheets bilan ulanish muvaffaqiyatli o'rnatildi.")
        return True
    except Exception as exc:  # noqa: BLE001 - bot ishini to'xtatmaslik uchun keng ushlanadi
        logger.warning(f"Google Sheets bilan ulanib bo'lmadi: {exc}")
        _enabled = False
        return False


def append_transaction(user_name: str, tx_type: str, amount: float, note: str) -> None:
    """Yangi kirim/chiqim yozuvini jadvalga qo'shadi. Xato bo'lsa botni to'xtatmaydi,
    faqat log'ga yozadi."""
    if not _enabled or _worksheet is None:
        return
    try:
        label = "Kirim" if tx_type == "income" else "Chiqim"
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        _worksheet.append_row([date_str, user_name, label, amount, note])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Google Sheetsga yozishda xato: {exc}")


def delete_last_row_for_user() -> None:
    """Jadvaldagi eng oxirgi qatorni o'chiradi (bot ichidagi 'oxirgisini o'chirish' bilan mos holda).
    Eslatma: bu botning eng so'nggi yozuvi hisoblanadi, jadvalni qo'lda tahrirlagan bo'lsangiz
    natija mos kelmasligi mumkin."""
    if not _enabled or _worksheet is None:
        return
    try:
        all_values = _worksheet.get_all_values()
        last_row_index = len(all_values)
        if last_row_index > 1:  # 1-qator sarlavha, uni o'chirmaymiz
            _worksheet.delete_rows(last_row_index)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Google Sheetsdan o'chirishda xato: {exc}")
