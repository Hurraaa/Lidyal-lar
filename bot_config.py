"""
Arbitraj Bot Konfigurasyonu
===========================
Tum ayarlar burada. API key'ler .env'den veya buradan okunur.

ONEMLI: API key'leri buraya yazmak yerine .env dosyasina yaz!
        bot_config.py'yi git'e commitlersin, .env'yi ASLA commitleme.
"""

import os

# ============================================================
# CALISMA MODU
# ============================================================

# True = Gercek islem YAPMAZ, sadece simule eder (ONCE BUNU KULLAN!)
# False = Gercek para ile islem yapar
PAPER_TRADING = True

# ============================================================
# SERMAYE & GUVENLIK LIMITLERI
# ============================================================

TOPLAM_SERMAYE_TL = 1_000          # Baslangic sermayesi
BORSA_BASI_SERMAYE_TL = 500        # Her borsaya 500 TL

# Tek islem limitleri
MIN_ISLEM_USDT = 10                # Minimum 10 USDT (borsalarin limiti)
MAX_ISLEM_USDT = 50                # Maksimum 50 USDT (guvende kal)
ISLEM_BAKIYE_ORANI = 0.30          # Bakiyenin max %30'u ile islem

# Gunluk limitler
GUNLUK_MAX_ISLEM = None             # Islem sayisi limiti yok (zarar limiti koruyor)
GUNLUK_MAX_ZARAR_TL = 15           # Gunde max 15 TL zarar (toplam sermayenin %1.5'i)
GUNLUK_MAX_ZARAR_DURDUR = True     # Zarar limitine ulasilinca botu durdur

# Spread (fark) esikleri
MIN_KAR_ESIGI_PCT = 0.003          # En az %0.30 net kar olmadan islem yapma
ACIL_CIKIS_FARK_PCT = 0.05         # %5'ten buyuk fark = ANORMAL, islem yapma
                                    # (muhtemelen bir borsada sorun var)

# ============================================================
# BORSA AYARLARI
# ============================================================

BORSALAR = {
    "btcturk": {
        "aktif": True,
        "api_key": os.environ.get("BTCTURK_API_KEY", ""),
        "api_secret": os.environ.get("BTCTURK_API_SECRET", ""),
        "base_url": "https://api.btcturk.com",
        "komisyon_maker": 0.0010,     # %0.10
        "komisyon_taker": 0.0020,     # %0.20
        "parite": "USDTTRY",
        "min_islem": 10,              # Min USDT
    },
    "binance": {
        "aktif": True,
        "api_key": os.environ.get("BINANCE_API_KEY", ""),
        "api_secret": os.environ.get("BINANCE_API_SECRET", ""),
        "base_url": "https://api.binance.com",
        "komisyon_maker": 0.0010,     # %0.10
        "komisyon_taker": 0.0010,     # %0.10
        "parite": "USDTTRY",
        "min_islem": 10,              # Min USDT
    },
}

# ============================================================
# BOT CALISMA AYARLARI
# ============================================================

# Fiyat kontrol sikligi (saniye)
FIYAT_KONTROL_ARALIGI = 10         # Her 10 saniyede fiyat cek

# API hata toleransi
MAX_API_HATASI_UST_USTE = 5        # 5 ust uste hata = botu durdur
API_HATA_BEKLEME_SN = 30           # Hata sonrasi 30 sn bekle

# Loglama
LOG_DOSYASI = "arbitraj_bot.log"
LOG_ISLEMLER = "islem_gecmisi.json"

# Telegram bildirimi (opsiyonel)
TELEGRAM_AKTIF = False
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ============================================================
# SLIPPAGE & MALIYET
# ============================================================

SLIPPAGE_PCT = 0.0003              # %0.03 beklenen slippage
# Toplam maliyet = alis_komisyon + satis_komisyon + slippage
# BtcTurk taker + Binance taker + slippage = %0.20 + %0.10 + %0.03 = %0.33
