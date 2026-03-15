"""
Arbitraj Motoru
===============
Fiyat farki tespit, maliyet hesaplama, islem karari.
Gercek islem YAPMAZ - sadece karar verir.
Islem yapmak bot_calistir.py'nin isi.
"""

import time
import json
import os
from datetime import datetime, date


# ============================================================
# FIRSAT TESPIT
# ============================================================

def fiyat_farki_hesapla(fiyat_a, fiyat_b):
    """
    Iki borsanin fiyat verilerinden arbitraj firsati hesaplar.

    ONEMLI: Gercek arbitrajda bid/ask kullanilir, last degil!
    - Alim: ask fiyatindan aliriz (satis emirlerinin en ucuzu)
    - Satim: bid fiyatindan satariz (alis emirlerinin en pahalisi)

    Args:
        fiyat_a: {"bid": float, "ask": float, "last": float}
        fiyat_b: {"bid": float, "ask": float, "last": float}

    Returns:
        {
            "firsat_var": bool,
            "yon": "A_AL_B_SAT" veya "B_AL_A_SAT",
            "alis_fiyat": float,    # ask fiyati (alacagimiz)
            "satis_fiyat": float,   # bid fiyati (satacagimiz)
            "fark_pct": float,      # brut fark %
            ...
        }
    """
    if not fiyat_a or not fiyat_b:
        return {"firsat_var": False, "sebep": "Fiyat verisi eksik"}

    # Senaryo 1: A'dan al (A'nin ask), B'de sat (B'nin bid)
    fark_1 = (fiyat_b["bid"] - fiyat_a["ask"]) / fiyat_a["ask"]

    # Senaryo 2: B'den al (B'nin ask), A'da sat (A'nin bid)
    fark_2 = (fiyat_a["bid"] - fiyat_b["ask"]) / fiyat_b["ask"]

    if fark_1 >= fark_2:
        return {
            "firsat_var": True,
            "yon": "A_AL_B_SAT",
            "alis_fiyat": fiyat_a["ask"],
            "satis_fiyat": fiyat_b["bid"],
            "fark_pct": fark_1,
            "fark_tl": fiyat_b["bid"] - fiyat_a["ask"],
        }
    else:
        return {
            "firsat_var": True,
            "yon": "B_AL_A_SAT",
            "alis_fiyat": fiyat_b["ask"],
            "satis_fiyat": fiyat_a["bid"],
            "fark_pct": fark_2,
            "fark_tl": fiyat_a["bid"] - fiyat_b["ask"],
        }


def maliyet_hesapla(alis_komisyon, satis_komisyon, slippage_pct):
    """Toplam islem maliyetini (%) hesaplar."""
    return alis_komisyon + satis_komisyon + slippage_pct * 2


def islem_karari(fark_pct, maliyet_pct, min_kar_esigi, acil_cikis_esigi):
    """
    Islem yapilmali mi karar verir.

    Returns:
        {"islem_yap": bool, "sebep": str, "net_kar_pct": float}
    """
    net_kar = fark_pct - maliyet_pct

    # Anormal buyuk fark = tehlike isareti
    if fark_pct > acil_cikis_esigi:
        return {
            "islem_yap": False,
            "sebep": f"ANORMAL FARK (%{fark_pct*100:.2f}) - muhtemelen borsa sorunu",
            "net_kar_pct": net_kar,
        }

    # Negatif fark = islem mantikli degil
    if fark_pct <= 0:
        return {
            "islem_yap": False,
            "sebep": "Fark negatif veya sifir",
            "net_kar_pct": net_kar,
        }

    # Maliyet altinda = zarar
    if net_kar < 0:
        return {
            "islem_yap": False,
            "sebep": f"Maliyet altinda (fark %{fark_pct*100:.3f} < maliyet %{maliyet_pct*100:.3f})",
            "net_kar_pct": net_kar,
        }

    # Minimum kar esiginin altinda = riske degmez
    if net_kar < min_kar_esigi:
        return {
            "islem_yap": False,
            "sebep": f"Kar cok dusuk (%{net_kar*100:.3f} < esik %{min_kar_esigi*100:.3f})",
            "net_kar_pct": net_kar,
        }

    return {
        "islem_yap": True,
        "sebep": f"FIRSAT: net kar %{net_kar*100:.3f}",
        "net_kar_pct": net_kar,
    }


def islem_miktari_hesapla(bakiye_tl, bakiye_usdt, alis_fiyat, satis_fiyat,
                           yon, islem_orani, min_usdt, max_usdt):
    """
    Islem miktarini bakiyelere gore hesaplar.

    Args:
        bakiye_tl: Alis borsasindaki TL bakiye
        bakiye_usdt: Satis borsasindaki USDT bakiye
        alis_fiyat: Alim fiyati (ask)
        satis_fiyat: Satim fiyati (bid)
        yon: "alis" veya "satis"
        islem_orani: Bakiyenin yuzde kaci kullanilir
        min_usdt: Minimum USDT
        max_usdt: Maksimum USDT

    Returns:
        {"miktar_usdt": float, "miktar_tl": float, "uygun": bool, "sebep": str}
    """
    # Alim tarafinda: TL ile USDT alacagiz
    max_alis_usdt = (bakiye_tl / alis_fiyat) * islem_orani

    # Satim tarafinda: USDT satacagiz
    max_satis_usdt = bakiye_usdt * islem_orani

    # Ikisinin minimumu
    miktar = min(max_alis_usdt, max_satis_usdt)

    # Limitlere uygula
    miktar = min(miktar, max_usdt)

    if miktar < min_usdt:
        return {
            "miktar_usdt": 0,
            "miktar_tl": 0,
            "uygun": False,
            "sebep": f"Yetersiz bakiye (hesaplanan: {miktar:.2f} USDT < min: {min_usdt} USDT)",
        }

    return {
        "miktar_usdt": round(miktar, 2),
        "miktar_tl": round(miktar * alis_fiyat, 2),
        "uygun": True,
        "sebep": "OK",
    }


# ============================================================
# GUNLUK LIMIT TAKIBI
# ============================================================

class GunlukTakip:
    """Gunluk islem ve zarar limitlerini takip eder."""

    def __init__(self, max_islem, max_zarar_tl):
        self.max_islem = max_islem
        self.max_zarar_tl = max_zarar_tl
        self.bugun = date.today()
        self.islem_sayisi = 0
        self.toplam_kar_zarar = 0.0
        self.islemler = []

    def _gun_kontrolu(self):
        """Yeni gun mu kontrol et, oyle ise sifirla."""
        if date.today() != self.bugun:
            self.bugun = date.today()
            self.islem_sayisi = 0
            self.toplam_kar_zarar = 0.0
            self.islemler = []

    def islem_yapilabilir_mi(self):
        """Gunluk limitler uygun mu?"""
        self._gun_kontrolu()

        if self.islem_sayisi >= self.max_islem:
            return False, f"Gunluk islem limiti doldu ({self.max_islem})"

        if self.toplam_kar_zarar <= -self.max_zarar_tl:
            return False, f"Gunluk zarar limiti asildi ({self.toplam_kar_zarar:.2f} TL)"

        return True, "OK"

    def islem_kaydet(self, kar_zarar_tl, detay=None):
        """Yapilan islemi kaydet."""
        self._gun_kontrolu()
        self.islem_sayisi += 1
        self.toplam_kar_zarar += kar_zarar_tl
        self.islemler.append({
            "zaman": datetime.now().isoformat(),
            "kar_zarar_tl": kar_zarar_tl,
            "toplam": self.toplam_kar_zarar,
            "detay": detay,
        })

    def ozet(self):
        """Gunluk ozet."""
        self._gun_kontrolu()
        return {
            "tarih": str(self.bugun),
            "islem_sayisi": self.islem_sayisi,
            "kalan_islem": self.max_islem - self.islem_sayisi,
            "toplam_kar_zarar": self.toplam_kar_zarar,
            "kalan_zarar_limiti": self.max_zarar_tl + self.toplam_kar_zarar,
        }


# ============================================================
# ISLEM GECMISI
# ============================================================

def islem_logla(dosya_yolu, islem_verisi):
    """Islemi JSON dosyasina ekler."""
    gecmis = []
    if os.path.exists(dosya_yolu):
        try:
            with open(dosya_yolu, "r") as f:
                gecmis = json.load(f)
        except (json.JSONDecodeError, IOError):
            gecmis = []

    gecmis.append(islem_verisi)

    with open(dosya_yolu, "w") as f:
        json.dump(gecmis, f, indent=2, ensure_ascii=False)


def islem_gecmisi_oku(dosya_yolu):
    """Islem gecmisini okur."""
    if not os.path.exists(dosya_yolu):
        return []
    try:
        with open(dosya_yolu, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  ARBITRAJ MOTORU TESTI")
    print("=" * 50)
    print()

    # Ornek fiyatlar
    btcturk = {"bid": 38.45, "ask": 38.50, "last": 38.47}
    binance = {"bid": 38.62, "ask": 38.65, "last": 38.63}

    print(f"BtcTurk: bid={btcturk['bid']} ask={btcturk['ask']}")
    print(f"Binance: bid={binance['bid']} ask={binance['ask']}")
    print()

    # Fark hesapla
    fark = fiyat_farki_hesapla(btcturk, binance)
    print(f"Yon      : {fark['yon']}")
    print(f"Alis     : {fark['alis_fiyat']:.4f} TL")
    print(f"Satis    : {fark['satis_fiyat']:.4f} TL")
    print(f"Fark     : %{fark['fark_pct']*100:.3f}")
    print()

    # Maliyet
    maliyet = maliyet_hesapla(0.0020, 0.0010, 0.0003)
    print(f"Maliyet  : %{maliyet*100:.3f}")
    print()

    # Karar
    karar = islem_karari(fark["fark_pct"], maliyet, 0.003, 0.05)
    print(f"Islem yap: {karar['islem_yap']}")
    print(f"Sebep    : {karar['sebep']}")
    print(f"Net kar  : %{karar['net_kar_pct']*100:.3f}")
    print()

    # Miktar
    miktar = islem_miktari_hesapla(
        bakiye_tl=500, bakiye_usdt=13,
        alis_fiyat=fark["alis_fiyat"],
        satis_fiyat=fark["satis_fiyat"],
        yon="alis", islem_orani=0.30,
        min_usdt=10, max_usdt=50
    )
    print(f"Miktar   : {miktar['miktar_usdt']} USDT ({miktar['miktar_tl']} TL)")
    print(f"Uygun    : {miktar['uygun']}")
    print()

    # Gunluk takip
    takip = GunlukTakip(max_islem=20, max_zarar_tl=15)
    ok, msg = takip.islem_yapilabilir_mi()
    print(f"Islem yapilabilir: {ok} ({msg})")
    takip.islem_kaydet(1.50, {"test": True})
    takip.islem_kaydet(-0.30, {"test": True})
    print(f"Gunluk ozet: {takip.ozet()}")
