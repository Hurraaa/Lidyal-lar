"""
Kripto Arbitraj Fizibilite Analizi
==================================
Midas Kripto ile global borsalar (Binance vb.) arasinda
BTC arbitraj firsatlarinin analizi.

Midas'in halka acik API'si olmadigindan, Turkiye primi
modelleme + canli Binance/CoinGecko verisi ile analiz yapilir.
"""

import random
import statistics
import math
import json
import time
import ssl
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from datetime import datetime

# ============================================================
# PARAMETRELER
# ============================================================

# Sermaye
SERMAYE_TL = 100_000

# Midas komisyon oranlari (hacme gore kademeli)
# Kaynak: https://www.getmidas.com/ucretler/
MIDAS_KOMISYON_KADEMELERI = [
    (1_000_000, 0.0020),       # 0-1M TL: %0.20
    (10_000_000, 0.0015),      # 1M-10M TL: %0.15
    (100_000_000, 0.0012),     # 10M-100M TL: %0.12
    (float('inf'), 0.0010),    # 100M+ TL: %0.10
]

# Binance komisyon
BINANCE_KOMISYON = 0.0010      # %0.10

# BTC transfer parametreleri
BTC_ONAY_SAYISI = 3
BTC_BLOK_SURESI_DK = 10
BTC_TRANSFER_SURESI_DK = BTC_ONAY_SAYISI * BTC_BLOK_SURESI_DK  # ~30 dk
BTC_NETWORK_UCRETI_USD = 5.0

# Piyasa parametreleri
SLIPPAGE_PCT = 0.0010          # %0.10 slippage (tek yon)
BTC_SAATLIK_VOLATILITE = 0.015 # %1.5 saatlik volatilite (gercekci)

# Turkiye primi parametreleri (simulasyon icin)
TURKIYE_PRIMI_ORT_PCT = 0.015  # Ortalama %1.5
TURKIYE_PRIMI_STD_PCT = 0.010  # Std sapma %1.0

# Varsayilan fiyatlar (API basarisiz olursa)
VARSAYILAN_BTC_USD = 85_000
VARSAYILAN_USD_TRY = 38.50

# Monte Carlo
SIMULASYON_SAYISI = 5_000
GUN_SAYISI = 30
ISLEM_PER_GUN = 2


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
        return {
            "usd": btc.get("usd"),
            "try": btc.get("try"),
        }
    return None


def canli_veri_topla():
    """
    Canli piyasa verilerini toplar.
    Basarisiz olursa varsayilan degerler kullanilir.
    """
    sonuc = {
        "canli": False,
        "btc_usd": VARSAYILAN_BTC_USD,
        "usd_try": VARSAYILAN_USD_TRY,
        "btc_try_global": VARSAYILAN_BTC_USD * VARSAYILAN_USD_TRY,
        "kaynak": "SIMULASYON (varsayilan degerler)",
    }

    # Binance fiyati
    print("  Binance API sorgusu...", end=" ")
    binance_fiyat = binance_btc_fiyat_al()
    if binance_fiyat:
        sonuc["btc_usd"] = binance_fiyat
        sonuc["canli"] = True
        print(f"OK (${binance_fiyat:,.0f})")
    else:
        print("BASARISIZ (varsayilan kullaniliyor)")

    # CoinGecko fiyatlari
    time.sleep(1.5)  # Rate limit'e saygi
    print("  CoinGecko API sorgusu...", end=" ")
    cg = coingecko_fiyat_al()
    if cg and cg.get("usd") and cg.get("try"):
        sonuc["usd_try"] = cg["try"] / cg["usd"]
        sonuc["btc_try_global"] = cg["try"]
        sonuc["canli"] = True
        print(f"OK (${cg['usd']:,.0f} / {cg['try']:,.0f} TL)")
    else:
        print("BASARISIZ (varsayilan kullaniliyor)")

    # Global BTC/TRY hesapla
    if not (cg and cg.get("try")):
        sonuc["btc_try_global"] = sonuc["btc_usd"] * sonuc["usd_try"]

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


def tek_arbitraj_maliyeti(miktar_tl, btc_try_global, usd_try):
    """
    Tek bir arbitraj isleminin toplam maliyetini hesaplar.

    Senaryo: Global'den BTC al -> Midas'ta sat
    (veya tersi, maliyetler simetrik)

    Returns: (toplam_maliyet_tl, toplam_maliyet_pct)
    """
    # 1. Alis komisyonu (global taraf - Binance)
    alis_komisyon = miktar_tl * BINANCE_KOMISYON

    # 2. Satis komisyonu (Midas taraf)
    satis_komisyon = miktar_tl * midas_komisyon_hesapla(miktar_tl)

    # 3. BTC network transfer ucreti
    network_ucreti_tl = BTC_NETWORK_UCRETI_USD * usd_try

    # 4. Slippage (iki taraf)
    slippage = miktar_tl * SLIPPAGE_PCT * 2

    toplam = alis_komisyon + satis_komisyon + network_ucreti_tl + slippage
    toplam_pct = toplam / miktar_tl * 100

    return toplam, toplam_pct


def minimum_prim_hesapla(miktar_tl, usd_try):
    """Basabas icin gereken minimum Turkiye primini hesaplar (%)."""
    _, maliyet_pct = tek_arbitraj_maliyeti(miktar_tl, None, usd_try)
    return maliyet_pct


def zaman_riski_hesapla(prim_pct, transfer_dk=BTC_TRANSFER_SURESI_DK):
    """
    Transfer suresi boyunca fiyat hareketinin primi silme olasiligini hesaplar.

    BTC fiyati lognormal dagilim ile modellenir.
    """
    # Transfer suresi icin volatilite
    saat = transfer_dk / 60.0
    sigma = BTC_SAATLIK_VOLATILITE * math.sqrt(saat)

    # Primin silinme olasiligi (tek tarafli)
    # P(hareket > prim) = P(Z > prim/sigma)
    if sigma == 0:
        return 0.0

    z = (prim_pct / 100.0) / sigma

    # Normal dagilim CDF yaklasimlari (stdlib'de erfc yok)
    # P(Z > z) hesabi
    def norm_sf(x):
        """Standart normal survival function yaklasimi."""
        return 0.5 * math.erfc(x / math.sqrt(2))

    return norm_sf(z)


# ============================================================
# MONTE CARLO SIMULASYONU
# ============================================================

def arbitraj_simulasyonu(sermaye, btc_try_global, usd_try):
    """
    Monte Carlo ile arbitraj stratejisi simulasyonu.

    Her gun:
    - Turkiye primi rastgele belirlenir
    - Prim yeterli ise arbitraj yapilir
    - Transfer sirasinda fiyat hareketi simule edilir
    """
    son_sermayeler = []
    islem_sayilari = []
    basarili_islemler = []

    min_prim = minimum_prim_hesapla(sermaye, usd_try) / 100.0

    for _ in range(SIMULASYON_SAYISI):
        kasa = sermaye
        toplam_islem = 0
        basarili = 0

        for _ in range(GUN_SAYISI):
            for _ in range(ISLEM_PER_GUN):
                # Gunun Turkiye primini cek
                prim = random.gauss(TURKIYE_PRIMI_ORT_PCT, TURKIYE_PRIMI_STD_PCT)

                # Prim negatif olabilir (Turkiye'de ucuz)
                if prim <= min_prim:
                    continue  # Arbitraj karli degil, atlA

                toplam_islem += 1

                # Arbitraj islem buyuklugu (kasanin %50'si ile)
                islem_miktari = kasa * 0.5

                # Brut kar (prim - maliyet)
                _, maliyet_pct = tek_arbitraj_maliyeti(islem_miktari, btc_try_global, usd_try)
                brut_kar_pct = (prim * 100) - maliyet_pct

                # Transfer sirasinda fiyat hareketi (risk)
                saat = BTC_TRANSFER_SURESI_DK / 60.0
                sigma = BTC_SAATLIK_VOLATILITE * math.sqrt(saat)
                fiyat_hareketi = random.gauss(0, sigma)

                # Net kar (brut kar - fiyat hareketi riski)
                net_kar_pct = brut_kar_pct - abs(fiyat_hareketi) * 100

                if net_kar_pct > 0:
                    basarili += 1

                kasa += islem_miktari * (net_kar_pct / 100.0)

                if kasa <= 0:
                    kasa = 0
                    break

            if kasa <= 0:
                break

        son_sermayeler.append(kasa)
        islem_sayilari.append(toplam_islem)
        basarili_islemler.append(basarili)

    return son_sermayeler, islem_sayilari, basarili_islemler


# ============================================================
# RAPOR
# ============================================================

def rapor_yazdir():
    """Ana analiz raporu."""

    print()
    print("=" * 65)
    print("  KRIPTO ARBITRAJ FIZIBILITE ANALIZI")
    print("  Midas Kripto vs Global Piyasalar (Binance)")
    print("=" * 65)
    print()

    # ----------------------------------------------------------
    # 0) Canli veri toplama
    # ----------------------------------------------------------
    print("-" * 65)
    print("  PIYASA VERILERI")
    print("-" * 65)
    veri = canli_veri_topla()
    print()
    print(f"  Veri kaynagi    : {veri['kaynak']}")
    print(f"  BTC/USD         : ${veri['btc_usd']:>12,.0f}")
    print(f"  USD/TRY         :  {veri['usd_try']:>12,.2f} TL")
    print(f"  BTC/TRY (global): {veri['btc_try_global']:>12,.0f} TL")
    print(f"  Sermaye         : {SERMAYE_TL:>12,.0f} TL")
    print()

    btc_try = veri["btc_try_global"]
    usd_try = veri["usd_try"]

    # ----------------------------------------------------------
    # 1) MALIYET ANALIZI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  1) MALIYET ANALIZI - Tek Arbitraj Isleminin Maliyeti")
    print("-" * 65)
    print()

    miktar = SERMAYE_TL
    midas_kom = midas_komisyon_hesapla(miktar) * 100
    network_tl = BTC_NETWORK_UCRETI_USD * usd_try

    print(f"  Islem miktari     : {miktar:>12,.0f} TL")
    print(f"  Binance komisyon  : {miktar * BINANCE_KOMISYON:>12,.0f} TL (%{BINANCE_KOMISYON*100:.2f})")
    print(f"  Midas komisyon    : {miktar * midas_komisyon_hesapla(miktar):>12,.0f} TL (%{midas_kom:.2f})")
    print(f"  BTC network ucreti: {network_tl:>12,.0f} TL (${BTC_NETWORK_UCRETI_USD:.0f})")
    print(f"  Slippage (2 taraf): {miktar * SLIPPAGE_PCT * 2:>12,.0f} TL (%{SLIPPAGE_PCT*200:.2f})")

    toplam_maliyet, toplam_pct = tek_arbitraj_maliyeti(miktar, btc_try, usd_try)
    print(f"  ────────────────────────────────────")
    print(f"  TOPLAM MALIYET    : {toplam_maliyet:>12,.0f} TL (%{toplam_pct:.2f})")
    print()

    # ----------------------------------------------------------
    # 2) BASABAS PRIM TABLOSU
    # ----------------------------------------------------------
    print("-" * 65)
    print("  2) BASABAS PRIM TABLOSU")
    print("     (Kar etmek icin Turkiye priminin bu degerin USTUNDE olmasi gerek)")
    print("-" * 65)
    print()
    print(f"  {'Islem Buyuklugu':>20s} | {'Min. Prim (Basabas)':>20s} | {'Maliyet (TL)':>15s}")
    print(f"  {'─' * 20}─┼─{'─' * 20}─┼─{'─' * 15}")

    test_miktarlari = [10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000]
    for m in test_miktarlari:
        prim = minimum_prim_hesapla(m, usd_try)
        maliyet, _ = tek_arbitraj_maliyeti(m, btc_try, usd_try)
        print(f"  {m:>18,.0f} TL │ {prim:>19.2f}% │ {maliyet:>13,.0f} TL")

    print()
    print("  YORUM: Kucuk islemlerde network ucreti agir basar.")
    print("         100K+ TL islemlerde basabas primi ~%0.50'ye duser.")
    print()

    # ----------------------------------------------------------
    # 3) ZAMAN RISKI ANALIZI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  3) ZAMAN RISKI ANALIZI")
    print(f"     (BTC transferi ~{BTC_TRANSFER_SURESI_DK} dk surer, bu surede fiyat degisir)")
    print("-" * 65)
    print()

    transfer_saat = BTC_TRANSFER_SURESI_DK / 60.0
    sigma = BTC_SAATLIK_VOLATILITE * math.sqrt(transfer_saat)
    sigma_pct = sigma * 100

    print(f"  BTC saatlik volatilite  : %{BTC_SAATLIK_VOLATILITE*100:.1f}")
    print(f"  Transfer suresi         : {BTC_TRANSFER_SURESI_DK} dakika")
    print(f"  Transfer volatilitesi   : %{sigma_pct:.2f} (std sapma)")
    print()

    print(f"  {'Turkiye Primi':>15s} | {'Primin Silinme Olasiligi':>25s} | {'Risk Degerlendirmesi'}")
    print(f"  {'─' * 15}─┼─{'─' * 25}─┼─{'─' * 25}")

    prim_testleri = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
    for p in prim_testleri:
        risk = zaman_riski_hesapla(p, BTC_TRANSFER_SURESI_DK) * 100
        if risk > 40:
            seviye = "!!! COK RISKLI"
        elif risk > 25:
            seviye = "!! RISKLI"
        elif risk > 10:
            seviye = "! ORTA RISK"
        else:
            seviye = "KABUL EDILEBILIR"
        print(f"  {p:>14.1f}% │ {risk:>24.1f}% │ {seviye}")

    print()
    print(f"  YORUM: %{sigma_pct:.2f} volatilite ile, %1 prim bile riskli.")
    print(f"         Guvende olmak icin en az %2-3 prim gerekir.")
    print()

    # ----------------------------------------------------------
    # 4) TURKIYE PRIMI GERCEKLIGI
    # ----------------------------------------------------------
    print("-" * 65)
    print("  4) TURKIYE PRIMI GERCEKLIGI")
    print("-" * 65)
    print("""
  Turkiye'deki kripto borsalarinda (Midas, BtcTurk, Paribu)
  zaman zaman global fiyatlara gore prim olusur. Ancak:

  - Normal zamanlarda prim: %0.5 - %1.5 (YETERSIZ)
  - Panik/FOMO zamanlarinda: %2 - %5 (FIRSATLAR OLABILIR)
  - Nadir krizlerde (2021 Turkiye primi): %5 - %15

  Ortalama prim: ~%1.5 (simulasyonda bu deger kullanildi)
  Prim std sapma: ~%1.0 (degiskenlik)
""")

    # ----------------------------------------------------------
    # 5) MONTE CARLO SIMULASYONU
    # ----------------------------------------------------------
    print("-" * 65)
    print(f"  5) MONTE CARLO SIMULASYONU ({SIMULASYON_SAYISI:,} senaryo, {GUN_SAYISI} gun)")
    print("-" * 65)
    print()

    son_sermayeler, islem_sayilari, basarili_islemler = arbitraj_simulasyonu(
        SERMAYE_TL, btc_try, usd_try
    )

    ort_sermaye = statistics.mean(son_sermayeler)
    medyan_sermaye = statistics.median(son_sermayeler)
    en_iyi = max(son_sermayeler)
    en_kotu = min(son_sermayeler)
    std_dev = statistics.stdev(son_sermayeler)
    ort_islem = statistics.mean(islem_sayilari)
    ort_basarili = statistics.mean(basarili_islemler)

    zararda = sum(1 for s in son_sermayeler if s < SERMAYE_TL) / SIMULASYON_SAYISI * 100
    karda = 100 - zararda

    print(f"  Simulasyon parametreleri:")
    print(f"    Turkiye primi ort.  : %{TURKIYE_PRIMI_ORT_PCT*100:.1f}")
    print(f"    Turkiye primi std.  : %{TURKIYE_PRIMI_STD_PCT*100:.1f}")
    print(f"    Islem/gun           : {ISLEM_PER_GUN}")
    print(f"    Islem buyuklugu     : kasanin %50'si")
    print()
    print(f"  Sonuclar:")
    print(f"    Ort. son sermaye    : {ort_sermaye:>12,.0f} TL ({(ort_sermaye/SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    Medyan son sermaye  : {medyan_sermaye:>12,.0f} TL ({(medyan_sermaye/SERMAYE_TL-1)*100:+.2f}%)")
    print(f"    En iyi senaryo      : {en_iyi:>12,.0f} TL")
    print(f"    En kotu senaryo     : {en_kotu:>12,.0f} TL")
    print(f"    Std. sapma          : {std_dev:>12,.0f} TL")
    print()
    print(f"    Ort. islem sayisi   : {ort_islem:>8.1f} ({GUN_SAYISI} gunde)")
    print(f"    Ort. basarili islem : {ort_basarili:>8.1f}")
    print(f"    Karda biten         : %{karda:.1f}")
    print(f"    Zararda biten       : %{zararda:.1f}")
    print()

    # Dagilim
    print("  Sonuc dagilimi:")
    araliklar = [
        (0, 80000, " <80K      "),
        (80000, 90000, " 80K - 90K "),
        (90000, 95000, " 90K - 95K "),
        (95000, 100000, " 95K - 100K"),
        (100000, 105000, "100K - 105K"),
        (105000, 110000, "105K - 110K"),
        (110000, 120000, "110K - 120K"),
        (120000, float('inf'), "120K+      "),
    ]

    for alt, ust, etiket in araliklar:
        sayi = sum(1 for s in son_sermayeler if alt <= s < ust)
        oran = sayi / SIMULASYON_SAYISI * 100
        bar = "#" * int(oran / 0.5)
        print(f"    {etiket} : {oran:5.1f}% |{bar}")

    print()

    # ----------------------------------------------------------
    # 6) PRATIK ENGELLER
    # ----------------------------------------------------------
    print("-" * 65)
    print("  6) PRATIK ENGELLER (Simulasyonda Yer Almayan)")
    print("-" * 65)
    print("""
  a) PARA TRANSFERI:
     - TL'yi Midas'tan cekmek: 1-2 is gunu (EFT/havale)
     - USDT/BTC transferi: 10-60 dk (ag yogunluguna bagli)
     - Sermaye iki borsada da kilit olur

  b) MIDAS'TAN KRIPTO CEKME LIMITLERI:
     - Gunluk cekim limitleri olabilir
     - KYC/AML gecikmeleri yasanabilir

  c) VERGI:
     - Turkiye'de kripto geliri vergilendirilir
     - Her islem vergiye tabi olabilir

  d) REGÜLASYON RISKI:
     - SPK/MASAK duzenleme degisiklikleri
     - Kripto transferlere kisitlama gelebilir

  e) LIKIDITE:
     - Buyuk islemlerde orderbook derinligi yetersiz
     - Slippage beklenenden cok daha yuksek olabilir
""")

    # ----------------------------------------------------------
    # SONUC
    # ----------------------------------------------------------
    print("=" * 65)
    print("  SONUC VE ONERILER")
    print("=" * 65)

    basabas = minimum_prim_hesapla(SERMAYE_TL, usd_try)

    print(f"""
  KISA CEVAP: Arbitraj TEORIK OLARAK MUMKUN ama PRATIK'TE COK ZOR.

  NEDEN?

  1. MALIYET BARAJI:
     - 100.000 TL islem icin basabas primi: %{basabas:.2f}
     - Normal piyasa primi: %0.5-1.5 (genelde YETERSIZ)
     - Sadece panik/FOMO zamanlarinda %3-5 prim olusur

  2. ZAMAN RISKI:
     - BTC transferi ~30 dk surer
     - Bu surede %{sigma_pct:.2f} fiyat degisimi olabilir
     - Primi silecek kadar buyuk bir hareket %{zaman_riski_hesapla(basabas)*100:.0f} olasilikla gerceklesir

  3. SERMAYE VERIMSIZLIGI:
     - Para iki borsada kilit kalir
     - Gunluk cekim limitleri arbitraji yavaslatir
     - Ayni sermaye ile az sayida islem yapilabilir

  4. MONTE CARLO SONUCU:
     - Ort. %1.5 prim ile 30 gunde: {(ort_sermaye/SERMAYE_TL-1)*100:+.2f}% getiri
     - %{zararda:.0f} olasilikla zarar, %{karda:.0f} olasilikla kar

  ONERILER:
  - Arbitraj yerine SPOT AL-TUT veya DCA stratejisi daha guvenli
  - Arbitraj yapacaksan: USDT ciftlerini kullan (daha hizli transfer)
  - Buyuk primler (%3+) olusan NADIR anlar icin hazirlikli ol
  - Her iki borsada da onceden sermaye bulundur (transfer bekleme)
  - Kucuk miktarlarla test et, buyuk sermaye riske atma
""")


if __name__ == "__main__":
    random.seed(42)
    rapor_yazdir()
