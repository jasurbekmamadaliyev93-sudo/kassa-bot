# Kassa Bot — Kirim-chiqim hisobi Telegram boti

Bu bot sizning olgan va bergan (sarflagan) pullaringizni hisob-kitob qilib beradi: kirim/chiqim qo'shish, joriy balansni ko'rish, so'nggi yozuvlar tarixini ko'rish va oxirgi yozuvni bekor qilish.

## 1. Bot tokenini olish (BotFather)

1. Telegramda **@BotFather** ni toping va oching.
2. `/newbot` buyrug'ini yuboring.
3. Bot uchun nom bering (masalan: `Mening Kassam`).
4. Bot uchun username bering, u `bot` bilan tugashi kerak (masalan: `mening_kassam_bot`).
5. BotFather sizga token beradi, masalan:
   `123456789:AAExampleTokenDoNotUseThisOne`
   Bu tokenni hech kimga bermang — u orqali botingizni to'liq boshqarish mumkin.

## 2. O'rnatish

Kompyuteringizda Python 3.10+ o'rnatilgan bo'lishi kerak.

```bash
# Loyiha papkasiga o'ting
cd kassa_bot

# Virtual muhit yaratish (tavsiya etiladi)
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Kutubxonalarni o'rnatish
pip install -r requirements.txt
```

## 3. Tokenni sozlash

Ikki usuldan birini tanlang:

**A usul (tavsiya etiladi) — muhit o'zgaruvchisi:**

```bash
export BOT_TOKEN="123456789:AAExampleTokenDoNotUseThisOne"   # Windows (PowerShell): $env:BOT_TOKEN="..."
```

**B usul — config.py fayli:**

```bash
cp config.example.py config.py
```

So'ng `config.py` faylini ochib, `BOT_TOKEN` qatoriga o'z tokeningizni yozing.

## 4. Google Sheets bilan bog'lash

Bu bosqich ixtiyoriy — agar uni o'tkazib yuborsangiz, bot baribir ishlayveradi, faqat ma'lumotlar Google jadvalga emas, faqat bot ichidagi bazaga saqlanadi.

### 4.1. Google Cloud'da loyiha va xizmat hisobi (service account) yaratish

1. [console.cloud.google.com](https://console.cloud.google.com) ga Google hisobingiz bilan kiring.
2. Tepada loyihalar ro'yxatidan **"New Project"** (Yangi loyiha) ni tanlang, nom bering (masalan `kassa-bot`) va **"Create"** bosing.
3. Yuqoridagi qidiruv qatoriga **"Google Sheets API"** deb yozing, uni tanlang va **"Enable"** bosing.
4. Xuddi shunday qidiruvdan **"Google Drive API"** ni ham toping va **"Enable"** qiling.
5. Chap menyudan **"APIs & Services" → "Credentials"** ga o'ting.
6. Tepada **"+ Create Credentials" → "Service account"** ni tanlang.
7. Xizmat hisobiga nom bering (masalan `kassa-bot-service`), **"Create and Continue"**, keyin **"Continue"**, so'ng **"Done"** bosing.
8. Yaratilgan xizmat hisobini ro'yxatdan bosib oching, **"Keys"** bo'limiga o'ting.
9. **"Add Key" → "Create new key"** ni tanlang, turi sifatida **JSON** ni tanlang va **"Create"** bosing — kompyuteringizga JSON fayl yuklab olinadi.
10. Shu yuklangan faylni `kassa_bot` papkasiga ko'chirib, nomini **`credentials.json`** deb o'zgartiring.
11. Shu JSON faylni oching (Notepad bilan) va `"client_email"` qatoridagi manzilni (masalan `kassa-bot-service@kassa-bot-123456.iam.gserviceaccount.com`) nusxalab oling — keyingi qadamda kerak bo'ladi.

### 4.2. Google Sheets jadvalini yaratish va ulashish

1. [sheets.google.com](https://sheets.google.com) da yangi bo'sh jadval yarating, nomini xohlaganingizcha qo'ying (masalan `Kassa hisobi`).
2. Yuqori o'ng burchakdagi **"Share"** (Ulashish) tugmasini bosing.
3. Ochilgan oynaga 4.1-qadamda nusxalagan `client_email` manzilini kiriting, huquq sifatida **"Editor"** ni tanlab **"Send"**/**"Share"** bosing.
4. Brauzeringizdagi manzil qatoridan jadval ID'sini oling — bu `https://docs.google.com/spreadsheets/d/`  bilan `/edit` orasidagi uzun harf-raqamlar qatori:
   ```
   https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890/edit
                                          └──────────── shu qism — GOOGLE_SHEET_ID ────────────┘
   ```

### 4.3. Botga sozlamalarni berish

`config.py` faylida (3-bosqichda yaratgan bo'lsangiz) quyidagilarni to'ldiring:

```python
GOOGLE_CREDENTIALS_FILE = "credentials.json"
GOOGLE_SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"   # o'zingizning jadval ID'ingiz
```

Yoki muhit o'zgaruvchilari orqali:

```bash
export GOOGLE_CREDENTIALS_FILE="credentials.json"
export GOOGLE_SHEET_ID="1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
```

`credentials.json` faylini hech kimga bermang va ochiq repozitoriyga (masalan GitHub'ga) yuklamang — u orqali jadvalingizga kirish mumkin.

## 5. Botni ishga tushirish

```bash
python3 bot.py
```

Terminalda xatolik chiqmasa, bot ishlay boshlaydi. Agar Google Sheets to'g'ri sozlangan bo'lsa, terminalda "Google Sheets bilan ulanish muvaffaqiyatli o'rnatildi." degan yozuv chiqadi. Endi Telegramda o'z botingizni topib, `/start` bosing.

## 6. Botdan foydalanish

- **➕ Kirim qo'shish** — olgan pulingizni kiritasiz (masalan: `500000 oylik maosh`)
- **➖ Chiqim qo'shish** — bergan/sarflagan pulingizni kiritasiz (masalan: `50000 transport`)
- **💰 Balans** — jami kirim, jami chiqim va qoldiqni ko'rsatadi
- **📜 Tarix** — so'nggi 10 ta yozuvni ko'rsatadi
- **🗑 Oxirgisini o'chirish** — adashib kiritilgan oxirgi yozuvni bekor qiladi

Ma'lumotlar `kassa.db` nomli SQLite faylida saqlanadi (bot ishga tushirilgan papkada avtomatik yaratiladi) va Google Sheets sozlangan bo'lsa, har bir kirim/chiqim jadvalga ham qator sifatida qo'shiladi. Har bir foydalanuvchining hisobi alohida yuritiladi.

## 7. Botni doim ishlab turadigan qilish (deploy)

Kompyuteringiz o'chirilganda ham bot ishlashi uchun uni serverga joylashtirish kerak. Eng oson bepul variantlar:

### Railway.app
1. github.com da yangi repository yarating va shu papkadagi fayllarni yuklang.
2. [railway.app](https://railway.app) da hisob oching, "New Project" → "Deploy from GitHub repo" ni tanlang.
3. Railway loyihasida **Variables** bo'limiga o'tib, `BOT_TOKEN`, `GOOGLE_SHEET_ID` o'zgaruvchilarini qo'shing va qiymatlarini kiriting.
4. `credentials.json` faylini repozitoriyga ham qo'shing (Google Sheets ishlashi uchun kerak) — shaxsiy/private repository qilishni unutmang, chunki bu faylni ochiq joyga qo'yish xavfli.
5. Start buyrug'i sifatida `python3 bot.py` ishlatilishini tekshiring (Railway buni avtomatik aniqlaydi, kerak bo'lsa `Procfile` qo'shing: `worker: python3 bot.py`).

### VPS (masalan Ubuntu server)
```bash
git clone <repo-url>
cd kassa_bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="..."
nohup python3 bot.py &      # fonda ishga tushirish
```
Server qayta yoqilganda ham avtomatik ishga tushishi uchun `systemd` service yoki `screen`/`tmux` dan foydalanishni maslahat beramiz.

## Fayllar tuzilishi

```
kassa_bot/
├── bot.py               # Bot logikasi (aiogram handlerlari)
├── database.py           # SQLite bilan ishlash funksiyalari
├── sheets.py              # Google Sheets bilan ishlash funksiyalari
├── requirements.txt      # Kerakli kutubxonalar
├── config.example.py     # Token/Sheets sozlamalari namunasi
├── credentials.json       # (o'zingiz qo'shasiz) Google xizmat hisobi kaliti
└── README.md              # Ushbu qo'llanma
```
