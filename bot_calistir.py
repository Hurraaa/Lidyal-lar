"""
Stablecoin Arbitraj Botu
========================
Ana calistirici. Fiyatlari izler, firsat bulur, islem yapar.

Kullanim:
  python3 bot_calistir.py              # Paper trading (varsayilan)
  python3 bot_calistir.py --canli      # GERCEK islem (dikkat!)
  python3 bot_calistir.py --test       # Tek seferlik baglanti testi
  python3 bot_calistir.py --durum      # Gunluk ozet ve bakiyeler
"""

import sys
import time
import json
import os
import signal
from datetime import datetime

import bot_config as cfg
from borsa_api import BtcTurkAPI, BinanceAPI
from arbitraj_motor import (
    fiyat_farki_hesapla,
    maliyet_hesapla,
    islem_karari,
    islem_miktari_hesapla,
    GunlukTakip,
    islem_logla,
)


# ============================================================
# LOGLAMA
# ============================================================

def log(mesaj, seviye="INFO"):
    """Konsola ve dosyaya log yazar."""
    zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    satir = f"[{zaman}] [{seviye}] {mesaj}"
    print(satir)

    try:
        with open(cfg.LOG_DOSYASI, "a") as f:
            f.write(satir + "\n")
    except IOError:
        pass


def telegram_bildir(mesaj):
    """Telegram bildirimi gonderir (aktifse)."""
    if not cfg.TELEGRAM_AKTIF or not cfg.TELEGRAM_BOT_TOKEN:
        return

    try:
        import ssl
        from urllib.request import urlopen, Request
        url = (f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}"
               f"/sendMessage?chat_id={cfg.TELEGRAM_CHAT_ID}"
               f"&text={mesaj}&parse_mode=HTML")
        ctx = ssl.create_default_context()
        req = Request(url, headers={"User-Agent": "ArbitrajBot/1.0"})
        urlopen(req, timeout=5, context=ctx)
    except Exception:
        pass


# ============================================================
# PAPER TRADING SIMULASYONU
# ============================================================

class PaperBakiye:
    """Paper trading icin sanal bakiye yonetimi."""

    def __init__(self, tl_a, usdt_a, tl_b, usdt_b):
        """
        Args:
            tl_a: Borsa A (BtcTurk) TL bakiye
            usdt_a: Borsa A USDT bakiye
            tl_b: Borsa B (Binance) TL bakiye
            usdt_b: Borsa B USDT bakiye
        """
        self.bakiye = {
            "btcturk": {"TRY": tl_a, "USDT": usdt_a},
            "binance": {"TRY": tl_b, "USDT": usdt_b},
        }
        self.baslangic_toplam = tl_a + tl_b  # Baslangic degeri (TL cinsinden)

    def al(self, borsa, miktar_usdt, fiyat, komisyon_pct):
        """USDT al (TL ile)."""
        maliyet_tl = miktar_usdt * fiyat
        komisyon_tl = maliyet_tl * komisyon_pct

        if self.bakiye[borsa]["TRY"] < maliyet_tl + komisyon_tl:
            return False, "Yetersiz TL bakiye"

        self.bakiye[borsa]["TRY"] -= (maliyet_tl + komisyon_tl)
        self.bakiye[borsa]["USDT"] += miktar_usdt
        return True, komisyon_tl

    def sat(self, borsa, miktar_usdt, fiyat, komisyon_pct):
        """USDT sat (TL al)."""
        if self.bakiye[borsa]["USDT"] < miktar_usdt:
            return False, "Yetersiz USDT bakiye"

        gelir_tl = miktar_usdt * fiyat
        komisyon_tl = gelir_tl * komisyon_pct

        self.bakiye[borsa]["USDT"] -= miktar_usdt
        self.bakiye[borsa]["TRY"] += (gelir_tl - komisyon_tl)
        return True, komisyon_tl

    def toplam_deger(self, usdt_fiyat):
        """Toplam portfoy degeri (TL)."""
        toplam = 0
        for borsa in self.bakiye:
            toplam += self.bakiye[borsa]["TRY"]
            toplam += self.bakiye[borsa]["USDT"] * usdt_fiyat
        return toplam

    def ozet(self, usdt_fiyat):
        """Bakiye ozeti."""
        toplam = self.toplam_deger(usdt_fiyat)
        kar = toplam - self.baslangic_toplam
        return {
            "btcturk_tl": self.bakiye["btcturk"]["TRY"],
            "btcturk_usdt": self.bakiye["btcturk"]["USDT"],
            "binance_tl": self.bakiye["binance"]["TRY"],
            "binance_usdt": self.bakiye["binance"]["USDT"],
            "toplam_tl": toplam,
            "kar_zarar": kar,
            "kar_pct": (kar / self.baslangic_toplam * 100) if self.baslangic_toplam > 0 else 0,
        }


# ============================================================
# ANA BOT DONGUSU
# ============================================================

class ArbitrajBot:
    """Ana arbitraj botu."""

    def __init__(self, paper_trading=True):
        self.paper_trading = paper_trading
        self.calisiyor = True

        # Borsa API'leri
        btcturk_cfg = cfg.BORSALAR["btcturk"]
        binance_cfg = cfg.BORSALAR["binance"]

        self.btcturk = BtcTurkAPI(
            btcturk_cfg["api_key"],
            btcturk_cfg["api_secret"],
        )
        self.binance = BinanceAPI(
            binance_cfg["api_key"],
            binance_cfg["api_secret"],
        )

        # Gunluk takip
        self.gunluk = GunlukTakip(
            max_islem=cfg.GUNLUK_MAX_ISLEM,
            max_zarar_tl=cfg.GUNLUK_MAX_ZARAR_TL,
        )

        # Paper trading bakiyeleri
        if paper_trading:
            usdt_tahmini = 38.50  # Yaklasiik USDT/TRY
            borsa_basi = cfg.BORSA_BASI_SERMAYE_TL
            self.paper = PaperBakiye(
                tl_a=borsa_basi / 2,
                usdt_a=(borsa_basi / 2) / usdt_tahmini,
                tl_b=borsa_basi / 2,
                usdt_b=(borsa_basi / 2) / usdt_tahmini,
            )

        # Istatistikler
        self.toplam_islem = 0
        self.toplam_kar = 0.0
        self.ust_uste_hata = 0
        self.son_fiyatlar = {}

    def durdur(self):
        """Botu durdur."""
        self.calisiyor = False

    def fiyatlari_al(self):
        """Her iki borsadan fiyat ceker."""
        fiyat_a = self.btcturk.fiyat_al()
        fiyat_b = self.binance.fiyat_al()
        return fiyat_a, fiyat_b

    def bakiyeleri_al(self):
        """Bakiyeleri alir (paper veya gercek)."""
        if self.paper_trading:
            usdt_fiyat = 38.50
            if self.son_fiyatlar.get("btcturk"):
                usdt_fiyat = self.son_fiyatlar["btcturk"]["last"]
            return self.paper.ozet(usdt_fiyat)

        # Gercek bakiyeler
        b_a = self.btcturk.bakiye_al()
        b_b = self.binance.bakiye_al()
        if b_a and b_b:
            return {
                "btcturk_tl": b_a["TRY"],
                "btcturk_usdt": b_a["USDT"],
                "binance_tl": b_b["TRY"],
                "binance_usdt": b_b["USDT"],
            }
        return None

    def tek_dongü(self):
        """Bot'un tek bir dongusu: fiyat al, analiz et, gerekirse islem yap."""

        # 1. Fiyatlari al
        fiyat_a, fiyat_b = self.fiyatlari_al()

        if not fiyat_a or not fiyat_b:
            self.ust_uste_hata += 1
            hata_msg = (f"Fiyat alinamadi "
                        f"(BtcTurk: {'OK' if fiyat_a else 'HATA'}, "
                        f"Binance: {'OK' if fiyat_b else 'HATA'}) "
                        f"[{self.ust_uste_hata}/{cfg.MAX_API_HATASI_UST_USTE}]")
            log(hata_msg, "HATA")

            if self.ust_uste_hata >= cfg.MAX_API_HATASI_UST_USTE:
                log("Cok fazla ust uste hata! Bot durduruluyor.", "KRITIK")
                telegram_bildir("KRITIK: Bot durdu - API hatalari!")
                self.durdur()
            return

        self.ust_uste_hata = 0
        self.son_fiyatlar = {"btcturk": fiyat_a, "binance": fiyat_b}

        # 2. Fark hesapla
        fark = fiyat_farki_hesapla(fiyat_a, fiyat_b)

        if not fark["firsat_var"]:
            return

        # 3. Maliyet hesapla
        btcturk_cfg = cfg.BORSALAR["btcturk"]
        binance_cfg = cfg.BORSALAR["binance"]

        if fark["yon"] == "A_AL_B_SAT":
            alis_kom = btcturk_cfg["komisyon_taker"]
            satis_kom = binance_cfg["komisyon_taker"]
            alis_borsa = "btcturk"
            satis_borsa = "binance"
        else:
            alis_kom = binance_cfg["komisyon_taker"]
            satis_kom = btcturk_cfg["komisyon_taker"]
            alis_borsa = "binance"
            satis_borsa = "btcturk"

        maliyet = maliyet_hesapla(alis_kom, satis_kom, cfg.SLIPPAGE_PCT)

        # 4. Islem karari
        karar = islem_karari(
            fark["fark_pct"], maliyet,
            cfg.MIN_KAR_ESIGI_PCT, cfg.ACIL_CIKIS_FARK_PCT,
        )

        # Durum logu (her 10 dongude bir veya firsat varsa)
        fark_str = f"BtcTurk: {fiyat_a['bid']:.4f}/{fiyat_a['ask']:.4f} | "
        fark_str += f"Binance: {fiyat_b['bid']:.4f}/{fiyat_b['ask']:.4f} | "
        fark_str += f"Fark: %{fark['fark_pct']*100:.3f} | Net: %{karar['net_kar_pct']*100:.3f}"

        if not karar["islem_yap"]:
            # Sadece onemli red sebeplerini logla (spam onleme)
            if fark["fark_pct"] > 0.001:  # %0.1'den buyuk farklar
                log(f"IZLE  | {fark_str}")
            return

        # 5. Gunluk limit kontrolu
        limit_ok, limit_msg = self.gunluk.islem_yapilabilir_mi()
        if not limit_ok:
            log(f"LIMIT | {limit_msg}", "UYARI")
            return

        # 6. Bakiye ve miktar hesapla
        if self.paper_trading:
            bakiye_tl = self.paper.bakiye[alis_borsa]["TRY"]
            bakiye_usdt = self.paper.bakiye[satis_borsa]["USDT"]
        else:
            bakiyeler = self.bakiyeleri_al()
            if not bakiyeler:
                log("Bakiye alinamadi!", "HATA")
                return
            bakiye_tl = bakiyeler[f"{alis_borsa}_tl"]
            bakiye_usdt = bakiyeler[f"{satis_borsa}_usdt"]

        miktar = islem_miktari_hesapla(
            bakiye_tl=bakiye_tl,
            bakiye_usdt=bakiye_usdt,
            alis_fiyat=fark["alis_fiyat"],
            satis_fiyat=fark["satis_fiyat"],
            yon="alis",
            islem_orani=cfg.ISLEM_BAKIYE_ORANI,
            min_usdt=cfg.MIN_ISLEM_USDT,
            max_usdt=cfg.MAX_ISLEM_USDT,
        )

        if not miktar["uygun"]:
            log(f"BAKIYE | {miktar['sebep']}", "UYARI")
            return

        # 7. ISLEMI GERCEKLESTIR!
        log(f"{'PAPER' if self.paper_trading else 'GERCEK'} ISLEM BASLIYOR!", "ISLEM")
        log(f"  Yon     : {alis_borsa} AL -> {satis_borsa} SAT", "ISLEM")
        log(f"  Miktar  : {miktar['miktar_usdt']} USDT ({miktar['miktar_tl']} TL)", "ISLEM")
        log(f"  Alis    : {fark['alis_fiyat']:.4f} TL", "ISLEM")
        log(f"  Satis   : {fark['satis_fiyat']:.4f} TL", "ISLEM")
        log(f"  Fark    : %{fark['fark_pct']*100:.3f}", "ISLEM")
        log(f"  Net kar : %{karar['net_kar_pct']*100:.3f}", "ISLEM")

        if self.paper_trading:
            # Paper trading: sanal bakiyeleri guncelle
            ok_al, kom_al = self.paper.al(
                alis_borsa, miktar["miktar_usdt"],
                fark["alis_fiyat"], alis_kom,
            )
            ok_sat, kom_sat = self.paper.sat(
                satis_borsa, miktar["miktar_usdt"],
                fark["satis_fiyat"], satis_kom,
            )

            if ok_al and ok_sat:
                brut_kar = miktar["miktar_usdt"] * (fark["satis_fiyat"] - fark["alis_fiyat"])
                net_kar = brut_kar - kom_al - kom_sat
                self.toplam_islem += 1
                self.toplam_kar += net_kar
                self.gunluk.islem_kaydet(net_kar)

                ozet = self.paper.ozet(fiyat_a["last"])
                log(f"  BASARILI! Net kar: {net_kar:.2f} TL | "
                    f"Toplam: {ozet['kar_zarar']:.2f} TL ({ozet['kar_pct']:+.2f}%)", "ISLEM")

                islem_kaydi = {
                    "zaman": datetime.now().isoformat(),
                    "mod": "paper",
                    "alis_borsa": alis_borsa,
                    "satis_borsa": satis_borsa,
                    "miktar_usdt": miktar["miktar_usdt"],
                    "alis_fiyat": fark["alis_fiyat"],
                    "satis_fiyat": fark["satis_fiyat"],
                    "fark_pct": fark["fark_pct"],
                    "net_kar_tl": net_kar,
                    "portfoy_toplam": ozet["toplam_tl"],
                }
                islem_logla(cfg.LOG_ISLEMLER, islem_kaydi)
                telegram_bildir(
                    f"ISLEM: {alis_borsa}->{ satis_borsa} "
                    f"{miktar['miktar_usdt']} USDT | "
                    f"Kar: {net_kar:.2f} TL"
                )
            else:
                log(f"  BASARISIZ: AL={ok_al}, SAT={ok_sat}", "HATA")

        else:
            # GERCEK ISLEM
            log("  Emirler gonderiliyor...", "ISLEM")

            # Alis emri
            if alis_borsa == "btcturk":
                sonuc_al = self.btcturk.emir_gonder(
                    "buy", miktar["miktar_usdt"], fark["alis_fiyat"],
                )
            else:
                sonuc_al = self.binance.emir_gonder(
                    "BUY", miktar["miktar_usdt"], fark["alis_fiyat"],
                )

            # Satis emri
            if satis_borsa == "btcturk":
                sonuc_sat = self.btcturk.emir_gonder(
                    "sell", miktar["miktar_usdt"], fark["satis_fiyat"],
                )
            else:
                sonuc_sat = self.binance.emir_gonder(
                    "SELL", miktar["miktar_usdt"], fark["satis_fiyat"],
                )

            # Sonuclari logla
            if sonuc_al["success"] and sonuc_sat["success"]:
                brut_kar = miktar["miktar_usdt"] * (fark["satis_fiyat"] - fark["alis_fiyat"])
                tahmini_maliyet = miktar["miktar_tl"] * maliyet
                net_kar = brut_kar - tahmini_maliyet
                self.toplam_islem += 1
                self.toplam_kar += net_kar
                self.gunluk.islem_kaydet(net_kar)

                log(f"  BASARILI! Tahmini net kar: {net_kar:.2f} TL", "ISLEM")
                log(f"  Alis order: {sonuc_al['order_id']}", "ISLEM")
                log(f"  Satis order: {sonuc_sat['order_id']}", "ISLEM")

                islem_kaydi = {
                    "zaman": datetime.now().isoformat(),
                    "mod": "canli",
                    "alis_borsa": alis_borsa,
                    "satis_borsa": satis_borsa,
                    "miktar_usdt": miktar["miktar_usdt"],
                    "alis_fiyat": fark["alis_fiyat"],
                    "satis_fiyat": fark["satis_fiyat"],
                    "fark_pct": fark["fark_pct"],
                    "net_kar_tl": net_kar,
                    "alis_order": sonuc_al["order_id"],
                    "satis_order": sonuc_sat["order_id"],
                }
                islem_logla(cfg.LOG_ISLEMLER, islem_kaydi)
                telegram_bildir(
                    f"GERCEK ISLEM: {alis_borsa}->{satis_borsa} "
                    f"{miktar['miktar_usdt']} USDT | "
                    f"Kar: ~{net_kar:.2f} TL"
                )
            else:
                log(f"  EMIR HATASI!", "HATA")
                log(f"  Alis: {sonuc_al}", "HATA")
                log(f"  Satis: {sonuc_sat}", "HATA")
                telegram_bildir(f"HATA: Emir gonderilemedi! {sonuc_al} / {sonuc_sat}")

    def calistir(self):
        """Ana bot dongusunu baslatir."""
        mod = "PAPER TRADING" if self.paper_trading else "CANLI ISLEM"

        print()
        print("=" * 60)
        print(f"  STABLECOIN ARBITRAJ BOTU - {mod}")
        print("=" * 60)
        print()
        print(f"  Sermaye        : {cfg.TOPLAM_SERMAYE_TL:,.0f} TL")
        print(f"  Borsalar       : BtcTurk + Binance")
        print(f"  Min kar esigi  : %{cfg.MIN_KAR_ESIGI_PCT*100:.2f}")
        print(f"  Max islem      : {cfg.MAX_ISLEM_USDT} USDT")
        print(f"  Kontrol araligi: {cfg.FIYAT_KONTROL_ARALIGI} saniye")
        islem_limit = f"{cfg.GUNLUK_MAX_ISLEM} islem" if cfg.GUNLUK_MAX_ISLEM else "limitsiz"
        print(f"  Gunluk limit   : {islem_limit} / {cfg.GUNLUK_MAX_ZARAR_TL} TL zarar")
        print()

        if not self.paper_trading:
            print("  !!! DIKKAT: GERCEK PARA ILE ISLEM YAPILACAK !!!")
            print("  !!! Durdurmak icin Ctrl+C !!!")
            print()
            # 5 saniye bekle, kullanici iptal edebilsin
            for i in range(5, 0, -1):
                print(f"  Baslamaya {i} saniye...", end="\r")
                time.sleep(1)
            print()

        log(f"Bot baslatildi ({mod})")
        telegram_bildir(f"Bot baslatildi ({mod}) - {cfg.TOPLAM_SERMAYE_TL} TL")

        dongu_sayisi = 0
        while self.calisiyor:
            try:
                self.tek_dongü()
                dongu_sayisi += 1

                # Her 30 dongude durum raporu
                if dongu_sayisi % 30 == 0:
                    ozet = self.gunluk.ozet()
                    if self.paper_trading and hasattr(self, 'paper'):
                        usdt_fiyat = 38.50
                        if self.son_fiyatlar.get("btcturk"):
                            usdt_fiyat = self.son_fiyatlar["btcturk"]["last"]
                        p = self.paper.ozet(usdt_fiyat)
                        log(f"DURUM | Portfoy: {p['toplam_tl']:.0f} TL ({p['kar_pct']:+.2f}%) | "
                            f"Gun: {ozet['islem_sayisi']} islem, {ozet['toplam_kar_zarar']:.2f} TL | "
                            f"Toplam: {self.toplam_islem} islem, {self.toplam_kar:.2f} TL")
                    else:
                        log(f"DURUM | Gun: {ozet['islem_sayisi']} islem, "
                            f"{ozet['toplam_kar_zarar']:.2f} TL")

                time.sleep(cfg.FIYAT_KONTROL_ARALIGI)

            except KeyboardInterrupt:
                log("Ctrl+C ile durduruldu.")
                break
            except Exception as e:
                log(f"Beklenmeyen hata: {e}", "HATA")
                time.sleep(cfg.API_HATA_BEKLEME_SN)

        # Kapanirken ozet
        self._kapanis_raporu()

    def _kapanis_raporu(self):
        """Bot kapanirken son durum raporu."""
        print()
        print("=" * 60)
        print("  BOT KAPANIYOR - SON DURUM")
        print("=" * 60)
        print()
        print(f"  Toplam islem  : {self.toplam_islem}")
        print(f"  Toplam kar    : {self.toplam_kar:.2f} TL")

        if self.paper_trading and hasattr(self, 'paper'):
            usdt_fiyat = 38.50
            if self.son_fiyatlar.get("btcturk"):
                usdt_fiyat = self.son_fiyatlar["btcturk"]["last"]
            ozet = self.paper.ozet(usdt_fiyat)
            print()
            print(f"  PAPER BAKIYELER:")
            print(f"    BtcTurk : {ozet['btcturk_tl']:.2f} TL + {ozet['btcturk_usdt']:.2f} USDT")
            print(f"    Binance : {ozet['binance_tl']:.2f} TL + {ozet['binance_usdt']:.2f} USDT")
            print(f"    Toplam  : {ozet['toplam_tl']:.2f} TL ({ozet['kar_pct']:+.2f}%)")

        gunluk = self.gunluk.ozet()
        print()
        print(f"  GUNLUK OZET ({gunluk['tarih']}):")
        print(f"    Islem sayisi : {gunluk['islem_sayisi']}")
        print(f"    Kar/zarar    : {gunluk['toplam_kar_zarar']:.2f} TL")
        print()

        log("Bot durduruldu.")
        telegram_bildir(
            f"Bot durduruldu. "
            f"Toplam: {self.toplam_islem} islem, {self.toplam_kar:.2f} TL"
        )


# ============================================================
# BAGLANTI TESTI
# ============================================================

def baglanti_testi():
    """Tek seferlik baglanti ve fiyat testi."""
    print()
    print("=" * 60)
    print("  BAGLANTI TESTI")
    print("=" * 60)
    print()

    btcturk = BtcTurkAPI()
    binance = BinanceAPI()

    print("1) FIYAT TESTI (API key gerektirmez)")
    print("-" * 40)

    print("  BtcTurk USDT/TRY...", end=" ")
    f_a = btcturk.fiyat_al()
    if f_a:
        print(f"OK")
        print(f"    Bid: {f_a['bid']:.4f} | Ask: {f_a['ask']:.4f} | "
              f"Last: {f_a['last']:.4f}")
        print(f"    Spread: {f_a['ask']-f_a['bid']:.4f} TL "
              f"(%{(f_a['ask']-f_a['bid'])/f_a['bid']*100:.3f})")
    else:
        print("BASARISIZ")
    print()

    print("  Binance USDT/TRY...", end=" ")
    f_b = binance.fiyat_al()
    if f_b:
        print(f"OK")
        print(f"    Bid: {f_b['bid']:.4f} | Ask: {f_b['ask']:.4f} | "
              f"Last: {f_b['last']:.4f}")
        print(f"    Spread: {f_b['ask']-f_b['bid']:.4f} TL "
              f"(%{(f_b['ask']-f_b['bid'])/f_b['bid']*100:.3f})")
    else:
        print("BASARISIZ")
    print()

    if f_a and f_b:
        print("2) ARBITRAJ FARKI")
        print("-" * 40)
        fark = fiyat_farki_hesapla(f_a, f_b)
        print(f"  Yon          : {fark['yon']}")
        print(f"  Alis (ask)   : {fark['alis_fiyat']:.4f} TL")
        print(f"  Satis (bid)  : {fark['satis_fiyat']:.4f} TL")
        print(f"  Brut fark    : %{fark['fark_pct']*100:.3f}")

        maliyet = maliyet_hesapla(0.0020, 0.0010, cfg.SLIPPAGE_PCT)
        karar = islem_karari(fark["fark_pct"], maliyet,
                             cfg.MIN_KAR_ESIGI_PCT, cfg.ACIL_CIKIS_FARK_PCT)
        print(f"  Maliyet      : %{maliyet*100:.3f}")
        print(f"  Net kar      : %{karar['net_kar_pct']*100:.3f}")
        print(f"  Islem yap    : {'EVET' if karar['islem_yap'] else 'HAYIR'}")
        print(f"  Sebep        : {karar['sebep']}")

        if karar["islem_yap"]:
            miktar = islem_miktari_hesapla(
                bakiye_tl=cfg.BORSA_BASI_SERMAYE_TL / 2,
                bakiye_usdt=(cfg.BORSA_BASI_SERMAYE_TL / 2) / f_a["last"],
                alis_fiyat=fark["alis_fiyat"],
                satis_fiyat=fark["satis_fiyat"],
                yon="alis",
                islem_orani=cfg.ISLEM_BAKIYE_ORANI,
                min_usdt=cfg.MIN_ISLEM_USDT,
                max_usdt=cfg.MAX_ISLEM_USDT,
            )
            if miktar["uygun"]:
                tahmini_kar = miktar["miktar_usdt"] * karar["net_kar_pct"] * f_a["last"]
                print(f"  Miktar       : {miktar['miktar_usdt']} USDT")
                print(f"  Tahmini kar  : {tahmini_kar:.2f} TL")
    print()

    # API key testi (varsa)
    btcturk_cfg = cfg.BORSALAR["btcturk"]
    binance_cfg = cfg.BORSALAR["binance"]

    if btcturk_cfg["api_key"] or binance_cfg["api_key"]:
        print("3) BAKIYE TESTI (API key gerekir)")
        print("-" * 40)

        if btcturk_cfg["api_key"]:
            btcturk_auth = BtcTurkAPI(btcturk_cfg["api_key"], btcturk_cfg["api_secret"])
            print("  BtcTurk bakiye...", end=" ")
            b = btcturk_auth.bakiye_al()
            if b:
                print(f"OK → {b['TRY']:.2f} TL + {b['USDT']:.2f} USDT")
            else:
                print("BASARISIZ (API key hatali olabilir)")

        if binance_cfg["api_key"]:
            binance_auth = BinanceAPI(binance_cfg["api_key"], binance_cfg["api_secret"])
            print("  Binance bakiye...", end=" ")
            b = binance_auth.bakiye_al()
            if b:
                print(f"OK → {b['TRY']:.2f} TL + {b['USDT']:.2f} USDT")
            else:
                print("BASARISIZ (API key hatali olabilir)")
        print()
    else:
        print("3) BAKIYE TESTI - ATLANDI (API key tanimlanmamis)")
        print("   .env dosyasina veya bot_config.py'ye API key'leri ekle.")
        print()

    print("Test tamamlandi.")


# ============================================================
# CLI
# ============================================================

def main():
    """Komut satiri giris noktasi."""
    args = sys.argv[1:]

    if "--test" in args:
        baglanti_testi()
        return

    if "--durum" in args:
        # Islem gecmisini goster
        from arbitraj_motor import islem_gecmisi_oku
        gecmis = islem_gecmisi_oku(cfg.LOG_ISLEMLER)
        print(f"\nToplam {len(gecmis)} islem kaydi.")
        if gecmis:
            toplam_kar = sum(i.get("net_kar_tl", 0) for i in gecmis)
            print(f"Toplam kar/zarar: {toplam_kar:.2f} TL")
            print(f"\nSon 5 islem:")
            for i in gecmis[-5:]:
                print(f"  {i.get('zaman', '?')} | "
                      f"{i.get('alis_borsa', '?')}->{i.get('satis_borsa', '?')} | "
                      f"{i.get('miktar_usdt', 0)} USDT | "
                      f"Kar: {i.get('net_kar_tl', 0):.2f} TL")
        return

    paper = "--canli" not in args

    if not paper:
        # Canli mod icin API key kontrolu
        btcturk_key = cfg.BORSALAR["btcturk"]["api_key"]
        binance_key = cfg.BORSALAR["binance"]["api_key"]

        if not btcturk_key or not binance_key:
            print("\nHATA: Canli islem icin API key'ler gerekli!")
            print("  BTCTURK_API_KEY ve BTCTURK_API_SECRET")
            print("  BINANCE_API_KEY ve BINANCE_API_SECRET")
            print("\n.env dosyasina veya environment variable olarak tanimla.")
            print("Ya da paper trading ile test et: python3 bot_calistir.py")
            return

    # SIGINT (Ctrl+C) handler
    bot = ArbitrajBot(paper_trading=paper)

    def sigint_handler(sig, frame):
        bot.durdur()

    signal.signal(signal.SIGINT, sigint_handler)

    bot.calistir()


if __name__ == "__main__":
    main()
