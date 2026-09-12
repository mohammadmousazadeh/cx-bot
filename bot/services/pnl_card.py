"""Generate PnL share card image."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont


def generate_pnl_card(
    user_id: int,
    pnl_percent: float,
    total_bal: float,
    join_date: str,
) -> io.BytesIO:
    width, height = 1080, 1080
    img = Image.new("RGB", (width, height), "#0B1120")
    draw = ImageDraw.Draw(img)

    try:
        font_huge = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        font_normal = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        font_huge = font_large = font_normal = font_small = ImageFont.load_default()

    # Header bar
    draw.rectangle([(0, 0), (width, 140)], fill="#111827")
    draw.text((80, 45), "CX Trader Card", fill="#38BDF8", font=font_large)

    pnl_color = "#00C087" if pnl_percent >= 0 else "#FF3B69"
    sign = "+" if pnl_percent >= 0 else ""

    draw.text((80, 220), "Performance", fill="#94A3B8", font=font_normal)
    draw.text((80, 280), f"{sign}{pnl_percent:,.2f}%", fill=pnl_color, font=font_huge)

    draw.line([(80, 400), (1000, 400)], fill="#1E293B", width=3)

    draw.text((80, 450), "Total Balance", fill="#94A3B8", font=font_normal)
    draw.text((80, 500), f"{total_bal:,.2f} TON", fill="#F8FAFC", font=font_large)

    draw.text((80, 600), "Trader ID", fill="#94A3B8", font=font_normal)
    draw.text((80, 650), str(user_id), fill="#F8FAFC", font=font_large)

    draw.text((80, 750), "Member since", fill="#94A3B8", font=font_normal)
    draw.text((80, 800), str(join_date)[:19], fill="#F8FAFC", font=font_normal)

    draw.line([(80, 900), (1000, 900)], fill="#1E293B", width=3)
    ref_link = f"https://t.me/cx_bot?start={user_id}"
    draw.text((80, 940), "Invite link:", fill="#64748B", font=font_small)
    draw.text((80, 980), ref_link, fill="#38BDF8", font=font_small)

    bio = io.BytesIO()
    img.save(bio, "PNG", optimize=True)
    bio.seek(0)
    return bio
