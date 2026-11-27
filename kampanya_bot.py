from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys
import time
import requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "9203702774:AAHRD7T3aprg_yOozgQv1I_wBCb7yehq4Ck").strip()
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


def genel_kampanya_basligi_mi(baslik: str) -> bool:
    t = (baslik or "").strip().lower()
    if len(t) < 8:
        return True
    yasakli = [
        r"\bfırsatı\b",
        r"\bfırsatlar\b",
        r"\bkampanyası\b",
        r"\bkampanyalar\b",
        r"\bindirimler\b",
        r"\bgülümseten kasım\b",
        r"\bgünün fırsatı\b",
        r"\bçok satanlar\b",
        r"\bönceki fiyat\b",
        r"\bkalan süre\b",
    ]
    return any(re.search(pat, t, flags=re.IGNORECASE) for pat in yasakli)


def baslik_temizle(ham_baslik: str) -> str:
    s = re.sub(r"\s+", " ", (ham_baslik or "")).strip()
    s = re.split(
        r"(?:\s*[-–—|]?\s*%?\s*\d{1,2}\s*%\s*indirim|\s*%\s*\d{1,2}\s*indirim|\s*\d+\s*günün\s*en\s*düşük\s*fiyat[ıi]|\s*önceki\s*fiyat|\s*kalan\s*süre|\s*\d{1,3}(?:\.\d{3})*,\d{2}\s*(?:TL|₺))",
        s,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    s = re.sub(r"\s*[-–—:,;|]+\s*$", "", s).strip()
    if len(s) > 85:
        kisa = s[:85].rsplit(" ", 1)[0].strip()
        s = kisa if len(kisa) >= 20 else s[:85].strip()
    s = re.sub(r"\s*[-–—:,;|]+\s*$", "", s).strip()
    if genel_kampanya_basligi_mi(s):
        return ""
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
    norm = re.sub(r"(\d{1,3}(?:\.\d{3})*)\s+(\d{2})\s*(TL|₺)", r"\1,\2", str(metin), flags=re.IGNORECASE)
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


def asin_ve_link_ayarla(girdi: str) -> tuple[str, str]:
    s = (girdi or "").strip()
    m = re.search(r"/dp/([A-Z0-9]{10})", s, flags=re.IGNORECASE)
    if not m and re.fullmatch(r"[A-Z0-9]{10}", s, flags=re.IGNORECASE):
        m = re.match(r"([A-Z0-9]{10})", s, flags=re.IGNORECASE)
    if m:
        asin = m.group(1).upper()
        return asin, f"https://www.amazon.com.tr/dp/{asin}?tag={TAG}"
    if "tag=" in s:
        link = re.sub(r"tag=[^&]+", f"tag={TAG}", s)
    elif "?" in s:
        link = f"{s}&tag={TAG}"
    else:
        link = f"{s}?tag={TAG}"
    return "", link


def firsatlari_cek(url: str, limit: int = 20) -> list[dict]:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            locale="tr-TR",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3000)

        for _ in range(3):
            pg.mouse.wheel(0, 1200)
            pg.wait_for_timeout(1200)

        ham_liste = pg.evaluate(
            r"""(maxItems) => {
                const seenAsin = new Set();
                const out = [];

                const candidates = Array.from(
                    document.querySelectorAll('[data-asin], [data-testid*="deal"], .a-carousel-card, a[href*="/dp/"]')
                );

                for (const el of candidates) {
                    let asin = (el.getAttribute('data-asin') || '').trim();
                    let linkEl = null;

                    if (!/^[A-Z0-9]{10}$/i.test(asin)) {
                        linkEl = el.matches('a[href*="/dp/"]') ? el : el.querySelector('a[href*="/dp/"]');
                        if (!linkEl) continue;
                        const href = linkEl.getAttribute('href') || '';
                        const m = href.match(/\/dp\/([A-Z0-9]{10})/i);
                        if (!m) continue;
                        asin = m[1].toUpperCase();
                    } else {
                        linkEl = el.querySelector('a[href*="/dp/"]') || el;
                    }

                    if (!asin || seenAsin.has(asin)) continue;

                    let card = el;
                    for (let i = 0; i < 4 && card && card.parentElement; i++) {
                        const parent = card.parentElement;
                        const otherAsins = new Set();
                        for (const sub of parent.querySelectorAll('[data-asin], a[href*="/dp/"]')) {
                            const da = (sub.getAttribute('data-asin') || '').trim().toUpperCase();
                            if (/^[A-Z0-9]{10}$/.test(da) && da !== asin) otherAsins.add(da);
                            const sh = sub.getAttribute('href') || '';
                            const sm = sh.match(/\/dp\/([A-Z0-9]{10})/i);
                            if (sm && sm[1].toUpperCase() !== asin) otherAsins.add(sm[1].toUpperCase());
                        }
                        if (otherAsins.size > 0) break;
                        card = parent;
                    }

                    const text = card ? (card.innerText || '') : (el.innerText || '');
                    const lines = text.split('\n').map(x => x.trim()).filter(Boolean);

                    const imgEl = (card && card.querySelector('img')) || el.querySelector('img');
                    const img = imgEl
                        ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-src') || imgEl.getAttribute('data-old-hires') || '')
                        : '';
                    const imgAlt = imgEl ? (imgEl.getAttribute('alt') || '').trim() : '';

                    let rawTitle = (linkEl && linkEl.getAttribute('aria-label') ? linkEl.getAttribute('aria-label') : '').trim();
                    if (rawTitle.length < 10 && imgAlt.length >= 10) {
                        rawTitle = imgAlt;
                    }
                    if (rawTitle.length < 10) {
                        rawTitle = lines.find(
                            l => l.length >= 12 && !/TL|₺|%\s*\d|günün en düşük|önceki fiyat|kalan süre|sepete ekle|fırsatı\b/i.test(l)
                        ) || '';
                    }

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
        if len(title) < 8:
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
            ek.append("%" + str(ind) + " indirim")
        if eski:
            ek.append("(Eski: " + tr_para(eski) + ")")
        if ek:
            fiyat_satiri = fiyat_satiri + " — " + " ".join(ek)

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
                "link": link,
                "mesaj": mesaj,
            }
        )
    return sonuc


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


def mevcut_durumu_oku() -> dict:
    try:
        with open(DURUM_DOSYASI, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def durum_kaydet(durum: dict) -> None:
    with open(DURUM_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False, indent=2)


def durum_guncelle_kayit(
    durum: dict,
    yeni_kayitlar: list[dict],
    aday_sayisi: int,
    hatalar: list[dict],
    zaman: str,
    kaynak_notu: str = "",
) -> dict:
    onceki_liste = durum.get("son_paylasimlar") or []
    temiz_onceki = []
    if isinstance(onceki_liste, list):
        for item in onceki_liste:
            if not isinstance(item, dict):
                continue
            t = baslik_temizle(str(item.get("title") or ""))
            if not t:
                continue
            s = str(item.get("satis") or item.get("price") or "").strip()
            if not s or s.lower() == "undefined":
                continue
            ind = str(item.get("ind") or item.get("discount") or "%0").strip()
            if not ind or ind.lower() == "undefined":
                ind = "%0"
            asin = str(item.get("asin") or "").strip()
            link = str(item.get("link") or (f"https://www.amazon.com.tr/dp/{asin}?tag={TAG}" if asin else "")).strip()
            temiz_onceki.append(
                {
                    "title": t,
                    "satis": s,
                    "ind": ind,
                    "eski": str(item.get("eski") or ""),
                    "asin": asin,
                    "link": link,
                    "img": str(item.get("img") or ""),
                    "zaman": str(item.get("zaman") or zaman),
                }
            )

    guncel_liste = (yeni_kayitlar + temiz_onceki)[:30]
    toplam_yeni = int(durum.get("toplam_paylasim") or 0) + len(yeni_kayitlar)

    ek_not = f" ({kaynak_notu})" if kaynak_notu else ""
    bildirim_metni = f"{len(yeni_kayitlar)} yeni fırsat {KANAL} kanalına paylaşıldı ({aday_sayisi} aday incelendi){ek_not}."
    bildirim_tip = "uyari" if len(yeni_kayitlar) == 0 else "ok"

    eski_bildirimler = durum.get("bildirimler") or []
    temiz_bildirimler = []
    if isinstance(eski_bildirimler, list):
        for b in eski_bildirimler:
            if isinstance(b, dict):
                m = str(b.get("mesaj") or b.get("text") or "").strip()
                if m and m.lower() != "undefined":
                    temiz_bildirimler.append(
                        {
                            "zaman": str(b.get("zaman") or b.get("time") or zaman),
                            "mesaj": m,
                            "tip": str(b.get("tip") or "ok"),
                        }
                    )

    temiz_bildirimler.insert(0, {"zaman": zaman, "mesaj": bildirim_metni, "tip": bildirim_tip})
    bildirimler = temiz_bildirimler[:30]

    son_urun = yeni_kayitlar[0] if yeni_kayitlar else durum.get("son_urun")
    if isinstance(son_urun, dict):
        temiz_son = baslik_temizle(str(son_urun.get("title") or ""))
        if temiz_son:
            son_urun["title"] = temiz_son
        if not son_urun.get("satis") or str(son_urun.get("satis")).lower() == "undefined":
            son_urun["satis"] = guncel_liste[0]["satis"] if guncel_liste else "0,00 TL"
        if not son_urun.get("ind") or str(son_urun.get("ind")).lower() == "undefined":
            son_urun["ind"] = guncel_liste[0]["ind"] if guncel_liste else "%0"
        if not son_urun.get("zaman"):
            son_urun["zaman"] = zaman
        asin_val = str(son_urun.get("asin") or "").strip()
        if not son_urun.get("link") and asin_val:
            son_urun["link"] = f"https://www.amazon.com.tr/dp/{asin_val}?tag={TAG}"

    return {
        "son_tarama": zaman,
        "toplam_paylasim": toplam_yeni,
        "paylasilan_adet": len(yeni_kayitlar),
        "bulunan_link": aday_sayisi,
        "durum": "hata" if hatalar else ("uyari" if len(yeni_kayitlar) == 0 else "ok"),
        "hatalar": hatalar,
        "bildirimler": bildirimler,
        "son_urun": son_urun,
        "son_paylasimlar": guncel_liste,
    }


def manuel_paylas_cli(args: list[str]) -> None:
    if len(args) < 3:
        print('Kullanım: python kampanya_bot.py --manuel "Başlık" "Fiyat" "Link veya ASIN" ["İndirim %"] ["Eski Fiyat"] ["Görsel URL"]')
        sys.exit(1)

    title = baslik_temizle(args[0]) or args[0].strip()
    satis = fiyat_sayiya_cevir(args[1])
    if not satis:
        print("Geçersiz fiyat.")
        sys.exit(1)

    asin, link = asin_ve_link_ayarla(args[2])
    ind = int(re.sub(r"\D", "", args[3])) if len(args) > 3 and re.sub(r"\D", "", args[3]) else None
    eski = fiyat_sayiya_cevir(args[4]) if len(args) > 4 and args[4].strip() else None
    img = args[5].strip() if len(args) > 5 else ""

    if ind is None and eski and eski > satis:
        ind = round(((eski - satis) / eski) * 100)

    fiyat_satiri = tr_para(satis)
    ek = []
    if ind:
        ek.append("%" + str(ind) + " indirim")
    if eski:
        ek.append("(Eski: " + tr_para(eski) + ")")
    if ek:
        fiyat_satiri = fiyat_satiri + " — " + " ".join(ek)

    mesaj = "\n\n".join(
        [
            title,
            fiyat_satiri,
            link,
            "#tanitim #reklam #işbirliği",
        ]
    )

    zaman = simdi_tr_str()
    telegrama_gonder(img, mesaj)
    paylasim_kaydet(title, satis)

    kayit = {
        "title": title,
        "satis": tr_para(satis),
        "ind": "%" + str(ind) if ind else "%0",
        "eski": tr_para(eski) if eski else "",
        "asin": asin,
        "link": link,
        "img": img,
        "zaman": zaman,
    }
    durum = mevcut_durumu_oku()
    yeni_durum = durum_guncelle_kayit(durum, [kayit], 1, [], zaman, kaynak_notu="manuel")
    durum_kaydet(yeni_durum)
    print(f"Manuel paylaşıldı: {title} | {tr_para(satis)} | {link}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--manuel":
        manuel_paylas_cli(sys.argv[2:])
        return

    durum = mevcut_durumu_oku()
    zaman = simdi_tr_str()
    hatalar = []
    yeni_paylasilanlar = []

    try:
        adaylar = firsatlari_cek(TARAMA_URL, limit=20)
    except Exception as exc:
        adaylar = []
        hatalar.append({"zaman": zaman, "mesaj": f"Tarama hatası: {exc}"})

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
            hatalar.append({"zaman": zaman, "mesaj": f"Gönderim hatası ({u.get('asin')}): {exc}"})

    eklenen_kayitlar = [
        {
            "title": x["title"],
            "satis": tr_para(x["satis"]),
            "ind": "%" + str(x["ind"]) if x.get("ind") else "%0",
            "eski": tr_para(x["eski"]) if x.get("eski") else "",
            "asin": x["asin"],
            "link": x["link"],
            "img": x["img"],
            "zaman": zaman,
        }
        for x in reversed(yeni_paylasilanlar)
    ]

    yeni_durum = durum_guncelle_kayit(durum, eklenen_kayitlar, len(adaylar), hatalar, zaman)
    durum_kaydet(yeni_durum)
    print(f"durum.json güncellendi | Son tarama: {zaman} | Yeni: {len(yeni_paylasilanlar)}")


if __name__ == "__main__":
    main()
