# CX Bot — production image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for Pillow fonts (optional but useful)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot/ ./bot/
COPY crypto_pay.py ./
COPY .env.example ./

# Database file lives in a volume at runtime
VOLUME ["/app/data"]
ENV DB_NAME=/app/data/cx_database.db

CMD ["python", "-m", "bot.main"]
