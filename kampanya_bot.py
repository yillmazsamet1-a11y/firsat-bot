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
TEKRAR_SURESI_SN = 24 * 3600


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


def daha_once_paylasildi_mi(baslik: str, fiyat) -> bool:
    anahtar = anahtar_uret(baslik, fiyat)
    if not anahtar:
        return False
    simdi = time.time()
    try:
        with open(GECMIS_DOSYASI, "r", encoding="utf-8") as f:
            kayitlar = json.load(f)
        if not isinstance(kayitlar, dict):
            kayitlar = {}
    except Exception:
        kayitlar = {}

    temiz = {k: v for k, v in kayitlar.items() if isinstance(v, (int, float)) and (simdi - v) < TEKRAR_SURESI_SN}
    return anahtar in temiz


def paylasim_kaydet(baslik: str, fiyat) -> None:
    anahtar = anahtar_uret(baslik, fiyat)
    if not anahtar:
        return
    simdi = time.time()
    try:
        with open(GECMIS_DOSYASI, "r", encoding="utf-8") as f:
            kayitlar = json.load(f)
        if not isinstance(kayitlar, dict):
            kayitlar = {}
    except Exception:
        kayitlar = {}

    kayitlar = {k: v for k, v in kayitlar.items() if isinstance(v, (int, float)) and (simdi - v) < TEKRAR_SURESI_SN}
    kayitlar[anahtar] = simdi
    with open(GECMIS_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(kayitlar, f, ensure_ascii=False, indent=2)


def telegrama_gonder(foto_url: str, caption: str) -> bool:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ortam değişkeni tanımlı değil.")
    if foto_url:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                data={"chat_id": KANAL, "photo": foto_url, "caption": caption[:1024]},
                timeout=25,
            )
            if r.ok:
                return True
        except Exception:
            pass
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": KANAL, "text": caption, "disable_web_page_preview": False},
        timeout=20,
    )
    r.raise_for_status()
    return True


def urun_listesini_paylas(urunler: list[dict]) -> None:
    # Her ürün sözlüğünde 'title', 'satis', 'img', 'mesaj' alanları beklenir
    for u in urunler:
        baslik = u.get("title")
        fiyat = u.get("satis")
        mesaj = u.get("mesaj", "")
        img = u.get("img", "")

        if daha_once_paylasildi_mi(baslik, fiyat):
            print(f"Atlandı (aynı başlık ve fiyat 24 saat içinde paylaşıldı): {baslik} - {fiyat}")
            continue

        if telegrama_gonder(img, mesaj):
            paylasim_kaydet(baslik, fiyat)
            print(f"Paylaşıldı ve kaydedildi: {baslik} - {fiyat}")


def main() -> None:
    # --- KENDİ ÜRÜN ÇEKME VE PAYLAŞIM KODUN ---
    # Bulduğun ürünleri [{'title': ..., 'satis': ..., 'img': ..., 'mesaj': ...}] listesi olarak
    # urun_listesini_paylas(bulunan_urunler) fonksiyonuna aktarabilirsin.
    pass


if __name__ == "__main__":
    main()
