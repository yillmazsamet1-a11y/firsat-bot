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
    for m in re.finditer(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*(?:TL|₺)?", norm):
        _, son = m.span()
        sag = norm[son:son + 8].lower()
        if any(sag.strip().startswith(x) for x in ["/", "gün", "lt", "l ", "ml", "kg", "adet", "%"]):
            continue
        ham = m.group(1)
        try:
            v = float(ham.replace(".", "").replace(",", "."))
            if 20.0 <= v <= 100000.0 and v not in bulunanlar:
                bulunanlar.append(v)
        except ValueError:
            pass

    if bulunanlar:
        return bulunanlar

    if "tl" in low or "₺" in low:
        for m in re.finditer(r"(\d{1,3}(?:\.\d{3})+|\d{2,5})\s*(?:TL|₺)", norm, flags=re.IGNORECASE):
            try:
                v = float(m.group(1).replace(".", ""))
                if 20.0 <= v <= 100000.0 and v not in bulunanlar:
                    bulunanlar.append(v)
            except ValueError:
                pass

    return bulunanlar


def db_ac():
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS paylasilan_urunler ("
        "asin TEXT PRIMARY KEY, paylasim_zamani INTEGER NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS paylasilan_imza ("
        "imza TEXT PRIMARY KEY, paylasim_zamani INTEGER NOT NULL)"
    )
    c.commit()
    return c


def atildi_mi(c, asin: str, imza: str) -> bool:
    esik = int(time.time()) - 86400
    v1 = c.execute(
        "SELECT 1 FROM paylasilan_urunler WHERE asin = ? AND paylasim_zamani >= ?",
        (asin, esik),
    ).fetchone() is not None
    v2 = c.execute(
        "SELECT 1 FROM paylasilan_imza WHERE imza = ? AND paylasim_zamani >= ?",
        (imza, esik),
    ).fetchone() is not None
    return v1 or v2


def kaydet(c, asin: str, imza: str):
    simdi = int(time.time())
    c.execute(
        "INSERT OR REPLACE INTO paylasilan_urunler (asin, paylasim_zamani) VALUES (?, ?)",
        (asin, simdi),
    )
    c.execute(
        "INSERT OR REPLACE INTO paylasilan_imza (imza, paylasim_zamani) VALUES (?, ?)",
        (imza, simdi),
    )
    c.commit()


def mevcut_durumu_oku():
    if os.path.exists(DURUM_DOSYASI):
        try:
            with open(DURUM_DOSYASI, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "son_tarama": None,
        "durum": "bekleniyor",
        "bildirimler": [],
        "hatalar": [],
        "son_urun": None,
        "son_paylasimlar": [],
        "toplam_paylasim": 0,
    }


def durum_kaydet(
    durum: str,
    bulunan_link: int = 0,
    paylasilan_adet: int = 0,
    yeni_urunler: list = None,
    hata_mesaji: str = None,
    uyari_mesaji: str = None,
):
    onceki = mevcut_durumu_oku()
    simdi = simdi_tr_str()
    bildirimler = onceki.get("bildirimler", [])
    hatalar = onceki.get("hatalar", [])
    paylasimlar = onceki.get("son_paylasimlar", [])

    if hata_mesaji:
        hatalar.insert(0, {"zaman": simdi, "mesaj": hata_mesaji})
        hatalar = hatalar[:25]

    if uyari_mesaji:
        bildirimler.insert(0, {"zaman": simdi, "tip": "uyari", "mesaj": uyari_mesaji})
    elif paylasilan_adet > 0:
        bildirimler.insert(0, {
            "zaman": simdi,
            "tip": "bilgi",
            "mesaj": f"{paylasilan_adet} yeni fırsat @anlikiindirim kanalına paylaşıldı ({bulunan_link} aday incelendi).",
        })
    bildirimler = bildirimler[:25]

    if yeni_urunler:
        for u in reversed(yeni_urunler):
            paylasimlar.insert(0, {
                "zaman": simdi,
                "asin": u["asin"],
                "title": u["title"],
                "satis": tr_para(u["satis"]),
                "eski": tr_para(u["eski"]) if u.get("eski") else None,
                "ind": f"%{u['ind']:.0f}",
                "img": u.get("img", ""),
                "link": f"https://www.amazon.com.tr/dp/{u['asin']}?tag={TAG}",
            })
        paylasimlar = paylasimlar[:15]

    son_urun = paylasimlar[0] if paylasimlar else onceki.get("son_urun")
    toplam = int(onceki.get("toplam_paylasim", 0)) + paylasilan_adet

    veri = {
        "son_tarama": simdi,
        "durum": durum,
        "bulunan_link": bulunan_link,
        "paylasilan_adet": paylasilan_adet,
        "toplam_paylasim": toplam,
        "bildirimler": bildirimler,
        "hatalar": hatalar,
        "son_urun": son_urun,
        "son_paylasimlar": paylasimlar,
    }
    with open(DURUM_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)


def gonder(foto_url: str, caption: str):
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN eksik")
    if foto_url:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                data={"chat_id": KANAL, "photo": foto_url, "caption": caption[:1024]},
                timeout=25,
            )
            if r.ok:
                return
        except Exception:
            pass
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": KANAL, "text": caption, "disable_web_page_preview": False},
        timeout=20,
    )
    r.raise_for_status()


def tara():
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            locale="tr-TR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        pg = ctx.new_page()
        pg.goto(URL, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3500)
        for _ in range(5):
            pg.evaluate("window.scrollBy(0, 1200)")
            pg.wait_for_timeout(1200)

        rows = pg.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href*="/dp/"], a[href*="/gp/product/"]'));
            const seen = new Set(), res = [];
            for (const a of links) {
                const h = a.getAttribute('href') || '';
                if (!h || seen.has(h)) continue;
                seen.add(h);
                let card = a;
                for (let i = 0; i < 6 && card && card.parentElement; i++) {
                    card = card.parentElement;
                    const t = card.innerText || '';
                    if (t.includes('TL') && t.length > 30 && t.length < 1200) break;
                }
                const imgEl = card ? card.querySelector('img') : null;
                const imgSrc = imgEl ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-src') || '') : '';
                const lines = (card ? (card.innerText || '') : '').split('\\n').map(s => s.trim()).filter(Boolean);
                let title = (a.innerText || '').trim();
                if (!title || title.length < 8 || title.startsWith('%')) {
                    title = lines.find(s => s.length >= 8 && !s.startsWith('%') && !s.includes('TL')) || '';
                }
                res.push({h, title, lines, imgSrc});
            }
            return res;
        }""")
        b.close()

    print(f"Sayfada {len(rows)} urun linki bulundu.")
    for r in rows:
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", urljoin(URL, r.get("h", "")))
        if not m:
            continue
        asin = m.group(1)
        title = baslik_temizle(r.get("lines") or [], r.get("title") or "")
        if not title:
            continue

        fiyatlar = []
        yuzde = None
        for s in (r.get("lines") or []):
            if s == title:
                continue
            if "%" in s and ("indirim" in s.lower() or re.match(r"^%\s*\d{1,2}\b", s.strip())):
                ym = re.search(r"%\s*(\d{1,2})", s)
                if ym:
                    yuzde = float(ym.group(1))
            for f in satir_fiyatlari_al(s):
                if f not in fiyatlar:
                    fiyatlar.append(f)

        if not fiyatlar:
            continue

        satis = fiyatlar[0]
        buyukler = [x for x in fiyatlar[1:] if x > satis]
        eski = min(buyukler) if buyukler else None

        if eski and eski > satis:
            ind = ((eski - satis) / eski) * 100.0
        elif yuzde is not None:
            ind = yuzde
            eski = satis / (1.0 - ind / 100.0) if ind < 95 else None
        else:
            ind = 0.0

        if ind < MIN_IND:
            continue

        img_url = urljoin(URL, r.get("imgSrc") or "") if r.get("imgSrc") else ""
        imza = urun_anahtari(title, img_url)
        out.append({"asin": asin, "imza": imza, "title": title, "satis": satis, "eski": eski, "ind": ind, "img": img_url})
    return out


def main():
    if datetime.now(TR_TZ).replace(tzinfo=None) > BITIS:
        print("Sure doldu.")
        durum_kaydet("uyari", uyari_mesaji="Bot çalışma süresi doldu.")
        return

    try:
        adaylar = tara()
        if not adaylar:
            print("Uygun yuksek indirimli urun bulunamadi.")
            durum_kaydet("uyari", bulunan_link=0, paylasilan_adet=0, uyari_mesaji="Taramada %25 ve üzeri uygun ürün bulunamadı.")
            return

        c = db_ac()
        tur_imzalari = set()
        paylasilan_listesi = []
        n = 0
        try:
            for u in adaylar:
                if n >= MAX_ADET:
                    break
                if u["imza"] in tur_imzalari or atildi_mi(c, u["asin"], u["imza"]):
                    continue
                tur_imzalari.add(u["imza"])
                link = f"https://www.amazon.com.tr/dp/{u['asin']}?tag={TAG}"
                f_str = tr_para(u["satis"])
                if u["eski"]:
                    f_satir = f"{f_str} | %{u['ind']:.0f} indirim (Eski: {tr_para(u['eski'])})"
                else:
                    f_satir = f"{f_str} | %{u['ind']:.0f} indirim"
                mesaj = (
                    f"{u['title']}\n\n"
                    f"{f_satir}\n\n"
                    f"{link}\n\n"
                    f"#tanitim #reklam #işbirliği #fırsat #indirim"
                )
                gonder(u["img"], mesaj)
                kaydet(c, u["asin"], u["imza"])
                paylasilan_listesi.append(u)
                n += 1
                print(f"[YAYINLANDI {n}] %{u['ind']:.0f} -> {u['title'][:45]} ({f_str})")
                time.sleep(1.5)
        finally:
            c.close()

        if n > 0:
            durum_kaydet("ok", bulunan_link=len(adaylar), paylasilan_adet=n, yeni_urunler=paylasilan_listesi)
        else:
            durum_kaydet("uyari", bulunan_link=len(adaylar), paylasilan_adet=0, uyari_mesaji="Aday ürünler bulundu ancak tamamı daha önce paylaşılmış veya aynı varyanttı.")
        print(f"Bitti. {n} urun paylasildi.")
    except Exception as e:
        print(f"Hata olustu: {e}")
        durum_kaydet("hata", hata_mesaji=str(e))
        raise


if __name__ == "__main__":
    main()
