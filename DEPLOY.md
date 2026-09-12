# نقشه استقرار حرفه‌ای CX (هزاران کاربر)

ربات تلگرام پایتونی با long-polling **روی Cloudflare Workers اجرا نمی‌شود**
(محدودیت زمان CPU و عدم پشتیبانی پایدار از Python polling).

معماری استاندارد برای مقیاس:

```
┌─────────────────────┐     ┌──────────────────────────┐
│  Cloudflare Pages   │     │  Server (VPS / Railway /  │
│  (فقط فرانت استاتیک) │     │  Render / Fly.io / Docker)│
│                     │     │                          │
│  • cx_web/*         │◄────│  • bot/ (Python aiogram) │
│  • landing          │ URL │  • SQLite یا Postgres    │
│  • mimetype HTML/JS │     │  • .env اسرار            │
└─────────────────────┘     └──────────────────────────┘
         ▲                              ▲
         │ WebApp button                │ Telegram API
         └────────── Telegram Client ───┘
```

---

## ۱) چه چیزی را کجا آپلود کنید

### A) Cloudflare Pages / Workers (استاتیک)

| مسیر محلی | مقصد Cloudflare | توضیح |
|-----------|-----------------|--------|
| `web/public/*` | **Pages** project | تمام مینی‌اپ‌ها |
| `cx_web/*` | همان Pages | معادل بالا |
| `CX_Site/index.html` | همان Pages به‌صورت `landing.html` یا root | لندینگ |

**نکنید:** آپلود `bot.py`، `.env`، دیتابیس، `node_modules` روی Pages.

بعد از Publish، آدرس نهایی را کپی کنید:

```text
https://YOUR_NAME.pages.dev
```

و در سرور ربات:

```env
WEBAPP_BASE_URL=https://YOUR_NAME.pages.dev
```

در ربات، لینک مینی‌اپ این‌طور ساخته می‌شود:

```text
{WEBAPP_BASE_URL}/app.html?v=...&bal=...&kyc=...
```

### B) سرور ربات (اجباری برای هزاران کاربر)

| مسیر محلی | مقصد سرور | توضیح |
|-----------|-----------|--------|
| کل پکیج `bot/` | سرور | کد اصلی |
| `requirements.txt` | سرور | وابستگی‌ها |
| `.env` | سرور (محرمانه) | توکن و ولت |
| `Dockerfile` + `docker-compose.yml` | سرور | اجرای پایدار |
| فایل `.db` | Volume دائمی | داده کاربران |

پیشنهاد سرویس‌ها:

| سرویس | مناسب برای | نکته |
|--------|------------|------|
| **Railway / Render / Fly.io** | شروع سریع | Docker یا native Python |
| **VPS (Hetzner / DigitalOcean)** | کنترل کامل | `docker compose up -d` |
| **Cloudflare Tunnel** | اگر VPS دارید و IP ثابت نمی‌خواهید | فقط تونل؛ خود ربات روی VPS است |

### C) چیزهایی که اصلاً آپلود/پابلیش نشوند

- `.env` و هر توکن
- `*.db` / `*.sqlite`
- `node_modules/`
- `bot.js` (نسخه قدیمی — فقط legacy)
- `__pycache__/`

---

## ۲) مراحل استقرار پیشنهادی

### مرحله ۱ — فرانت روی Cloudflare Pages

```bash
# فقط این پوشه
cd web/public
# Upload در داشبورد Cloudflare Pages
```

یا با Wrangler:

```bash
cd web
npx wrangler pages deploy public --project-name=cx-webapp
```

### مرحله ۲ — ربات روی سرور

```bash
# روی سرور
git clone <repo> && cd cx_bot
cp .env.example .env
# ویرایش .env: BOT_TOKEN جدید + WEBAPP_BASE_URL دامنه Pages

docker compose up -d --build
# یا:
pip install -r requirements.txt
python -m bot.main
```

### مرحله ۳ — امنیت

1. توکن لو‌رفته را از BotFather **Revoke** کنید.
2. توکن جدید فقط داخل `.env` سرور.
3. ولت‌ها را فقط در `.env` نگه دارید.
4. دسترسی SSH و پنل ادمین را محدود کنید.

---

## ۳) مقیاس هزاران کاربر — نکات حیاتی

| موضوع | الان | پیشنهاد تولید |
|--------|------|----------------|
| دیتابیس | SQLite | برای >5k کاربر فعال روزانه → Postgres |
| Polling | long-polling | در مقیاس بالا → Webhook + HTTPS |
| پرداخت TON | polling toncenter | Webhook / TonAPI index |
| موجودی | فیلد balance | جدول `transactions` (از قبل اضافه شده) |
| FSM storage | Memory | Redis برای چند instance |
| لاگ | stdout | فایل + monitoring |

جدول `transactions` در `init_db` از قبل ساخته می‌شود تا فاز ledger آماده باشد.

---

## ۴) ساختار نهایی ریپو

```
cx_bot/
├── bot/                    # ← سرور (Python)
│   ├── main.py             # entrypoint
│   ├── config.py
│   ├── texts.py
│   ├── states.py
│   ├── db/
│   ├── handlers/
│   ├── keyboards/
│   ├── middlewares/
│   └── services/
├── web/                    # ← Cloudflare Pages
│   ├── public/             # آپلود این پوشه
│   ├── wrangler.toml
│   └── README.md
├── cx_web/                 # منبع اصلی مینی‌اپ (کپی در web/public)
├── Dockerfile              # ← سرور
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── DEPLOY.md               # همین فایل
└── README.md
```

---

## ۵) اجرای محلی برای تست

```bash
pip install -r requirements.txt
cp .env.example .env   # توکن را بگذارید
python -m bot.main
```

فرانت را می‌توانید موقتاً با:

```bash
cd web/public && python -m http.server 3000
```

سرو کنید و `WEBAPP_BASE_URL=http://localhost:3000` بگذارید (فقط برای تست دسکتاپ؛ تلگرام WebApp به HTTPS نیاز دارد).

---

## ۶) WebApp initData + API امن

مینی‌اپ‌ها **هرگز** نباید به `?bal=` در URL اعتماد کنند.

### جریان امن

1. کاربر مینی‌اپ را از تلگرام باز می‌کند → `Telegram.WebApp.initData` تولید می‌شود
2. JS (`cx-auth.js`) درخواست به API می‌زند با هدر:
   `X-Telegram-Init-Data: <initData>`
3. سرور با HMAC-SHA256 و `BOT_TOKEN` صحت را چک می‌کند
4. موجودی واقعی از دیتابیس / Ledger برمی‌گردد

### تنظیمات

```env
API_ENABLED=true
API_PORT=8080
WEBAPP_API_PUBLIC_URL=https://api.your-domain.com
WEBAPP_CORS_ORIGINS=https://your-app.pages.dev
```

در Cloudflare Pages قبل از `cx-auth.js` یا در خود صفحه:

```html
<script>window.CX_API_BASE = "https://api.your-domain.com";</script>
```

### اندپوینت‌ها

| Method | Path | توضیح |
|--------|------|--------|
| GET | `/api/health` | سلامت |
| GET | `/api/me` | پروفایل + موجودی (نیاز به initData) |
| GET | `/api/transactions` | آخرین تراکنش‌های ledger |
| POST | `/api/ping` | فقط اعتبارسنجی initData |

API را با HTTPS پشت Cloudflare Tunnel / Nginx / Caddy در دسترس بگذارید.

---

## ۷) Webhook Production

### Polling در مقابل Webhook

| | Long-polling | Webhook |
|--|--------------|---------|
| مناسب | توسعه / تست | **تولید / هزاران کاربر** |
| نیاز HTTPS | خیر | **بله (الزامی)** |
| فعال‌سازی | `WEBHOOK_URL` خالی | `WEBHOOK_URL=https://api.example.com` |

### تنظیم `.env`

```env
WEBHOOK_URL=https://api.your-domain.com
WEBHOOK_PATH=/telegram/webhook
WEBHOOK_SECRET=یک_رشته_تصادفی_بلند
API_ENABLED=true
API_PORT=8080
```

آدرس نهایی که تلگرام صدا می‌زند:

```text
https://api.your-domain.com/telegram/webhook
```

### HTTPS

تلگرام فقط HTTPS می‌پذیرد. گزینه‌ها:

1. **Cloudflare Tunnel** (پیشنهادی با VPS)
   ```bash
   cloudflared tunnel --url http://127.0.0.1:8080
   ```
   دامنه Tunnel را در `WEBHOOK_URL` بگذارید.

2. **Nginx / Caddy** با گواهی Let’s Encrypt روی VPS

3. **Railway / Render / Fly.io** با دامنه HTTPS خودکار

### امنیت

- `WEBHOOK_SECRET` را در `.env` ثابت نگه دارید (بین ری‌استارت‌ها عوض نشود).
- aiogram هدر `X-Telegram-Bot-Api-Secret-Token` را چک می‌کند.
- پورت ۸۰۸۰ را مستقیم به اینترنت باز نکنید مگر پشت TLS.

### یک سرور برای همه

روی همان پورت:

- `POST /telegram/webhook` → آپدیت‌های تلگرام
- `GET  /api/me` → موجودی مینی‌اپ (initData)
- `GET  /api/health` → healthcheck

### بازگشت به polling

```env
WEBHOOK_URL=
```

ربات را ری‌استارت کنید.

---

## ۸) آزادسازی استیک / بازپرداخت وام + Redis FSM

### استیک
- قفل موجودی → `stake_lock` در ledger
- پس از موعد: دکمه **آزادسازی** → `stake_unlock` + `stake_reward`
- وضعیت در جدول `stakes.status` = `active` | `unlocked`

### وام
- دریافت → `loan_credit` + ثبت `loan_amount`
- بازپرداخت → دکمه **بازپرداخت** → `loan_repay` و صفر شدن بدهی
- تا تسویه وام، برداشت مسدود است

### Redis FSM (چند instance)

```env
REDIS_URL=redis://redis:6379/0
```

اگر خالی باشد از MemoryStorage استفاده می‌شود (یک پروسه).

با `docker compose up -d` سرویس Redis همراه ربات بالا می‌آید.

---

## ۹) Docker Compose + Cloudflare Tunnel

Tunnel می‌تواند داخل همان `docker compose` بالا بیاید.

```bash
# یک‌بار: ساخت tunnel و کپی credentials (راهنما: cloudflared/README.md)
cp cloudflared/config.example.yml cloudflared/config.yml
# ویرایش hostname و tunnel id
# کپی credentials.json

# اجرا با tunnel
docker compose --profile tunnel up -d --build
```

بدون tunnel (توسعه):

```bash
docker compose up -d --build
```

سرویس‌ها:
- `bot` — ربات + API روی 8080
- `redis` — FSM
- `cloudflared` — فقط با `--profile tunnel`
