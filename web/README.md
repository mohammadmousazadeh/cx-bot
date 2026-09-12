# CX WebApps — Cloudflare Pages

این پوشه فقط فایل‌های استاتیک مینی‌اپ و لندینگ است.

## آپلود روی Cloudflare Pages

1. وارد [dash.cloudflare.com](https://dash.cloudflare.com) شوید
2. **Workers & Pages** → **Create** → **Pages** → Upload assets
3. محتویات پوشه `web/public/` را آپلود کنید
4. دامنه اختصاصی بگیرید (مثلاً `cx-app.pages.dev`)
5. همان URL را در `.env` سرور ربات به عنوان `WEBAPP_BASE_URL` بگذارید

```
WEBAPP_BASE_URL=https://YOUR_PROJECT.pages.dev
```

## فایل‌ها

| فایل | کاربرد |
|------|--------|
| `index.html` | ورودی مینی‌اپ |
| `app.html` | داشبورد اصلی WebApp |
| `binary.html` | باینری آپشن |
| `dashboard.html` | داشبورد معاملاتی |
| `game.html` | بازی |
| `swap.html` | سواپ |
| `wallet.html` | کیف پول |
| `landing.html` | لندینگ سایت |

ربات پایتونی را اینجا آپلود نکنید.
