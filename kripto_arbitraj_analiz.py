"""
Eszamanli Kripto Arbitraj Analizi
=================================
Iki borsada (Midas + Binance) ayni anda hazir sermaye ile
esanli al-sat arbitraj stratejisinin fizibilite analizi.

Model: Her iki borsada hem TL hem coin bulundur.
       Fiyat farki olustugunda ucuz tarafta AL, pahali tarafta SAT.
       Kripto transferi YOK - esanli islem.
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

# Sermaye (toplam 100K, iki borsaya esit dagitilir)
TOPLAM_SERMAYE_TL = 100_000
BORSA_BASI_SERMAYE = TOPLAM_SERMAYE_TL / 2  # 50K Midas, 50K Binance
# Her borsada: %50 TL + %50 coin degeri olarak baslar

# Midas komisyon oranlari (hacme gore kademeli)
MIDAS_KOMISYON_KADEMELERI = [
    (1_000_000, 0.0020),       # 0-1M TL: %0.20
    (10_000_000, 0.0015),      # 1M-10M TL: %0.15
    (100_000_000, 0.0012),     # 10M-100M TL: %0.12
    (float('inf'), 0.0010),    # 100M+ TL: %0.10
]

# Binance komisyon
BINANCE_KOMISYON = 0.0010      # %0.10

# Slippage (esanli islemde daha dusuk)
SLIPPAGE_PCT = 0.0005          # %0.05 (tek yon)

# Fiyat farki modeli (borsalar arasi anlik fark)
# Gercekte farklar genelde %0.1-0.5 arasi, bazen %1-3
FIYAT_FARKI_ORT_PCT = 0.003    # Ortalama %0.3
FIYAT_FARKI_STD_PCT = 0.005    # Std sapma %0.5

# Rebalancing parametreleri
BTC_NETWORK_UCRETI_USD = 5.0   # Rebalancing icin transfer ucreti
REBALANCING_ESIGI = 0.80       # Bir taraftaki TL veya coin %80'den fazla
                                # azaldiysa rebalancing yap

# Varsayilan fiyatlar
VARSAYILAN_BTC_USD = 85_000
VARSAYILAN_USD_TRY = 38.50

# Monte Carlo
SIMULASYON_SAYISI = 5_000
GUN_SAYISI = 30
FIRSAT_PER_GUN = 10            # Gunde kac kez fiyat farki kontrol edilir
ISLEM_ORANI = 0.30             # Her firsatta bakiyenin %30'u ile islem


# ============================================================
# API FONKSIYONLARI (onceki versiyondan)
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


def binance_btc_fiyat_al():
    """Binance'den BTC/USDT fiyatini alir."""
    veri = api_istegi_yap("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    if veri and "price" in veri:
        return float(veri["price"])
    return None


def coingecko_fiyat_al():
    """CoinGecko'dan BTC/USD ve BTC/TRY fiyatlarini alir."""
    veri = api_istegi_yap(
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin&vs_currencies=usd,try"
    )
    if veri and "bitcoin" in veri:
        btc = veri["bitcoin"]
        return {"usd": btc.get("usd"), "try": btc.get("try")}
    return None


def canli_veri_topla():
    """Canli piyasa verilerini toplar."""
    sonuc = {
        "canli": False,
        "btc_usd": VARSAYILAN_BTC_USD,
        "usd_try": VARSAYILAN_USD_TRY,
        "btc_try": VARSAYILAN_BTC_USD * VARSAYILAN_USD_TRY,
        "kaynak": "SIMULASYON (varsayilan degerler)",
    }

    print("  Binance API sorgusu...", end=" ")
    binance_fiyat = binance_btc_fiyat_al()
    if binance_fiyat:
        sonuc["btc_usd"] = binance_fiyat
        sonuc["canli"] = True
        print(f"OK (${binance_fiyat:,.0f})")
    else:
        print("BASARISIZ (varsayilan kullaniliyor)")

    time.sleep(1.5)
    print("  CoinGecko API sorgusu...", end=" ")
    cg = coingecko_fiyat_al()
    if cg and cg.get("usd") and cg.get("try"):
        sonuc["usd_try"] = cg["try"] / cg["usd"]
        sonuc["btc_try"] = cg["try"]
        sonuc["canli"] = True
        print(f"OK (${cg['usd']:,.0f} / {cg['try']:,.0f} TL)")
    else:
        print("BASARISIZ (varsayilan kullaniliyor)")

    if not (cg and cg.get("try")):
        sonuc["btc_try"] = sonuc["btc_usd"] * sonuc["usd_try"]

    sonuc["kaynak"] = "CANLI VERI" if sonuc["canli"] else "SIMULASYON (varsayilan degerler)"
    return sonuc


# ============================================================
# MATEMATIKSEL ANALIZ
# ============================================================

def midas_komisyon_hesapla(hacim_tl):
    """Midas'in kademeli komisyon oranini doner."""
    for limit, oran in MIDAS_KOMISYON_KADEMELERI:
        if hacim_tl <= limit:
            return oran
    return MIDAS_KOMISYON_KADEMELERI[-1][1]


def esanli_arbitraj_maliyeti(miktar_tl):
    """
    Esanli (simultaneous) arbitraj isleminin maliyetini hesaplar.

    Bir borsada AL + diger borsada SAT (ayni anda).
    Network ucreti YOK, transfer YOK.

    Returns: (toplam_maliyet_tl, toplam_maliyet_pct)
    """
    # Alis komisyonu (ucuz borsa - biri Midas biri Binance olabilir)
    # En kotu senaryo: Midas'tan al (yuksek komisyon)
    midas_kom = miktar_tl * midas_komisyon_hesapla(miktar_tl)
    binance_kom = miktar_tl * BINANCE_KOMISYON

    # Slippage (iki taraf)
    slippage = miktar_tl * SLIPPAGE_PCT * 2

    toplam = midas_kom + binance_kom + slippage
    toplam_pct = toplam / miktar_tl * 100

    return toplam, toplam_pct


def minimum_fark_hesapla(miktar_tl):
    """Basabas icin gereken minimum borsalar-arasi fiyat farki (%)."""
    _, maliyet_pct = esanli_arbitraj_maliyeti(miktar_tl)
    return maliyet_pct


# ============================================================
# MONTE CARLO SIMULASYONU
# ============================================================

def arbitraj_simulasyonu(btc_try_fiyat, usd_try):
    """
    Esanli arbitraj Monte Carlo simulasyonu.

    Her borsada baslangicta:
    - %50 TL bakiye
    - %50 degerinde BTC bakiye

    Fiyat farki olustugunda ucuz tarafta al, pahali tarafta sat.
    Rebalancing gerektiginde transfer maliyeti odenur.
    """
    sonuclar = []
    toplam_islem_sayilari = []
    toplam_rebalance_sayilari = []
    toplam_karli_islemler = []

    for _ in range(SIMULASYON_SAYISI):
        # Her borsada baslangic bakiyeleri
        # Midas: 25K TL + 25K degerinde BTC
        # Binance: 25K TL + 25K degerinde BTC
        midas_tl = BORSA_BASI_SERMAYE / 2
        midas_btc = (BORSA_BASI_SERMAYE / 2) / btc_try_fiyat  # BTC miktari

        binance_tl = BORSA_BASI_SERMAYE / 2
        binance_btc = (BORSA_BASI_SERMAYE / 2) / btc_try_fiyat

        islem_sayisi = 0
        rebalance_sayisi = 0
        karli_islem = 0
        rebalance_maliyet_toplam = 0

        for _ in range(GUN_SAYISI):
            for _ in range(FIRSAT_PER_GUN):
                # Anlik fiyat farkini cek (mutlak deger - yon rastgele)
                fark_pct = abs(random.gauss(FIYAT_FARKI_ORT_PCT, FIYAT_FARKI_STD_PCT))

                # Basabas kontrolu
                # Islem miktari: mevcut bakiyenin ISLEM_ORANI kadar
                potansiyel_islem_tl = min(midas_tl, binance_btc * btc_try_fiyat,
                                          binance_tl, midas_btc * btc_try_fiyat)
                potansiyel_islem_tl *= ISLEM_ORANI

                if potansiyel_islem_tl < 100:  # Minimum islem limiti
                    continue

                basabas = minimum_fark_hesapla(potansiyel_islem_tl) / 100.0

                if fark_pct <= basabas:
                    continue  # Fark yeterli degil

                islem_sayisi += 1

                # Yon: Midas pahali mi Binance pahali mi? (esit olasilik)
                midas_pahali = random.random() < 0.5

                islem_btc = potansiyel_islem_tl / btc_try_fiyat
                _, maliyet_pct = esanli_arbitraj_maliyeti(potansiyel_islem_tl)
                net_kar_pct = (fark_pct * 100) - maliyet_pct
                net_kar_tl = potansiyel_islem_tl * (net_kar_pct / 100.0)

                if net_kar_tl > 0:
                    karli_islem += 1

                if midas_pahali:
                    # Binance'den al (TL ile), Midas'tan sat (BTC ile)
                    binance_tl -= potansiyel_islem_tl
                    binance_btc += islem_btc
                    midas_btc -= islem_btc
                    midas_tl += potansiyel_islem_tl
                else:
                    # Midas'tan al (TL ile), Binance'den sat (BTC ile)
                    midas_tl -= potansiyel_islem_tl
                    midas_btc += islem_btc
                    binance_btc -= islem_btc
                    binance_tl += potansiyel_islem_tl

                # Net kari ekle (iki tarafa esit dagit)
                midas_tl += net_kar_tl / 2
                binance_tl += net_kar_tl / 2

                # Bakiye kontrolu (negatif olamaz)
                if midas_tl < 0 or binance_tl < 0 or midas_btc < 0 or binance_btc < 0:
                    # Islem yapilamazdi, geri al
                    if midas_pahali:
                        binance_tl += potansiyel_islem_tl
                        binance_btc -= islem_btc
                        midas_btc += islem_btc
                        midas_tl -= potansiyel_islem_tl
                    else:
                        midas_tl += potansiyel_islem_tl
                        midas_btc -= islem_btc
                        binance_btc += islem_btc
                        binance_tl -= potansiyel_islem_tl
                    midas_tl -= net_kar_tl / 2
                    binance_tl -= net_kar_tl / 2
                    islem_sayisi -= 1
                    if net_kar_tl > 0:
                        karli_islem -= 1
                    continue

            # Gun sonu: Rebalancing gerekli mi?
            midas_toplam = midas_tl + midas_btc * btc_try_fiyat
            binance_toplam = binance_tl + binance_btc * btc_try_fiyat
            genel_toplam = midas_toplam + binance_toplam

            # Bir borsadaki TL veya coin cok azaldiysa rebalance
            midas_tl_oran = midas_tl / midas_toplam if midas_toplam > 0 else 0
            binance_tl_oran = binance_tl / binance_toplam if binance_toplam > 0 else 0

            rebalance_gerek = (midas_tl_oran < (1 - REBALANCING_ESIGI)
                               or midas_tl_oran > REBALANCING_ESIGI
                               or binance_tl_oran < (1 - REBALANCING_ESIGI)
                               or binance_tl_oran > REBALANCING_ESIGI)

            if rebalance_gerek and genel_toplam > 0:
                rebalance_sayisi += 1
                # Rebalancing maliyeti: BTC transfer ucreti
                reb_maliyet = BTC_NETWORK_UCRETI_USD * usd_try
                rebalance_maliyet_toplam += reb_maliyet

                # Yeniden dengele: her borsada %50 TL, %50 coin
                hedef_borsa = genel_toplam / 2
                hedef_tl = hedef_borsa / 2
                hedef_btc = (hedef_borsa / 2) / btc_try_fiyat

                midas_tl = hedef_tl - reb_maliyet / 4
                midas_btc = hedef_btc
                binance_tl = hedef_tl - reb_maliyet / 4
                binance_btc = hedef_btc

        # Simulasyon sonu: toplam deger
        toplam_deger = midas_tl + midas_btc * btc_try_fiyat + binance_tl + binance_btc * btc_try_fiyat
        sonuclar.append(toplam_deger)
        toplam_islem_sayilari.append(islem_sayisi)
        toplam_rebalance_sayilari.append(rebalance_sayisi)
        toplam_karli_islemler.append(karli_islem)

    return sonuclar, toplam_islem_sayilari, toplam_rebalance_sayilari, toplam_karli_islemler


# ============================================================
# RAPOR
# ============================================================

def rapor_yazdir():
    """Ana analiz raporu."""

    print()
    print("=" * 65)
    print("  ESZAMANLI KRIPTO ARBITRAJ ANALIZI")
    print("  Midas + Binance - Ayni Anda Al/Sat (Transfer Yok)")
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
    print(f"  BTC/USD           : ${veri['btc_usd']:>12,.0f}")
    print(f"  USD/TRY           :  {veri['usd_try']:>12,.2f} TL")
    print(f"  BTC/TRY           : {veri['btc_try']:>12,.0f} TL")
    print()

    btc_try = veri["btc_try"]
    usd_try = veri["usd_try"]

    # ----------------------------------------------------------
    # Strateji aciklamasi
    # ----------------------------------------------------------
    print("-" * 65)
    print("  STRATEJI ACIKLAMASI")
    print("-" * 65)
    print(f"""
  Toplam sermaye   : {TOPLAM_SERMAYE_TL:>10,.0f} TL
  Midas'ta         : {BORSA_BASI_SERMAYE:>10,.0f} TL (yarisi TL, yarisi BTC)
  Binance'de       : {BORSA_BASI_SERMAYE:>10,.0f} TL (yarisi TL, yarisi BTC)

  NASIL CALISIR:
  1. Her iki borsada da hem TL hem BTC hazir bekler
  2. Borsalar arasi fiyat farki olustu mu anlik kontrol
  3. Ucuz borsada AL + pahali borsada ayni anda SAT
  4. Kripto transferi YOK = zaman riski YOK
  5. Bakiyeler dengesizlestikce rebalancing yapilir
""")

    # ----------------------------------------------------------
    # 1) MALIYET ANALIZI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  1) MALIYET ANALIZI (Tek Esanli Arbitraj Islemi)")
    print("-" * 65)
    print()

    miktar = 25_000  # Tipik islem buyuklugu (bakiyenin %30'u)
    midas_kom = midas_komisyon_hesapla(miktar) * 100

    print(f"  Islem miktari       : {miktar:>10,.0f} TL")
    print(f"  Midas komisyon      : {miktar * midas_komisyon_hesapla(miktar):>10,.0f} TL (%{midas_kom:.2f})")
    print(f"  Binance komisyon    : {miktar * BINANCE_KOMISYON:>10,.0f} TL (%{BINANCE_KOMISYON*100:.2f})")
    print(f"  Slippage (2 taraf)  : {miktar * SLIPPAGE_PCT * 2:>10,.0f} TL (%{SLIPPAGE_PCT*200:.2f})")
    print(f"  Network ucreti      :          0 TL (transfer yok!)")

    toplam_mal, toplam_pct = esanli_arbitraj_maliyeti(miktar)
    print(f"  ─────────────────────────────────────")
    print(f"  TOPLAM MALIYET      : {toplam_mal:>10,.0f} TL (%{toplam_pct:.2f})")
    print()

    # Transfer modeli ile karsilastirma
    transfer_maliyet = toplam_mal + BTC_NETWORK_UCRETI_USD * usd_try
    print(f"  KARSILASTIRMA:")
    print(f"    Esanli model      : %{toplam_pct:.2f} maliyet")
    print(f"    Transfer modeli   : %{transfer_maliyet / miktar * 100:.2f} maliyet (+network ucreti)")
    print(f"    Kazanc            : %{(transfer_maliyet / miktar * 100) - toplam_pct:.2f} daha ucuz!")
    print()

    # ----------------------------------------------------------
    # 2) BASABAS FIYAT FARKI TABLOSU
    # ----------------------------------------------------------
    print("-" * 65)
    print("  2) BASABAS FIYAT FARKI TABLOSU")
    print("     (Kar icin borsalar arasi farkin bu degerin USTUNDE olmasi gerek)")
    print("-" * 65)
    print()
    print(f"  {'Islem Buyuklugu':>20s} | {'Min. Fark (Basabas)':>20s} | {'Maliyet (TL)':>12s}")
    print(f"  {'─' * 20}─┼─{'─' * 20}─┼─{'─' * 12}")

    test_miktarlari = [5_000, 10_000, 25_000, 50_000, 100_000, 250_000]
    for m in test_miktarlari:
        fark = minimum_fark_hesapla(m)
        maliyet, _ = esanli_arbitraj_maliyeti(m)
        print(f"  {m:>18,.0f} TL │ {fark:>19.2f}% │ {maliyet:>10,.0f} TL")

    print()
    print(f"  YORUM: Network ucreti olmadigi icin maliyet SABIT %{toplam_pct:.2f}.")
    print(f"         Bu, transfer modelinden cok daha dusuk!")
    print()

    # ----------------------------------------------------------
    # 3) FIYAT FARKI GERCEKLIGI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  3) BORSALAR ARASI FIYAT FARKI GERCEKLIGI")
    print("-" * 65)
    print("""
  Turkiye borsalari ile global borsalar arasindaki farklar:

  NORMAL PIYASA:
  - Anlik fark: %0.1 - %0.5 (cogunlukla)
  - Firsatlar: Gunde 5-15 kez %0.3+ fark olusabilir
  - Sure: Fark genelde saniyeler-dakikalar icinde kapanir

  HAREKETLI PIYASA:
  - Anlik fark: %0.5 - %2.0
  - Firsatlar: Gunde 20-50 kez
  - Sure: Daha uzun surebilir (dakikalar)

  PANIK/FOMO:
  - Anlik fark: %1 - %5+
  - Firsatlar: Surekli (saatlerce devam edebilir)

  SIMULASYON PARAMETRELERI:""")
    print(f"  - Ort. fiyat farki  : %{FIYAT_FARKI_ORT_PCT*100:.1f}")
    print(f"  - Std sapma          : %{FIYAT_FARKI_STD_PCT*100:.1f}")
    print(f"  - Kontrol/gun        : {FIRSAT_PER_GUN}")
    print(f"  - Islem orani        : bakiyenin %{ISLEM_ORANI*100:.0f}'i")
    print()

    # ----------------------------------------------------------
    # 4) REBALANCING ANALIZI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  4) REBALANCING ANALIZI")
    print("-" * 65)
    reb_maliyet_tl = BTC_NETWORK_UCRETI_USD * usd_try
    print(f"""
  PROBLEM: Her islemde bir borsadaki TL artar, coin azalir.
           Diger borsada tersi olur. Bir noktada islem yapilamaz.

  COZUM:  Periyodik olarak bakiyeleri yeniden dengele.
          (Kripto transfer veya TL havale ile)

  REBALANCING MALIYETI:
  - BTC transfer ucreti : {reb_maliyet_tl:>8,.0f} TL (${BTC_NETWORK_UCRETI_USD:.0f})
  - Rebalancing esigi   : Bir bakiye %{(1-REBALANCING_ESIGI)*100:.0f}'in altina dustugunde
  - Tahmini siklik      : Her 5-10 islemde bir
  - Bu maliyet kari azaltir ama transfer modelinden yine UCUZ
""")

    # ----------------------------------------------------------
    # 5) MONTE CARLO SIMULASYONU
    # ----------------------------------------------------------
    print("-" * 65)
    print(f"  5) MONTE CARLO SIMULASYONU ({SIMULASYON_SAYISI:,} senaryo, {GUN_SAYISI} gun)")
    print("-" * 65)
    print()

    sonuclar, islem_sayilari, reb_sayilari, karli_sayilari = arbitraj_simulasyonu(
        btc_try, usd_try
    )

    ort = statistics.mean(sonuclar)
    medyan = statistics.median(sonuclar)
    en_iyi = max(sonuclar)
    en_kotu = min(sonuclar)
    std = statistics.stdev(sonuclar)
    ort_islem = statistics.mean(islem_sayilari)
    ort_reb = statistics.mean(reb_sayilari)
    ort_karli = statistics.mean(karli_sayilari)

    zararda = sum(1 for s in sonuclar if s < TOPLAM_SERMAYE_TL) / SIMULASYON_SAYISI * 100
    karda = 100 - zararda

    print(f"  Sonuclar:")
    print(f"    Ort. son sermaye    : {ort:>12,.0f} TL ({(ort/TOPLAM_SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    Medyan son sermaye  : {medyan:>12,.0f} TL ({(medyan/TOPLAM_SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    En iyi senaryo      : {en_iyi:>12,.0f} TL ({(en_iyi/TOPLAM_SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    En kotu senaryo     : {en_kotu:>12,.0f} TL ({(en_kotu/TOPLAM_SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    Std. sapma          : {std:>12,.0f} TL")
    print()
    print(f"    Ort. islem sayisi   : {ort_islem:>8.1f} ({GUN_SAYISI} gunde)")
    print(f"    Ort. karli islem    : {ort_karli:>8.1f} ({ort_karli/max(ort_islem,1)*100:.0f}%)")
    print(f"    Ort. rebalancing    : {ort_reb:>8.1f} kez")
    print(f"    Karda biten         : %{karda:.1f}")
    print(f"    Zararda biten       : %{zararda:.1f}")
    print()

    # Dagilim
    print("  Sonuc dagilimi:")
    araliklar = [
        (0, 90000, " <90K      "),
        (90000, 95000, " 90K - 95K "),
        (95000, 98000, " 95K - 98K "),
        (98000, 100000, " 98K - 100K"),
        (100000, 102000, "100K - 102K"),
        (102000, 105000, "102K - 105K"),
        (105000, 110000, "105K - 110K"),
        (110000, float('inf'), "110K+      "),
    ]

    for alt, ust, etiket in araliklar:
        sayi = sum(1 for s in sonuclar if alt <= s < ust)
        oran = sayi / SIMULASYON_SAYISI * 100
        bar = "#" * int(oran / 0.5)
        print(f"    {etiket} : {oran:5.1f}% |{bar}")

    print()

    # ----------------------------------------------------------
    # 6) PRATIK GEREKSINIMLER
    # ----------------------------------------------------------
    print("-" * 65)
    print("  6) PRATIK GEREKSINIMLER")
    print("-" * 65)
    print(f"""
  BU STRATEJI ICIN GEREKENLER:

  a) SERMAYE: Iki borsada da hem TL hem coin hazir olmali
     - Toplam {TOPLAM_SERMAYE_TL:,.0f} TL ({BORSA_BASI_SERMAYE:,.0f} TL x 2 borsa)
     - Sermaye verimliligi: %50 (paranin yarisi "bekleme"de)

  b) HIZ: Fiyat farkini ANINDA gormen ve islem yapman gerek
     - Manuel takip ile ZOR (fark saniyeler icinde kapanir)
     - Bot/API gerekli ama Midas'in halka acik API'si YOK
     - Manuel yapacaksan sadece BUYUK farklari (%1+) yakala

  c) IKI BORSADA DA HESAP: KYC onaylanmis hesap
     - Midas: TC vatandasi, KYC onaylanmis
     - Binance: Turkiye'den erisim durumu degisken

  d) IZLEME ARACI: Fiyat farklarini gercek zamanli izle
     - TradingView, CoinGecko gibi araclarda iki borsayi karsılastir
     - Veya kendi izleme scriptini yaz (Binance API ile)

  e) VERGI: Her islem vergi yukumlulugu olusturabilir
""")

    # ----------------------------------------------------------
    # SONUC
    # ----------------------------------------------------------
    print("=" * 65)
    print("  SONUC VE ONERILER")
    print("=" * 65)

    basabas = minimum_fark_hesapla(25_000)

    print(f"""
  KISA CEVAP: Esanli arbitraj, transfer modelinden COK DAHA IYI.

  AVANTAJLAR:
  + Transfer riski YOK (aynı anda al-sat)
  + Network ucreti YOK (normal islemlerde)
  + Maliyet sadece %{basabas:.2f} (komisyon + slippage)
  + Daha sik islem yapilabilir

  DEZAVANTAJLAR:
  - 2x sermaye gerekir (iki borsada da para olmali)
  - Sermayenin %50'si "bekleme"de (coin olarak tutuluyor)
  - Rebalancing gerekir (periyodik transfer maliyeti)
  - Manuel yapmak ZOR (hiz gerek, bot/API lazim)
  - Midas'in API'si yok (otomasyon zor)

  MONTE CARLO SONUCU ({GUN_SAYISI} gun):
  - Ort. getiri    : {(ort/TOPLAM_SERMAYE_TL-1)*100:+.2f}%
  - Medyan getiri  : {(medyan/TOPLAM_SERMAYE_TL-1)*100:+.2f}%
  - Zarar olasiligi: %{zararda:.1f}

  PRATIK ONERILER:
  1. Once KUCUK sermaye ile dene (10-20K TL)
  2. Manuel yapacaksan sadece %1+ farklari hedefle
  3. Binance API ile fiyat izleme botu yaz
  4. Midas'ta manuel islem yap (API yok)
  5. Rebalancing icin USDT/TRC20 kullan (ucuz transfer)
  6. Gunluk kar hedefi koy, asiri islem yapma
""")


if __name__ == "__main__":
    random.seed(42)
    rapor_yazdir()
