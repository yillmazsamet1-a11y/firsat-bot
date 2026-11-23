import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import requests
from playwright.sync_api import sync_playwright

URL = "https://www.amazon.com.tr/gp/goldbox"
BITIS = datetime(2026, 11, 28, 23, 59, 59)
MIN_IND = 25.0
MAX_ADET = 6
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amazon_hafiza.db")
DURUM_DOSYASI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "durum.json")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
KANAL = os.environ.get("TELEGRAM_CHANNEL_ID", "@anlikiindirim").strip()
TAG = os.environ.get("AMAZON_ASSOCIATE_TAG", "anlikiindirim-21").strip()
TR_TZ = timezone(timedelta(hours=3))

YASAK_BASLIK_KALIPLARI = [
    "günün en düşük fiyatı",
    "günün en düşük",
    "en düşük fiyat",
    "gülümseten kasım",
    "önceki:",
    "indirim",
    "fırsatı",
    "fırsat",
    "kampanya",
    "sepete özel",
    "kupon",
]

VARYANT_KELIMELERI = {
    "siyah", "beyaz", "kırmızı", "mavi", "lacivert", "yeşil", "gri", "pembe",
    "mor", "turuncu", "sarı", "kahverengi", "bej", "altın", "gümüş", "metalik",
    "black", "white", "blue", "green", "red", "grey", "gray", "pink", "silver",
    "adet", "ad", "li", "lı", "lu", "lü", "ml", "lt", "l", "gr", "g", "kg", "oz", "cm", "mm",
}


def simdi_tr_str() -> str:
    return datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M:%S")


def tr_para(tutar: float) -> str:
    return f"{tutar:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")


def gecerli_urun_basligi_mi(metin: str) -> bool:
    t = re.sub(r"\s+", " ", (metin or "")).strip()
    if len(t) < 12:
        return False
    low = t.lower()
    if "%" in low or "tl" in low or "₺" in low:
        return False
    if any(k in low for k in YASAK_BASLIK_KALIPLARI):
        return False
    harfler = re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]", t)
    if len(harfler) < 10:
        return False
    return True


def baslik_temizle(lines, fallback_title: str) -> str:
    for s in (lines or []):
        t = re.sub(r"\s+", " ", s).strip()
        if gecerli_urun_basligi_mi(t):
            return t
    fb = re.sub(r"\s+", " ", fallback_title or "").strip()
    if gecerli_urun_basligi_mi(fb):
        return fb
    return ""


def urun_anahtari(title: str, img_url: str) -> str:
    clean = re.sub(r"[^\w\sçğıöşüÇĞİÖŞÜ]", " ", (title or "").lower())
    kelimeler = [
        k for k in clean.split()
        if len(k) > 1
        and k not in VARYANT_KELIMELERI
        and not re.fullmatch(r"\d+(?:[.,]\d+)?(?:l|lt|ml|gr|g|kg|oz|cm|mm|adet|li|lı|lu|lü)?", k)
    ]
    cekirdek = " ".join(kelimeler[:5])
    img_base = re.sub(r"\._[A-Z0-9_,.-]+_\.", ".", (img_url or "").split("?")[0])
    ham = f"{cekirdek}|{img_base}"
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()[:24]


def satir_fiyatlari_al(metin: str):
    if not metin:
        return []
    low = metin.lower()
    if any(k in low for k in ["/100", "/kg", "/adet", "/parça", "/ml", "/l", "günün en düşük"]):
        return []

    norm = re.sub(r"(\d{1,3}(?:\.\d{3})*)\s+(\d{2})\s*(TL|₺)", r"\1,\2 \3", metin, flags=re.IGNORECASE)

    bulunanlar = []
    for m in re.finditer
