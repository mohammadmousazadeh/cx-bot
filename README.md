# CX Bot — Professional Telegram Trading Platform

پلتفرم ربات تلگرامی CX (کیف پول، استیکینگ، وام، VIP، پیش‌بینی، KYC، WebApp).

**نقشه استقرار Cloudflare + سرور:** [DEPLOY.md](./DEPLOY.md)

## معماری

| بخش | فناوری | محل استقرار |
|-----|--------|-------------|
| ربات | Python 3.12 + aiogram 3 | VPS / Railway / Docker (**نه** Cloudflare Workers) |
| مینی‌اپ و لندینگ | HTML/JS استاتیک | **Cloudflare Pages** |
| دیتابیس | SQLite (+ جدول transactions) | Volume روی سرور ربات |

## نصب محلی

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# BOT_TOKEN جدید + WEBAPP_BASE_URL دامنه Pages

python -m bot.main
```

## ساختار

```
bot/                 # پکیج اصلی ربات → سرور
web/public/          # فقط این را روی Cloudflare Pages آپلود کنید
Dockerfile
docker-compose.yml
DEPLOY.md
```

## امنیت

1. توکن لو‌رفته را از BotFather Revoke کنید.
2. `.env` را commit نکنید.
3. فقط `web/public` را روی Cloudflare بگذارید.
