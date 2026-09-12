# Cloudflare Tunnel داخل Docker Compose

## یک‌بار راه‌اندازی (روی سرور یا لپ‌تاپ با دسترسی به Cloudflare)

### 1) نصب cloudflared (فقط برای ساخت Tunnel)

```bash
# Linux amd64 example
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/
```

### 2) لاگین و ساخت Tunnel

```bash
cloudflared tunnel login
cloudflared tunnel create cx-bot
```

خروجی `Tunnel ID` را بردارید.

فایل credentials معمولاً اینجاست:

```text
~/.cloudflared/<TUNNEL_ID>.json
```

کپی به پروژه:

```bash
cp ~/.cloudflared/<TUNNEL_ID>.json ./cloudflared/credentials.json
cp ./cloudflared/config.example.yml ./cloudflared/config.yml
```

### 3) ویرایش `cloudflared/config.yml`

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /etc/cloudflared/credentials.json

ingress:
  - hostname: api.YOUR_DOMAIN.com
    service: http://bot:8080
  - service: http_status:404
```

### 4) DNS

```bash
cloudflared tunnel route dns cx-bot api.YOUR_DOMAIN.com
```

یا در Cloudflare DNS یک CNAME:

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | api | `<TUNNEL_ID>.cfargotunnel.com` | ON |

### 5) `.env` ربات

```env
WEBAPP_API_PUBLIC_URL=https://api.YOUR_DOMAIN.com
WEBHOOK_URL=https://api.YOUR_DOMAIN.com
WEBHOOK_PATH=/telegram/webhook
WEBHOOK_SECRET=...
WEBAPP_CORS_ORIGINS=https://YOUR_PROJECT.pages.dev
```

### 6) اجرا

```bash
# بدون tunnel
docker compose up -d --build

# با tunnel دائمی
docker compose --profile tunnel up -d --build
```

### 7) تست

```bash
curl https://api.YOUR_DOMAIN.com/api/health
```

## امنیت

- `cloudflared/credentials.json` و `config.yml` را commit نکنید
- پورت 8080 را در فایروال می‌توانید فقط localhost نگه دارید؛ ترافیک از Tunnel می‌آید
