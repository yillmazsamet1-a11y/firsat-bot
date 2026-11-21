import os
import re
import sqlite3
import time
from datetime import datetime
from urllib.parse import urljoin
import requests
from playwright.sync_api import sync_playwright

URL = "https://www.amazon.com.tr/gp/goldbox"
BITIS = datetime(2026, 11, 25, 23, 59, 59)
MIN_IND = 25.0
MAX_ADET = 6
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amazon_hafiza.db")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
KANAL = os.environ.get("TELEGRAM_CHANNEL_ID", "@anlikiindirim").strip()
TAG = os.environ.get("AMAZON_ASSOCIATE_TAG", "anlikiindirim-21").strip()


def tr_para(tutar: float) -> str:
    return f"{tutar:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")


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
    c.commit()
    return c


def atildi_mi(c, asin):
    esik = int(time.time()) - 86400
    return c.execute(
        "SELECT 1 FROM paylasilan_urunler WHERE asin = ? AND paylasim_zamani >= ?",
        (asin, esik),
    ).fetchone() is not None


def kaydet(c, asin):
    c.execute(
        "INSERT OR REPLACE INTO paylasilan_urunler (asin, paylasim_zamani) VALUES (?, ?)",
        (asin, int(time.time())),
    )
    c.commit()


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
        title = re.sub(r"\s+", " ", r.get("title") or "").strip()
        if not title or len(title) < 8:
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
        out.append({"asin": asin, "title": title, "satis": satis, "eski": eski, "ind": ind, "img": img_url})
    return out


def main():
    if datetime.now() > BITIS:
        print("Sure doldu.")
        return
    adaylar = tara()
    if not adaylar:
        print("Uygun yuksek indirimli urun bulunamadi.")
        return
    c = db_ac()
    n = 0
    try:
        for u in adaylar:
            if n >= MAX_ADET:
                break
            if atildi_mi(c, u["asin"]):
                continue
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
            kaydet(c, u["asin"])
            n += 1
            print(f"[YAYINLANDI {n}] %{u['ind']:.0f} -> {u['title'][:45]} ({f_str})")
            time.sleep(1.5)
    finally:
        c.close()
    print(f"Bitti. {n} urun paylasildi.")


if __name__ == "__main__":
    main()
