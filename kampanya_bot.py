from datetime import datetime, timedelta, timezone
import json
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
KANAL = os.environ.get("TELEGRAM_CHANNEL_ID", "@anlikiindirim").strip()
TAG = os.environ.get("AMAZON_ASSOCIATE_TAG", "anlikiindirim-21").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GECMIS_DOSYASI = os.path.join(BASE_DIR, "paylasilan_gecmisi.json")
DURUM_DOSYASI = os.path.join(BASE_DIR, "durum.json")
TEKRAR_SURESI_SN = 24 * 3600

TR_TZ = timezone(timedelta(hours=3))
TARAMA_URL = os.environ.get(
    "AMAZON_DEAL_URL",
    "https://www.amazon.com.tr/deals",
).strip()


def simdi_tr_str() -> str:
    return datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M:%S")


def tr_para(tutar: float) -> str:
    return f"{tutar:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")


def baslik_temizle(ham_baslik: str) -> str:
    s = re.sub(r"\s+", " ", (ham_baslik or "")).strip()
    s = re.split(
        r"(?:\s*[-–—|]?\s*%?\s*\d{1,2}\s*%\s*indirim|\s*%\s*\d{1,2}\s*indirim|\s*\d+\s*günün\s*en\s*düşük\s*fiyat[ıi]|\s*önceki\s*fiyat|\s*kalan\s*süre|\s*\d{1,3}(?:\.\d{3})*,\d{2}\s*(?:TL|₺))",
        s,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    s = re.sub(r"\s*[-–—:,;]+\s*$", "", s).strip()
    if len(s) > 100:
        s = s[:97].rsplit(" ", 1)[0].strip()
    return s


def baslik_normalize(baslik: str) -> str:
    t = re.sub(r"[^\w\sçğıöşü]", " ", (baslik or "").lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def anahtar_uret(baslik: str, fiyat) -> str | None:
    if not baslik or fiyat is None:
        return None
    try:
        fiyat_norm = round(float(fiyat), 2)
    except (TypeError, ValueError):
        return None
    return f"{baslik_normalize(baslik)}|{fiyat_norm}"


def gecmis_yukle() -> dict:
    simdi = time.time()
    try:
        with open(GECMIS_DOSYASI, "r", encoding="utf-8") as f:
            kayitlar = json.load(f)
        if not isinstance(kayitlar, dict):
            kayitlar = {}
    except Exception:
        kayitlar = {}
    return {
        k: v
        for k, v in kayitlar.items()
        if isinstance(v, (int, float)) and (simdi - v) < TEKRAR_SURESI_SN
    }


def daha_once_paylasildi_mi(baslik: str, fiyat) -> bool:
    anahtar = anahtar_uret(baslik, fiyat)
    if not anahtar:
        return False
    return anahtar in gecmis_yukle()


def paylasim_kaydet(baslik: str, fiyat) -> None:
    anahtar = anahtar_uret(baslik, fiyat)
    if not anahtar:
        return
    kayitlar = gecmis_yukle()
    kayitlar[anahtar] = time.time()
    with open(GECMIS_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(kayitlar, f, ensure_ascii=False, indent=2)


def fiyat_sayiya_cevir(metin: str) -> float | None:
    if not metin:
        return None
    norm = re.sub(r"(\d{1,3}(?:\.\d{3})*)\s+(\d{2})\s*(TL|₺)", r"\1,\2", metin, flags=re.IGNORECASE)
    temiz = re.sub(r"[^\d,\.]", "", norm).strip()
    if not temiz:
        return None
    if "," in temiz:
        temiz = temiz.replace(".", "").replace(",", ".")
    elif temiz.count(".") > 1:
        temiz = temiz.replace(".", "")
    try:
        val = float(temiz)
        return val if 1.0 <= val <= 500000.0 else None
    except ValueError:
        return None


def firsatlari_cek(url: str, limit: int = 20) -> list[dict]:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            locale="tr-TR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.
