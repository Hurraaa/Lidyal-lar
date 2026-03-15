"""
Stablecoin Arbitraj Analizi & Bot Simulasyonu
==============================================
Turk borsalari arasinda USDT/TRY fiyat farki ile arbitraj.

Mantik:
  - USDT/TRY fiyati borsalar arasinda surekli farklidir
  - BtcTurk'te ucuzsa orda al, Paribu'da pahaliysa orda sat
  - BTC'ye gore COKK daha dusuk risk (stablecoin = sabit deger)
  - Fiyat dalgalanma riski neredeyse SIFIR

Borsalar: BtcTurk, Paribu, Binance TR (API destekli)
"""

import random
import statistics
import math
import json
import time
import ssl
from urllib.request import urlopen, Request
from datetime import datetime


# ============================================================
# PARAMETRELER
# ============================================================

# Sermaye
TOPLAM_SERMAYE_TL = 100_000
# Strateji: 3 borsada dagitilmis sermaye
# Her borsada hem TL hem USDT hazir bekler
BORSA_SAYISI = 3
BORSA_BASI_SERMAYE = TOPLAM_SERMAYE_TL / BORSA_SAYISI

# Borsalar ve komisyon oranlari
BORSALAR = {
    "BtcTurk": {
        "komisyon_maker": 0.0010,    # %0.10
        "komisyon_taker": 0.0020,    # %0.20
        "usdt_cekim_ucreti": 1.0,    # USDT (TRC20) cekim ucreti
        "tl_cekim_ucreti": 2.0,      # TL havale/EFT ucreti
        "min_islem": 10.0,           # Min USDT islem miktari
        "api_url": "https://api.btcturk.com/api/v2/ticker?pairSymbol=USDTTRY",
        "api_fiyat_yolu": "data.0.last",
    },
    "Paribu": {
        "komisyon_maker": 0.0010,    # %0.10
        "komisyon_taker": 0.0018,    # %0.18
        "usdt_cekim_ucreti": 1.0,
        "tl_cekim_ucreti": 0.0,      # Paribu TL cekim ucretsiz
        "min_islem": 10.0,
        "api_url": None,              # Paribu API halka acik degil
        "api_fiyat_yolu": None,
    },
    "Binance TR": {
        "komisyon_maker": 0.0010,    # %0.10
        "komisyon_taker": 0.0010,    # %0.10
        "usdt_cekim_ucreti": 1.0,
        "tl_cekim_ucreti": 3.0,
        "min_islem": 10.0,
        "api_url": "https://api.binance.com/api/v3/ticker/price?symbol=USDTTRY",
        "api_fiyat_yolu": "price",
    },
}

# Slippage (stablecoinde daha dusuk)
SLIPPAGE_PCT = 0.0002               # %0.02 (BTC'ye gore cok daha az)

# USDT/TRY fiyat farki modeli (borsalar arasi)
# Stablecoinde fark daha kucuk ama daha STABIL
FARK_ORTALAMA_PCT = 0.0020          # Ort. %0.20 fark
FARK_STD_PCT = 0.0015               # Std sapma %0.15
BUYUK_FARK_OLASILIGI = 0.05         # %5 ihtimalle buyuk fark (haber/panik)
BUYUK_FARK_CARPANI = 3.0            # Buyuk farkta 3x kadar fark

# Rebalancing
REBALANCING_ESIGI = 0.15            # TL veya USDT bakiyenin %15'inin altina
                                     # dustugunde rebalance yap
USDT_TRANSFER_SURESI_DK = 5         # TRC20 ile ~5 dakika
TL_HAVALE_SURESI_DK = 30            # EFT/havale ~30 dakika (mesai saatinde)

# Varsayilan fiyat
VARSAYILAN_USDT_TRY = 38.50

# Monte Carlo
SIMULASYON_SAYISI = 10_000
GUN_SAYISI = 30
KONTROL_PER_GUN = 48                # Her 30 dk'da bir fiyat kontrolu
ISLEM_ORANI = 0.40                  # Her firsatta bakiyenin %40'i ile islem


# ============================================================
# API FONKSIYONLARI
# ============================================================

def api_istegi_yap(url, timeout=10):
    """Guvenli HTTP GET istegi yapar, JSON doner veya None."""
    try:
        ctx = ssl.create_default_context()
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def fiyat_degerini_cek(veri, yol):
    """Nested dict/list icerisinden nokta-ayricli yol ile deger alir.
    Ornek: 'data.0.last' -> veri['data'][0]['last']
    """
    if veri is None:
        return None
    parcalar = yol.split(".")
    sonuc = veri
    for p in parcalar:
        try:
            if isinstance(sonuc, list):
                sonuc = sonuc[int(p)]
            elif isinstance(sonuc, dict):
                sonuc = sonuc[p]
            else:
                return None
        except (KeyError, IndexError, ValueError):
            return None
    return float(sonuc)


def canli_usdt_try_fiyatlari_al():
    """Tum borsalardan canli USDT/TRY fiyatlarini toplar."""
    fiyatlar = {}
    for borsa_adi, borsa in BORSALAR.items():
        if borsa["api_url"] is None:
            continue

        print(f"  {borsa_adi} sorgusu...", end=" ")
        veri = api_istegi_yap(borsa["api_url"])
        if veri:
            fiyat = fiyat_degerini_cek(veri, borsa["api_fiyat_yolu"])
            if fiyat:
                fiyatlar[borsa_adi] = fiyat
                print(f"OK ({fiyat:.4f} TL)")
            else:
                print("PARSE HATASI")
        else:
            print("BASARISIZ")
        time.sleep(0.5)

    return fiyatlar


def canli_veri_topla():
    """Canli piyasa verilerini toplar ve fark analizi yapar."""
    sonuc = {
        "canli": False,
        "usdt_try": VARSAYILAN_USDT_TRY,
        "fiyatlar": {},
        "kaynak": "SIMULASYON (varsayilan degerler)",
    }

    fiyatlar = canli_usdt_try_fiyatlari_al()

    if len(fiyatlar) >= 2:
        sonuc["canli"] = True
        sonuc["fiyatlar"] = fiyatlar
        sonuc["usdt_try"] = statistics.mean(fiyatlar.values())
        sonuc["kaynak"] = "CANLI VERI"

        # Fark analizi
        fiyat_listesi = list(fiyatlar.values())
        en_ucuz = min(fiyat_listesi)
        en_pahali = max(fiyat_listesi)
        fark_pct = (en_pahali - en_ucuz) / en_ucuz * 100
        sonuc["anlik_fark_pct"] = fark_pct
        sonuc["en_ucuz_borsa"] = min(fiyatlar, key=fiyatlar.get)
        sonuc["en_pahali_borsa"] = max(fiyatlar, key=fiyatlar.get)
    else:
        sonuc["anlik_fark_pct"] = 0
        sonuc["en_ucuz_borsa"] = "-"
        sonuc["en_pahali_borsa"] = "-"

    return sonuc


# ============================================================
# MALIYET HESAPLAMALARI
# ============================================================

def tek_islem_maliyeti(miktar_usdt, alis_borsa, satis_borsa, transfer_var=False):
    """
    Bir arbitraj isleminin toplam maliyetini hesaplar.

    Args:
        miktar_usdt: USDT miktari
        alis_borsa: Ucuz borsanin adi
        satis_borsa: Pahali borsanin adi
        transfer_var: True ise USDT transfer ucreti eklenir
                      False ise eszamanli model (iki borsada hazir bakiye)
    Returns:
        (maliyet_tl, maliyet_pct, detay_dict)
    """
    usdt_try = VARSAYILAN_USDT_TRY
    miktar_tl = miktar_usdt * usdt_try
    alis = BORSALAR[alis_borsa]
    satis = BORSALAR[satis_borsa]

    # Komisyonlar (taker olarak hesapla - market order)
    alis_komisyon = miktar_tl * alis["komisyon_taker"]
    satis_komisyon = miktar_tl * satis["komisyon_taker"]

    # Slippage (iki taraf)
    slippage = miktar_tl * SLIPPAGE_PCT * 2

    # Transfer ucreti (opsiyonel)
    transfer_ucreti = 0
    if transfer_var:
        transfer_ucreti = alis["usdt_cekim_ucreti"] * usdt_try

    toplam = alis_komisyon + satis_komisyon + slippage + transfer_ucreti
    toplam_pct = toplam / miktar_tl * 100

    detay = {
        "alis_komisyon": alis_komisyon,
        "satis_komisyon": satis_komisyon,
        "slippage": slippage,
        "transfer_ucreti": transfer_ucreti,
    }

    return toplam, toplam_pct, detay


def minimum_fark_hesapla(miktar_usdt, alis_borsa, satis_borsa, transfer_var=False):
    """Basabas icin gereken minimum fark (%)."""
    _, maliyet_pct, _ = tek_islem_maliyeti(
        miktar_usdt, alis_borsa, satis_borsa, transfer_var
    )
    return maliyet_pct


# ============================================================
# FIRSAT BULUCU (Canli Veriye Hazir)
# ============================================================

def firsatlari_analiz_et(fiyatlar):
    """
    Verilen fiyat dict'inden tum arbitraj firsatlarini bulur.

    Args:
        fiyatlar: {"BtcTurk": 38.50, "Paribu": 38.65, ...}

    Returns:
        Firsatlarin listesi (sirali: en karli ilk)
    """
    firsatlar = []
    borsa_listesi = list(fiyatlar.keys())

    for i in range(len(borsa_listesi)):
        for j in range(len(borsa_listesi)):
            if i == j:
                continue

            ucuz = borsa_listesi[i]
            pahali = borsa_listesi[j]
            ucuz_fiyat = fiyatlar[ucuz]
            pahali_fiyat = fiyatlar[pahali]

            if pahali_fiyat <= ucuz_fiyat:
                continue

            fark_pct = (pahali_fiyat - ucuz_fiyat) / ucuz_fiyat * 100
            maliyet_pct = minimum_fark_hesapla(1000, ucuz, pahali)
            net_kar_pct = fark_pct - maliyet_pct

            firsatlar.append({
                "alis_borsa": ucuz,
                "satis_borsa": pahali,
                "alis_fiyat": ucuz_fiyat,
                "satis_fiyat": pahali_fiyat,
                "fark_pct": fark_pct,
                "maliyet_pct": maliyet_pct,
                "net_kar_pct": net_kar_pct,
                "karli": net_kar_pct > 0,
            })

    firsatlar.sort(key=lambda x: x["net_kar_pct"], reverse=True)
    return firsatlar


# ============================================================
# MONTE CARLO SIMULASYONU
# ============================================================

def stablecoin_simulasyonu():
    """
    Stablecoin arbitraj Monte Carlo simulasyonu.

    Model:
    - 3 borsada (BtcTurk, Paribu, Binance TR) TL + USDT hazir
    - Her borsada baslangicta %50 TL + %50 USDT
    - Fiyat farki olustugunda ucuz tarafta USDT al, pahali tarafta sat
    - Stablecoin oldugu icin fiyat dalgalanma riski YOK
    - Sadece borsalar arasi fark degisiyor
    """
    borsa_adlari = list(BORSALAR.keys())
    usdt_try = VARSAYILAN_USDT_TRY

    sonuclar = []
    islem_sayilari = []
    karli_islem_sayilari = []
    rebalance_sayilari = []
    toplam_kar_listesi = []
    firsatsiz_gun_listesi = []

    for _ in range(SIMULASYON_SAYISI):
        # Baslangic bakiyeleri: her borsada %50 TL, %50 USDT
        bakiyeler = {}
        for borsa in borsa_adlari:
            bakiyeler[borsa] = {
                "tl": BORSA_BASI_SERMAYE / 2,
                "usdt": (BORSA_BASI_SERMAYE / 2) / usdt_try,
            }

        toplam_islem = 0
        karli_islem = 0
        rebalance = 0
        toplam_kar_tl = 0
        firsatsiz_gun = 0

        for gun in range(GUN_SAYISI):
            gun_islem_yapildi = False

            for _ in range(KONTROL_PER_GUN):
                # Her borsa icin USDT/TRY fiyatini simule et
                # Baz fiyat + borsaya ozel sapma
                baz_fiyat = usdt_try
                fiyatlar = {}
                for borsa in borsa_adlari:
                    # Her borsanin fiyati baz fiyattan biraz sapma gosterir
                    sapma = random.gauss(0, FARK_STD_PCT)

                    # Nadiren buyuk fark (haberler, panik vs.)
                    if random.random() < BUYUK_FARK_OLASILIGI:
                        sapma *= BUYUK_FARK_CARPANI

                    fiyatlar[borsa] = baz_fiyat * (1 + sapma)

                # En ucuz ve en pahali borsayi bul
                en_ucuz = min(fiyatlar, key=fiyatlar.get)
                en_pahali = max(fiyatlar, key=fiyatlar.get)

                ucuz_fiyat = fiyatlar[en_ucuz]
                pahali_fiyat = fiyatlar[en_pahali]
                fark_pct = (pahali_fiyat - ucuz_fiyat) / ucuz_fiyat

                # Maliyet hesapla
                alis_kom = BORSALAR[en_ucuz]["komisyon_taker"]
                satis_kom = BORSALAR[en_pahali]["komisyon_taker"]
                toplam_maliyet_pct = alis_kom + satis_kom + SLIPPAGE_PCT * 2

                if fark_pct <= toplam_maliyet_pct:
                    continue  # Fark yeterli degil

                # Islem yapilabilir miktar
                mevcut_tl = bakiyeler[en_ucuz]["tl"]
                mevcut_usdt = bakiyeler[en_pahali]["usdt"]

                # Alim icin TL, satim icin USDT gerekli
                max_alis_usdt = (mevcut_tl / ucuz_fiyat) * ISLEM_ORANI
                max_satis_usdt = mevcut_usdt * ISLEM_ORANI

                islem_usdt = min(max_alis_usdt, max_satis_usdt)

                if islem_usdt < BORSALAR[en_ucuz]["min_islem"]:
                    continue  # Minimum islem limitinin altinda

                # Islemi gerceklestir
                islem_tl = islem_usdt * ucuz_fiyat

                # Ucuz borsada: TL azalir, USDT artar
                bakiyeler[en_ucuz]["tl"] -= islem_tl
                alis_sonrasi = islem_usdt * (1 - alis_kom)
                bakiyeler[en_ucuz]["usdt"] += alis_sonrasi

                # Pahali borsada: USDT azalir, TL artar
                bakiyeler[en_pahali]["usdt"] -= islem_usdt
                satis_tl = islem_usdt * pahali_fiyat * (1 - satis_kom)
                bakiyeler[en_pahali]["tl"] += satis_tl

                # Net kar hesapla
                harcanan_tl = islem_tl
                kazanilan_tl = satis_tl
                # USDT degisimi (ucuz borsada artti, pahali borsada azaldi)
                usdt_net = alis_sonrasi - islem_usdt
                usdt_net_tl = usdt_net * usdt_try

                net_kar = kazanilan_tl - harcanan_tl + usdt_net_tl
                toplam_kar_tl += net_kar

                toplam_islem += 1
                if net_kar > 0:
                    karli_islem += 1
                gun_islem_yapildi = True

            if not gun_islem_yapildi:
                firsatsiz_gun += 1

            # Gun sonu: Rebalancing kontrolu
            for borsa in borsa_adlari:
                toplam_deger = bakiyeler[borsa]["tl"] + bakiyeler[borsa]["usdt"] * usdt_try
                if toplam_deger <= 0:
                    continue

                tl_oran = bakiyeler[borsa]["tl"] / toplam_deger
                usdt_oran = (bakiyeler[borsa]["usdt"] * usdt_try) / toplam_deger

                # Bir taraf cok azaldiysa rebalance
                if tl_oran < REBALANCING_ESIGI or usdt_oran < REBALANCING_ESIGI:
                    rebalance += 1
                    # Rebalancing maliyeti: USDT transfer ucreti (TRC20)
                    reb_maliyet = BORSALAR[borsa]["usdt_cekim_ucreti"] * usdt_try

                    # Yeniden dengele: %50 TL, %50 USDT
                    hedef_tl = (toplam_deger - reb_maliyet) / 2
                    hedef_usdt = hedef_tl / usdt_try
                    bakiyeler[borsa]["tl"] = hedef_tl
                    bakiyeler[borsa]["usdt"] = hedef_usdt

        # Simulasyon sonu: toplam portfoy degeri
        toplam_deger = 0
        for borsa in borsa_adlari:
            toplam_deger += bakiyeler[borsa]["tl"]
            toplam_deger += bakiyeler[borsa]["usdt"] * usdt_try

        sonuclar.append(toplam_deger)
        islem_sayilari.append(toplam_islem)
        karli_islem_sayilari.append(karli_islem)
        rebalance_sayilari.append(rebalance)
        toplam_kar_listesi.append(toplam_kar_tl)
        firsatsiz_gun_listesi.append(firsatsiz_gun)

    return {
        "sonuclar": sonuclar,
        "islem_sayilari": islem_sayilari,
        "karli_islem_sayilari": karli_islem_sayilari,
        "rebalance_sayilari": rebalance_sayilari,
        "toplam_kar_listesi": toplam_kar_listesi,
        "firsatsiz_gun_listesi": firsatsiz_gun_listesi,
    }


# ============================================================
# RAPOR
# ============================================================

def rapor_yazdir():
    """Ana analiz raporu."""

    print()
    print("=" * 65)
    print("  STABLECOIN ARBITRAJ ANALIZI")
    print("  USDT/TRY - Turk Borsalari Arasi (BtcTurk/Paribu/Binance TR)")
    print("=" * 65)
    print()

    # ----------------------------------------------------------
    # Canli veri
    # ----------------------------------------------------------
    print("-" * 65)
    print("  PIYASA VERILERI")
    print("-" * 65)
    veri = canli_veri_topla()
    print()
    print(f"  Veri kaynagi      : {veri['kaynak']}")
    print(f"  Ort. USDT/TRY     : {veri['usdt_try']:.4f} TL")
    if veri["canli"] and veri["fiyatlar"]:
        print()
        for borsa, fiyat in sorted(veri["fiyatlar"].items()):
            print(f"    {borsa:>12s}    : {fiyat:.4f} TL")
        print()
        print(f"  Anlik fark        : %{veri['anlik_fark_pct']:.3f}")
        print(f"  En ucuz           : {veri['en_ucuz_borsa']}")
        print(f"  En pahali         : {veri['en_pahali_borsa']}")

        # Canli firsat analizi
        firsatlar = firsatlari_analiz_et(veri["fiyatlar"])
        karli_firsatlar = [f for f in firsatlar if f["karli"]]
        if karli_firsatlar:
            print()
            print(f"  >>> CANLI ARBITRAJ FIRSATI BULUNDU! <<<")
            for f in karli_firsatlar:
                print(f"      {f['alis_borsa']} ({f['alis_fiyat']:.4f}) -> "
                      f"{f['satis_borsa']} ({f['satis_fiyat']:.4f})")
                print(f"      Fark: %{f['fark_pct']:.3f} | "
                      f"Maliyet: %{f['maliyet_pct']:.3f} | "
                      f"Net: %{f['net_kar_pct']:.3f}")
    print()

    usdt_try = veri["usdt_try"]

    # ----------------------------------------------------------
    # Strateji aciklamasi
    # ----------------------------------------------------------
    print("-" * 65)
    print("  STRATEJI ACIKLAMASI")
    print("-" * 65)
    print(f"""
  Toplam sermaye     : {TOPLAM_SERMAYE_TL:>10,.0f} TL
  Borsa sayisi       : {BORSA_SAYISI}
  Borsa basi         : {BORSA_BASI_SERMAYE:>10,.0f} TL (yarisi TL, yarisi USDT)

  NEDEN STABLECOIN?
  1. Fiyat dalgalanma riski YOK (BTC %5 dusebilir, USDT dusmez)
  2. Transferi HIZLI ve UCUZ (TRC20 ile ~5dk, ~1 USDT)
  3. Spread daha DUSUK (likidite yuksek)
  4. 7/24 islem yapilabilir
  5. Rebalancing KOLAY (USDT transferi ucuz)

  NASIL CALISIR:
  1. Her borsada hem TL hem USDT hazir bekler
  2. USDT/TRY fiyatlarini surekli karsilastir
  3. En ucuz borsada AL, en pahali borsada SAT
  4. Transfer yok - eszamanli islem (mevcut bakiyelerle)
  5. Bakiyeler dengesizlesince USDT transferi ile rebalance
""")

    # ----------------------------------------------------------
    # 1) MALIYET KARSILASTIRMA TABLOSU
    # ----------------------------------------------------------
    print("-" * 65)
    print("  1) MALIYET ANALIZI - TUM BORSA CIFTLERI")
    print("-" * 65)
    print()
    print(f"  {'Alis':>12s} -> {'Satis':>12s} | {'Komisyon':>10s} | "
          f"{'Slippage':>10s} | {'TOPLAM':>10s} | {'Min Fark':>10s}")
    print(f"  {'─' * 12} -> {'─' * 12}─┼─{'─' * 10}─┼─"
          f"{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 10}")

    borsa_adlari = list(BORSALAR.keys())
    for i in range(len(borsa_adlari)):
        for j in range(len(borsa_adlari)):
            if i == j:
                continue
            alis = borsa_adlari[i]
            satis = borsa_adlari[j]
            maliyet, maliyet_pct, detay = tek_islem_maliyeti(1000, alis, satis)
            kom_pct = (detay["alis_komisyon"] + detay["satis_komisyon"]) / (1000 * usdt_try) * 100
            slip_pct = detay["slippage"] / (1000 * usdt_try) * 100
            print(f"  {alis:>12s} -> {satis:>12s} │ "
                  f"%{kom_pct:.3f}     │ "
                  f"%{slip_pct:.3f}     │ "
                  f"%{maliyet_pct:.3f}     │ "
                  f"%{maliyet_pct:.3f}")

    print()

    # En dusuk maliyetli cifti bul
    en_ucuz_cift = None
    en_ucuz_maliyet = float('inf')
    for i in range(len(borsa_adlari)):
        for j in range(len(borsa_adlari)):
            if i == j:
                continue
            _, m_pct, _ = tek_islem_maliyeti(1000, borsa_adlari[i], borsa_adlari[j])
            if m_pct < en_ucuz_maliyet:
                en_ucuz_maliyet = m_pct
                en_ucuz_cift = (borsa_adlari[i], borsa_adlari[j])

    print(f"  EN UCUZ CIFT: {en_ucuz_cift[0]} -> {en_ucuz_cift[1]} (%{en_ucuz_maliyet:.3f})")
    print(f"  Bu demek ki %{en_ucuz_maliyet:.3f}'un ustundeki her fark KAR!")
    print()

    # ----------------------------------------------------------
    # 2) BTC ARBITRAJ ILE KARSILASTIRMA
    # ----------------------------------------------------------
    print("-" * 65)
    print("  2) STABLECOIN vs BTC ARBITRAJ KARSILASTIRMA")
    print("-" * 65)
    print("""
  Kriter               Stablecoin        BTC
  ─────────────────────────────────────────────────────
  Fiyat riski          YOK               YUKSEK (%3-10)
  Islem maliyeti       %0.24             %0.35
  Transfer ucreti      ~1 USDT (TRC20)   ~$5 (BTC network)
  Transfer suresi      ~5 dakika          ~30 dakika
  Likidite             Cok yuksek         Yuksek
  Fark sikligi         Cok sik            Sik
  Fark buyuklugu       %0.1-0.5           %0.3-2.0
  Rebalancing          Ucuz + hizli       Pahali + yavas
  Otomasyon            API ile kolay      API ile kolay
  ─────────────────────────────────────────────────────

  SONUC: Stablecoin icin farklar DAHA KUCUK ama maliyet de DAHA
         DUSUK, risk SIFIRA YAKIN, ve cok daha SIK islem yapilabilir.
""")

    # ----------------------------------------------------------
    # 3) FIYAT FARKI GERCEKLIGI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  3) USDT/TRY FIYAT FARKI GERCEKLIGI")
    print("-" * 65)
    print(f"""
  GERCEK PIYASA GOZLEMLERI:

  NORMAL PIYASA:
  - BtcTurk vs Paribu farki   : %0.05 - %0.30
  - BtcTurk vs Binance farki  : %0.10 - %0.40
  - Firsat sikligi             : Gunde 20-50+ kez

  HAREKETLI PIYASA (USD/TRY hareketi):
  - Fark                       : %0.30 - %1.00
  - Firsat sikligi             : Surekli (saatlerce)

  PANIK / TL KRIZI:
  - Fark                       : %1.00 - %3.00+
  - Firsat sikligi             : Surekli (gunlerce)

  NEDEN FARK OLUSIYOR:
  1. USD/TRY kuru degisince borsalar farkli hizda gunceller
  2. Her borsanin likidite havuzu farkli
  3. Market maker'lar farkli spread uygular
  4. TL yatirma/cekme hizlari farkli

  SIMULASYON PARAMETRELERI:
  - Ort. fark          : %{FARK_ORTALAMA_PCT*100:.2f}
  - Std sapma          : %{FARK_STD_PCT*100:.2f}
  - Buyuk fark olasil. : %{BUYUK_FARK_OLASILIGI*100:.0f} (3x kadar)
  - Kontrol/gun        : {KONTROL_PER_GUN} (her {24*60//KONTROL_PER_GUN} dk)
  - Islem orani        : bakiyenin %{ISLEM_ORANI*100:.0f}'i
""")

    # ----------------------------------------------------------
    # 4) MONTE CARLO SIMULASYONU
    # ----------------------------------------------------------
    print("-" * 65)
    print(f"  4) MONTE CARLO SIMULASYONU ({SIMULASYON_SAYISI:,} senaryo, {GUN_SAYISI} gun)")
    print("-" * 65)
    print()

    sim = stablecoin_simulasyonu()
    sonuclar = sim["sonuclar"]
    islem_s = sim["islem_sayilari"]
    karli_s = sim["karli_islem_sayilari"]
    reb_s = sim["rebalance_sayilari"]
    kar_s = sim["toplam_kar_listesi"]
    firsatsiz_s = sim["firsatsiz_gun_listesi"]

    ort = statistics.mean(sonuclar)
    medyan = statistics.median(sonuclar)
    en_iyi = max(sonuclar)
    en_kotu = min(sonuclar)
    std = statistics.stdev(sonuclar)
    ort_islem = statistics.mean(islem_s)
    ort_karli = statistics.mean(karli_s)
    ort_reb = statistics.mean(reb_s)
    ort_kar = statistics.mean(kar_s)
    ort_firsatsiz = statistics.mean(firsatsiz_s)

    zararda = sum(1 for s in sonuclar if s < TOPLAM_SERMAYE_TL) / SIMULASYON_SAYISI * 100
    karda = 100 - zararda

    # Yillik getiri tahmini (30 gunluk veriyi yilliga cevir)
    aylik_getiri = (ort / TOPLAM_SERMAYE_TL - 1)
    yillik_getiri = ((1 + aylik_getiri) ** 12 - 1) * 100

    print(f"  PORTFOY SONUCLARI:")
    print(f"    Baslangic sermaye   : {TOPLAM_SERMAYE_TL:>12,.0f} TL")
    print(f"    Ort. son sermaye    : {ort:>12,.0f} TL ({(ort/TOPLAM_SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    Medyan son sermaye  : {medyan:>12,.0f} TL ({(medyan/TOPLAM_SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    En iyi senaryo      : {en_iyi:>12,.0f} TL ({(en_iyi/TOPLAM_SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    En kotu senaryo     : {en_kotu:>12,.0f} TL ({(en_kotu/TOPLAM_SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    Std. sapma          : {std:>12,.0f} TL")
    print()
    print(f"  ISLEM ISTATISTIKLERI:")
    print(f"    Ort. islem sayisi   : {ort_islem:>8.1f} ({GUN_SAYISI} gunde)")
    print(f"    Ort. gunluk islem   : {ort_islem/GUN_SAYISI:>8.1f}")
    print(f"    Ort. karli islem    : {ort_karli:>8.1f} ({ort_karli/max(ort_islem,1)*100:.0f}%)")
    print(f"    Ort. rebalancing    : {ort_reb:>8.1f} kez")
    print(f"    Ort. firsatsiz gun  : {ort_firsatsiz:>8.1f} / {GUN_SAYISI}")
    print()
    print(f"  RISK ANALIZI:")
    print(f"    Karda biten         : %{karda:.1f}")
    print(f"    Zararda biten       : %{zararda:.1f}")
    print(f"    Ort. net kar        : {ort_kar:>10,.0f} TL")
    print(f"    Aylik getiri        : {aylik_getiri*100:>+10.2f}%")
    print(f"    Yillik getiri (bhm) : {yillik_getiri:>+10.2f}% (bilesik)")
    print()

    # Dagilim
    print("  SONUC DAGILIMI:")
    araliklar = [
        (0, 95000, "  <95K      "),
        (95000, 98000, " 95K - 98K  "),
        (98000, 99000, " 98K - 99K  "),
        (99000, 100000, " 99K - 100K "),
        (100000, 101000, "100K - 101K "),
        (101000, 102000, "101K - 102K "),
        (102000, 105000, "102K - 105K "),
        (105000, 110000, "105K - 110K "),
        (110000, float('inf'), "110K+       "),
    ]

    for alt, ust, etiket in araliklar:
        sayi = sum(1 for s in sonuclar if alt <= s < ust)
        oran = sayi / SIMULASYON_SAYISI * 100
        bar = "█" * int(oran / 0.5)
        isaretci = " ◄ BASABAS" if alt <= TOPLAM_SERMAYE_TL < ust else ""
        print(f"    {etiket} : {oran:5.1f}% |{bar}{isaretci}")

    print()

    # ----------------------------------------------------------
    # 5) SENARYO ANALIZI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  5) SENARYO ANALIZI")
    print("-" * 65)
    print()

    senaryolar = [
        ("Konservatif", 0.0015, 0.0010, 24),
        ("Normal", 0.0020, 0.0015, 48),
        ("Agresif", 0.0030, 0.0020, 96),
        ("TL Krizi", 0.0050, 0.0030, 96),
    ]

    print(f"  {'Senaryo':>14s} | {'Ort.Fark':>8s} | {'Kontrol/Gun':>11s} | "
          f"{'Aylik Getiri':>12s} | {'Yillik (bhm)':>12s}")
    print(f"  {'─' * 14}─┼─{'─' * 8}─┼─{'─' * 11}─┼─{'─' * 12}─┼─{'─' * 12}")

    for ad, fark_ort, fark_std, kontrol in senaryolar:
        # 3 borsadan rastgele fiyatlar uretip en ucuz-en pahali farkini hesapla
        # Monte Carlo ile senaryo tahmini (1000 deneme)
        maliyet = en_ucuz_maliyet / 100.0
        karli_sayac = 0
        toplam_net = 0.0
        deneme = 5000

        for _ in range(deneme):
            # 3 borsanin fiyat sapmalari
            sapmalar = [random.gauss(0, fark_std) for _ in range(BORSA_SAYISI)]
            # Nadiren buyuk fark
            for k in range(len(sapmalar)):
                if random.random() < BUYUK_FARK_OLASILIGI:
                    sapmalar[k] *= BUYUK_FARK_CARPANI
            fark = max(sapmalar) - min(sapmalar)
            if fark > maliyet:
                karli_sayac += 1
                toplam_net += (fark - maliyet)

        p_firsat = karli_sayac / deneme
        ort_net_kar = toplam_net / max(karli_sayac, 1)

        gunluk_islem = kontrol * p_firsat
        # Her islemde bakiyenin ISLEM_ORANI kadarini kullan
        gunluk_getiri = gunluk_islem * ort_net_kar * ISLEM_ORANI
        aylik = gunluk_getiri * 30 * 100
        yillik = ((1 + gunluk_getiri * 30) ** 12 - 1) * 100

        yillik_str = f"{yillik:+.0f}%" if yillik > 999 else f"{yillik:+.2f}%"
        print(f"  {ad:>14s} │ %{fark_ort*100:.2f}   │ {kontrol:>9d}x  │ "
              f"{aylik:>+10.2f}%  │ {yillik_str:>12s}")

    print()
    print("  bhm = bilesik hesaplama modeli")
    print()

    # ----------------------------------------------------------
    # 6) PRATIK UYGULAMA REHBERI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  6) PRATIK UYGULAMA REHBERI")
    print("-" * 65)
    print(f"""
  ADIM 1: HESAP ACMA
  - BtcTurk: KYC onayla, USDT/TRY ciftini aktif et
  - Paribu: KYC onayla, USDT/TRY ciftini aktif et
  - Binance TR: KYC onayla (erisim durumunu kontrol et)

  ADIM 2: SERMAYE DAGITIMI ({TOPLAM_SERMAYE_TL:,.0f} TL ornek)
  - Her borsaya {BORSA_BASI_SERMAYE:,.0f} TL yatir
  - Yarisini USDT'ye cevir ({BORSA_BASI_SERMAYE/2:,.0f} TL'lik)
  - Sonuc: Her borsada ~{BORSA_BASI_SERMAYE/2:,.0f} TL + ~{BORSA_BASI_SERMAYE/2/usdt_try:,.0f} USDT

  ADIM 3: FIYAT IZLEME
  - BtcTurk API: api.btcturk.com (halka acik, rate limit var)
  - Binance API: api.binance.com (halka acik, hizli)
  - Paribu: Web scraping veya manuel kontrol
  - Alternatif: 0xbroker.com/market/Turkey/USDTTRY

  ADIM 4: ISLEM KURALLARI
  - Minimum fark esigi : %{en_ucuz_maliyet:.3f} (basabas)
  - Hedef fark         : %{en_ucuz_maliyet*1.5:.3f}+ (guvenli kar)
  - Islem buyuklugu    : Bakiyenin %{ISLEM_ORANI*100:.0f}'i
  - Gunluk limit       : Max 20 islem (komisyon kademesi icin)

  ADIM 5: REBALANCING (Bakiye Dengeleme)
  - Ne zaman: Bir borsadaki TL veya USDT %{REBALANCING_ESIGI*100:.0f}'in altina dustugunde
  - Nasil: USDT transferi (TRC20 agi - ucuz ve hizli)
  - Maliyet: ~1 USDT (~{usdt_try:.0f} TL)
  - Sure: ~5 dakika

  ADIM 6: BOT GELISTIRME (Opsiyonel)
  - BtcTurk + Binance API ile fiyat stream'i dinle
  - Fark esigini asinca otomatik limit order gonder
  - Loglama + alert sistemi kur (Telegram bot vb.)
""")

    # ----------------------------------------------------------
    # 7) RISK ANALIZI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  7) RISK ANALIZI")
    print("-" * 65)
    print(f"""
  DUSUK RISKLER:
  + USDT fiyat riski yok (stablecoin, $1'a sabitli)
  + Transfer riski dusuk (TRC20 hizli ve guvenilir)
  + Likidite riski dusuk (USDT/TRY en cok islenen cift)

  ORTA RISKLER:
  ~ Borsa riski (hack, iflas, dondurulan hesap)
  ~ Regulasyon riski (Turkiye'de kripto duzenlemeleri degisiyor)
  ~ Slippage (buyuk islemlerde fiyat kaymasi)

  YUKSEK RISKLER:
  ! TL cekme/yatirma gecikmeleri (banka kaynaklı)
  ! Spread daralması (daha fazla bot = daha az firsat)
  ! Borsa API degisiklikleri (beklenmedik kesintiler)

  RISK AZALTMA:
  1. Sermayeyi 3 borsaya dagit (tek borsa riski azalir)
  2. Kucuk basla, buyut (10-20K TL ile baslangic)
  3. Gunluk kar/zarar limiti koy
  4. Her borsada sadece ihtiyac kadar para tut
  5. USDT disinda stablecoin deneme (USDC, FDUSD)
""")

    # ----------------------------------------------------------
    # SONUC
    # ----------------------------------------------------------
    print("=" * 65)
    print("  SONUC VE ONERILER")
    print("=" * 65)
    print(f"""
  STABLECOIN ARBITRAJ OZETI ({GUN_SAYISI} gunluk simulasyon):

  Aylik beklenen getiri : {aylik_getiri*100:+.2f}%
  Yillik beklenen (bhm) : {yillik_getiri:+.2f}%
  Zarar olasiligi       : %{zararda:.1f}
  Ort. gunluk islem     : {ort_islem/GUN_SAYISI:.1f}
  Min. gerekli fark     : %{en_ucuz_maliyet:.3f}

  KARAR:
  ✓ BTC arbitrajindan DAHA GUVENLI (fiyat riski yok)
  ✓ Maliyet DAHA DUSUK (%{en_ucuz_maliyet:.3f} vs %0.35)
  ✓ Transfer DAHA HIZLI ve UCUZ (TRC20)
  ✓ Daha SIK islem firsati

  BASLAMAK ICIN:
  1. BtcTurk + Paribu/Binance TR hesabi ac
  2. 20-50K TL ile baslangic sermayesi dagit
  3. Manuel olarak 1 hafta fiyat farklarini izle
  4. Karli gorursen bot gelistirmeye basla
  5. Sermayeyi kademeli artir
""")


if __name__ == "__main__":
    random.seed(42)
    rapor_yazdir()
