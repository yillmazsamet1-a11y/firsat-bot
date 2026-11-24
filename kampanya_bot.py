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
    if len(s) > 120:
        s = s[:117].rsplit(" ", 1)[0].strip()
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
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3500)

        ham_liste = pg.evaluate(
            r"""(maxItems) => {
                const links = Array.from(document.querySelectorAll('a[href*="/dp/"]'));
                const seenAsin = new Set();
                const out = [];

                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(/\/dp\/([A-Z0-9]{10})/);
                    if (!m) continue;
                    const asin = m[1];
                    if (seenAsin.has(asin)) continue;

                    const card = a.closest('[data-testid*="deal"], [data-testid*="card"], .a-section, .s-result-item, li, div') || a.parentElement;
                    const text = card ? (card.innerText || '') : (a.innerText || '');
                    const lines = text.split('\n').map(x => x.trim()).filter(Boolean);

                    let rawTitle = (a.getAttribute('aria-label') || '').trim();
                    if (rawTitle.length < 8) {
                        rawTitle = lines.find(l => l.length >= 12 && !/TL|₺|%\s*\d|günün en düşük|önceki fiyat|kalan süre/i.test(l)) || (a.innerText || '').trim();
                    }

                    const imgEl = card ? card.querySelector('img') : null;
                    const img = imgEl ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-src') || '') : '';

                    const priceMatches = text.match(/(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d{1,3}(?:\.\d{3})*)\s*(?:TL|₺)/g) || [];
                    const discMatch = text.match(/%\s*(\d{1,2})\b/);

                    if (rawTitle && priceMatches.length > 0) {
                        seenAsin.add(asin);
                        out.push({
                            asin,
                            rawTitle,
                            img,
                            prices: priceMatches.slice(0, 4),
                            discount: discMatch ? parseInt(discMatch[1], 10) : null
                        });
                    }
                    if (out.length >= maxItems) break;
                }
                return out;
            }""",
            limit,
        )
        b.close()

    sonuc = []
    for item in ham_liste or []:
        title = baslik_temizle(item.get("rawTitle") or "")
        if len(title) < 5:
            continue

        fiyat_sayilari = []
        for pm in item.get("prices") or []:
            v = fiyat_sayiya_cevir(pm)
            if v is not None and v not in fiyat_sayilari:
                fiyat_sayilari.append(v)
        if not fiyat_sayilari:
            continue

        satis = fiyat_sayilari[0]
        buyukler = [x for x in fiyat_sayilari[1:] if x > satis]
        eski = min(buyukler) if buyukler else None
        ind = item.get("discount")
        if ind is None and eski and eski > satis:
            ind = round(((eski - satis) / eski) * 100)

        link = f"https://www.amazon.com.tr/dp/{item['asin']}?tag={TAG}"
        fiyat_satiri = tr_para(satis)
        ek = []
        if ind:
            ek.append(f"%{ind} indirim")
        if eski:
            ek.append(f"(Eski: {tr_para(eski)})")
        if ek:
            fiyat_satiri = f"{fiyat_satiri} — {' '.join(ek)}"

        mesaj = "\n\n".join(
            [
                title,
                fiyat_satiri,
                link,
                "#tanitim #reklam #işbirliği",
            ]
        )

        sonuc.append(
            {
                "asin": item["asin"],
                "title": title,
                "img": item.get("img") or "",
                "satis": satis,
                "eski": eski,
                "ind": ind,
                "url": link,
                "mesaj": mesaj,
            }
        )
    return sonuc


def telegrama_gonder(foto_url: str, caption: str) -> bool:
    if not TOKEN:
        print("Uyarı: TELEGRAM_BOT_TOKEN tanımlı değil, Telegram gönderimi atlandı.")
        return False
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


def mevcut_durumu_oku() -> dict:
    try:
        with open(DURUM_DOSYASI, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {
        "son_tarama": "",
        "toplam_paylasim": 0,
        "son_tur_paylasim": 0,
        "son_tur_aday": 0,
        "ort_indirim": "%0",
        "hatalar": [],
        "bildirimler": [],
        "son_urun": None,
        "son_paylasimlar": [],
    }


def durum_kaydet(durum: dict) -> None:
    with open(DURUM_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False, indent=2)


def main() -> None:
    durum = mevcut_durumu_oku()
    zaman = simdi_tr_str()
    hatalar = []
    yeni_paylasilanlar = []

    try:
        adaylar = firsatlari_cek(TARAMA_URL, limit=20)
    except Exception as exc:
        adaylar = []
        hatalar.append(f"Tarama hatası: {exc}")

    for u in adaylar:
        baslik = u["title"]
        fiyat = u["satis"]

        if daha_once_paylasildi_mi(baslik, fiyat):
            print(f"Atlandı (zaten paylaşıldı): {baslik} - {fiyat}")
            continue

        try:
            if telegrama_gonder(u["img"], u["mesaj"]):
                paylasim_kaydet(baslik, fiyat)
                yeni_paylasilanlar.append(u)
                print(f"Paylaşıldı: {baslik} - {fiyat}")
                time.sleep(1.5)
        except Exception as exc:
            hatalar.append(f"Gönderim hatası ({u.get('asin')}): {exc}")

    toplam_onceki = int(durum.get("toplam_paylasim") or 0)
    toplam_yeni = toplam_onceki + len(yeni_paylasilanlar)

    onceki_liste = durum.get("son_paylasimlar")
    if not isinstance(onceki_liste, list):
        onceki_liste = []

    temiz_onceki = []
    for item in onceki_liste:
        if not isinstance(item, dict):
            continue
        temiz_ad = baslik_temizle(str(item.get("title") or ""))
        if not temiz_ad:
            continue
        fiyat_str = str(item.get("price") or "").strip()
        if not fiyat_str or fiyat_str.lower() == "undefined":
            fiyat_str = "0,00 TL"
        ind_str = str(item.get("discount") or "").strip()
        if not ind_str or ind_str.lower() == "undefined":
            ind_str = "%0"
        temiz_onceki.append(
            {
                **item,
                "title": temiz_ad,
                "price": fiyat_str,
                "discount": ind_str,
                "time": item.get("time") or zaman,
            }
        )

    eklenen_kayitlar = [
        {
            "title": x["title"],
            "price": tr_para(x["satis"]),
            "discount": f"%{x['ind']}" if x.get("ind") else "%0",
            "asin": x["asin"],
            "url": x["url"],
            "img": x["img"],
            "time": zaman,
        }
        for x in reversed(yeni_paylasilanlar)
    ]
    guncel_liste = (eklenen_kayitlar + temiz_onceki)[:30]

    indirim_listesi = [
        int(re.sub(r"\D", "", str(x.get("discount") or "")))
        for x in guncel_liste
        if re.sub(r"\D", "", str(x.get("discount") or ""))
    ]
    ort_ind = (
        f"%{round(sum(indirim_listesi) / len(indirim_listesi))}"
        if indirim_listesi
        else (durum.get("ort_indirim") or "%0")
    )

    bildirim_metni = f"{len(yeni_paylasilanlar)} yeni fırsat {KANAL} kanalına paylaşıldı ({len(adaylar)} aday incelendi)."
    eski_bildirimler = durum.get("bildirimler")
    temiz_bildirimler = []
    if isinstance(eski_bildirimler, list):
        for b in eski_bildirimler:
            if isinstance(b, dict):
                txt = str(b.get("text") or b.get("message") or "").strip()
                if txt and txt.lower() != "undefined":
                    temiz_bildirimler.append(
                        {
                            "time": b.get("time") or zaman,
                            "text": txt,
                            "message": txt,
                        }
                    )
            elif isinstance(b, str) and b.strip() and b.strip().lower() != "undefined":
                temiz_bildirimler.append(
                    {
                        "time": zaman,
                        "text": b.strip(),
                        "message": b.strip(),
                    }
                )

    temiz_bildirimler.insert(
        0,
        {
            "time": zaman,
            "text": bildirim_metni,
            "message": bildirim_metni,
        },
    )
    bildirimler = temiz_bildirimler[:30]

    durum["son_tarama"] = zaman
    durum["toplam_paylasim"] = toplam_yeni
    durum["son_tur_paylasim"] = len(yeni_paylasilanlar)
    durum["son_tur_aday"] = len(adaylar)
    durum["ort_indirim"] = ort_ind
    durum["hatalar"] = hatalar
    durum["bildirimler"] = bildirimler
    if eklenen_kayitlar:
        durum["son_urun"] = eklenen_kayitlar[0]
    durum["son_paylasimlar"] = guncel_liste

    durum_kaydet(durum)
    print(f"durum.json güncellendi | Son tarama: {zaman} | Yeni: {len(yeni_paylasilanlar)}")


if __name__ == "__main__":
    main()
