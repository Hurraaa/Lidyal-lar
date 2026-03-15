"""
Kripto Trading Strateji Simulasyonu
===================================
Strateji: 100.000 TL sermaye, tum kriptolara esit dagitim,
          %1 stop-loss, %2 take-profit

Bu script stratejinin matematiksel analizini ve
Monte Carlo simulasyonunu icerir.
"""

import random
import statistics
import math

# ============================================================
# PARAMETRELER
# ============================================================
SERMAYE = 100_000              # Baslangic sermayesi (TL)
COIN_SAYISI = 50               # Kac farkli kripto alinacak
STOP_LOSS_PCT = 1.0            # %1 zarar
TAKE_PROFIT_PCT = 2.0          # %2 kar
KOMISYON_PCT = 0.1             # %0.1 komisyon (tek yon)
SPREAD_PCT = 0.05              # Ortalama spread maliyeti (tek yon)
SIMULASYON_SAYISI = 5_000      # Monte Carlo simulasyon sayisi
ROUND_SAYISI = 20              # Her simulasyonda kac tur trade yapilacak


def tek_trade_sonucu_analitik(sl_pct, tp_pct, komisyon_pct, spread_pct):
    """
    Analitik olasilik ile trade sonucu uretir.

    Simetrik random walk'ta, stop-loss'a ulasma olasiligi:
    P(SL) = TP / (SL + TP)
    P(TP) = SL / (SL + TP)
    """
    p_tp = sl_pct / (sl_pct + tp_pct)  # Take-profit olasiligi
    toplam_maliyet_pct = (komisyon_pct + spread_pct) * 2  # alis + satis

    if random.random() < p_tp:
        return (tp_pct - toplam_maliyet_pct) / 100  # Kar
    else:
        return (-sl_pct - toplam_maliyet_pct) / 100  # Zarar


def simulasyon_calistir():
    """Ana simulasyonu calistirir ve sonuclari raporlar."""

    sl = STOP_LOSS_PCT
    tp = TAKE_PROFIT_PCT
    kom = KOMISYON_PCT
    spr = SPREAD_PCT
    toplam_maliyet = (kom + spr) * 2  # %

    # Analitik olasliklar
    p_tp = sl / (sl + tp)
    p_sl = tp / (sl + tp)

    print("=" * 60)
    print("  KRIPTO TRADING STRATEJI ANALIZI")
    print("=" * 60)
    print()
    print(f"  Sermaye        : {SERMAYE:>12,.0f} TL")
    print(f"  Coin sayisi    : {COIN_SAYISI:>12}")
    print(f"  Her coine      : {SERMAYE / COIN_SAYISI:>12,.0f} TL")
    print(f"  Stop-Loss      :        -{sl}%")
    print(f"  Take-Profit    :        +{tp}%")
    print(f"  Komisyon       :    {kom:.2f}% (tek yon)")
    print(f"  Spread         :    {spr:.2f}% (tek yon)")
    print(f"  Toplam maliyet :    {toplam_maliyet:.2f}% (alis+satis)")
    print()

    # ----------------------------------------------------------
    # 1) MATEMATIKSEL ANALIZ
    # ----------------------------------------------------------
    print("-" * 60)
    print("  1) MATEMATIKSEL ANALIZ")
    print("-" * 60)
    print()
    print(f"  Take-Profit olasiligi (P_TP) = SL/(SL+TP) = {sl}/({sl}+{tp}) = {p_tp:.4f} (%{p_tp*100:.1f})")
    print(f"  Stop-Loss olasiligi   (P_SL) = TP/(SL+TP) = {tp}/({sl}+{tp}) = {p_sl:.4f} (%{p_sl*100:.1f})")
    print()

    # Komisyonsuz beklenen deger
    beklenen_komisyonsuz = p_tp * tp + p_sl * (-sl)
    print(f"  Komisyonsuz beklenen deger:")
    print(f"    E = P_TP x (+{tp}%) + P_SL x (-{sl}%)")
    print(f"    E = {p_tp:.4f} x (+{tp}%) + {p_sl:.4f} x (-{sl}%)")
    print(f"    E = +{p_tp * tp:.4f}% + ({p_sl * (-sl):.4f}%)")
    print(f"    E = %{beklenen_komisyonsuz:.4f}  (NOTR)")
    print()

    # Komisyonlu beklenen deger
    beklenen_komisyonlu = p_tp * (tp - toplam_maliyet) + p_sl * (-sl - toplam_maliyet)
    print(f"  Komisyonlu beklenen deger:")
    print(f"    E = P_TP x (+{tp}% - {toplam_maliyet}%) + P_SL x (-{sl}% - {toplam_maliyet}%)")
    print(f"    E = {p_tp:.4f} x (+{tp - toplam_maliyet:.2f}%) + {p_sl:.4f} x (-{sl + toplam_maliyet:.2f}%)")
    print(f"    E = %{beklenen_komisyonlu:.4f}  ", end="")
    if beklenen_komisyonlu < 0:
        print(">>> NEGATIF! <<<")
    else:
        print("(Pozitif)")
    print()
    print(f"  Her trade'de ortalama %{abs(beklenen_komisyonlu):.4f} KAYIP!")
    print(f"  50 coin x 20 tur = 1000 trade")
    print(f"  Toplam beklenen kayip: {1000 * beklenen_komisyonlu:.2f}% = {SERMAYE * 1000 * beklenen_komisyonlu / 100:,.0f} TL")
    print()

    # ----------------------------------------------------------
    # 2) Monte Carlo Simulasyonu
    # ----------------------------------------------------------
    print("-" * 60)
    print(f"  2) MONTE CARLO SIMULASYONU ({SIMULASYON_SAYISI:,} senaryo)")
    print("-" * 60)

    son_sermayeler = []

    for _ in range(SIMULASYON_SAYISI):
        sermaye = SERMAYE

        for _ in range(ROUND_SAYISI):
            parca = sermaye / COIN_SAYISI
            tur_kar = 0

            for _ in range(COIN_SAYISI):
                sonuc = tek_trade_sonucu_analitik(sl, tp, kom, spr)
                tur_kar += parca * sonuc

            sermaye += tur_kar
            if sermaye <= 0:
                sermaye = 0
                break

        son_sermayeler.append(sermaye)

    ort_sermaye = statistics.mean(son_sermayeler)
    medyan_sermaye = statistics.median(son_sermayeler)
    en_iyi = max(son_sermayeler)
    en_kotu = min(son_sermayeler)
    std_dev = statistics.stdev(son_sermayeler)

    zararda_olan = sum(1 for s in son_sermayeler if s < SERMAYE)
    zararda_oran = zararda_olan / SIMULASYON_SAYISI * 100

    ciddi_kayip = sum(1 for s in son_sermayeler if s < SERMAYE * 0.95)
    ciddi_kayip_oran = ciddi_kayip / SIMULASYON_SAYISI * 100

    print(f"  Ort. son sermaye    : {ort_sermaye:>12,.0f} TL ({(ort_sermaye / SERMAYE - 1) * 100:+.2f}%)")
    print(f"  Medyan son sermaye  : {medyan_sermaye:>12,.0f} TL ({(medyan_sermaye / SERMAYE - 1) * 100:+.2f}%)")
    print(f"  En iyi senaryo      : {en_iyi:>12,.0f} TL")
    print(f"  En kotu senaryo     : {en_kotu:>12,.0f} TL")
    print(f"  Std. sapma          : {std_dev:>12,.0f} TL")
    print()
    print(f"  Zararda biten       : %{zararda_oran:.1f}")
    print(f"  >%5 kayipla biten  : %{ciddi_kayip_oran:.1f}")
    print()

    # ----------------------------------------------------------
    # 3) Dagilim tablosu
    # ----------------------------------------------------------
    print("-" * 60)
    print("  3) SONUC DAGILIMI (20 tur sonrasi)")
    print("-" * 60)

    araliklar = [
        (0, 90000, "  0 - 90K  "),
        (90000, 94000, " 90K - 94K "),
        (94000, 96000, " 94K - 96K "),
        (96000, 98000, " 96K - 98K "),
        (98000, 100000, " 98K - 100K"),
        (100000, 102000, "100K - 102K"),
        (102000, 104000, "102K - 104K"),
        (104000, 106000, "104K - 106K"),
        (106000, 110000, "106K - 110K"),
        (110000, float('inf'), "110K+      "),
    ]

    for alt, ust, etiket in araliklar:
        sayi = sum(1 for s in son_sermayeler if alt <= s < ust)
        oran = sayi / SIMULASYON_SAYISI * 100
        bar = "#" * int(oran / 0.5)
        print(f"  {etiket} : {oran:5.1f}% |{bar}")

    print()

    # ----------------------------------------------------------
    # 4) Alternatif strateji karsilastirmasi
    # ----------------------------------------------------------
    print("-" * 60)
    print("  4) ALTERNATIF STRATEJILER (Matematiksel Karsilastirma)")
    print("-" * 60)
    print()

    stratejiler = [
        ("Senin strateji  (%1 SL / %2 TP)", 1.0, 2.0),
        ("Esit oran       (%2 SL / %2 TP)", 2.0, 2.0),
        ("1:3 oran        (%2 SL / %6 TP)", 2.0, 6.0),
        ("Dar             (%0.5 SL / %1 TP)", 0.5, 1.0),
        ("Genis           (%5 SL / %10 TP)", 5.0, 10.0),
    ]

    print(f"  {'Strateji':40s} | P(TP)  | Ort/trade | Komisyon etkisi")
    print(f"  {'-'*40}-+--------+-----------+-----------------")

    for isim, s, t in stratejiler:
        p = s / (s + t)
        e_saf = p * t + (1 - p) * (-s)
        e_kom = p * (t - toplam_maliyet) + (1 - p) * (-s - toplam_maliyet)
        print(f"  {isim:40s} | %{p*100:4.1f} |  %{e_saf:+.4f} |  %{e_kom:+.4f}")

    print()
    print(f"  NOT: Komisyonsuz tum stratejiler %0.0000 (NOTR) cikar.")
    print(f"       Fark yaratan komisyon ve spread'dir.")
    print(f"       Dar SL/TP'de komisyon etkisi DAHA BUYUK olur!")
    print()

    # ----------------------------------------------------------
    # SONUC VE ONERILER
    # ----------------------------------------------------------
    print("=" * 60)
    print("  SONUC VE ONERILER")
    print("=" * 60)
    print(f"""
  KISA CEVAP: Bu strateji MANTIKLI DEGIL. Ispatlar:

  1. MATEMATIK:
     - %1 SL / %2 TP'de kazanma olasiligi sadece %{p_tp*100:.1f}
     - %{p_sl*100:.1f} olasilikla kaybedersin, %{p_tp*100:.1f} olasilikla kazanirsin
     - Risk/Odul orani 1:2 gibi gorunse de OLASILIKLAR bunu dengeler
     - Simetrik random walk'ta SL/TP orani ne olursa olsun
       beklenen deger = 0 (komisyonsuz)

  2. KOMISYON TUZAGI:
     - Her trade %{toplam_maliyet:.2f} islem maliyeti
     - 50 coin x 20 tur = 1000 trade
     - Tahmini toplam kayip: ~{abs(SERMAYE * 1000 * beklenen_komisyonlu / 100):,.0f} TL

  3. DAHA IYI NE YAPABILIRSIN:
     - Teknik/temel analiz ile 5-10 coin sec (hepsini alma!)
     - Daha genis stop-loss (%3-5) kullan (whipsaw'dan kacin)
     - En az 1:3 risk-odul hedefle (%2 SL / %6+ TP)
     - Trailing stop-loss kullan (kari korur)
     - DCA (Dollar-Cost Averaging) stratejisi dusun
     - Islem sayisini azalt = komisyon azalir

  4. ALTIN KURAL:
     Islem basina maliyet > beklenen kar ise
     => Strateji UZUN VADEDE ZARAR EDER
     => Kazanan tek taraf BORSA'dir (komisyonlardan)
""")


if __name__ == "__main__":
    random.seed(42)
    simulasyon_calistir()
