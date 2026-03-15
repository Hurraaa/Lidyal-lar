"""
Borsa API Baglantilari
======================
BtcTurk ve Binance API istemcileri.
Sadece stdlib kullanir (requests yok, urllib ile).

Her borsa icin:
  - Fiyat cekme (public - API key gerekmez)
  - Bakiye sorgulama (private - API key gerekir)
  - Emir gonderme (private - API key gerekir)
"""

import json
import ssl
import time
import hmac
import hashlib
import base64
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError


# ============================================================
# ORTAK FONKSIYONLAR
# ============================================================

def _http_istegi(url, method="GET", headers=None, body=None, timeout=10):
    """
    HTTP istegi yapar, (status_code, json_body) doner.
    Hata durumunda (None, None) doner.
    """
    try:
        ctx = ssl.create_default_context()
        if headers is None:
            headers = {}
        headers["User-Agent"] = "StablecoinArbitrajBot/1.0"

        if body is not None:
            if isinstance(body, dict):
                body = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif isinstance(body, str):
                body = body.encode("utf-8")

        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            veri = json.loads(resp.read().decode())
            return resp.status, veri
    except HTTPError as e:
        try:
            hata_body = json.loads(e.read().decode())
        except Exception:
            hata_body = {"error": str(e)}
        return e.code, hata_body
    except Exception as e:
        return None, {"error": str(e)}


# ============================================================
# BTCTURK API
# ============================================================

class BtcTurkAPI:
    """BtcTurk API istemcisi."""

    def __init__(self, api_key="", api_secret=""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.btcturk.com"
        self.ad = "BtcTurk"

    def _imza_olustur(self):
        """BtcTurk API imzasi olusturur (HMAC-SHA256, Base64)."""
        if not self.api_key or not self.api_secret:
            return None, None, None

        stamp = str(int(time.time()) * 1000)
        data = f"{self.api_key}{stamp}".encode("utf-8")

        try:
            secret_decoded = base64.b64decode(self.api_secret)
        except Exception:
            secret_decoded = self.api_secret.encode("utf-8")

        signature = hmac.new(secret_decoded, data, hashlib.sha256).digest()
        signature_b64 = base64.b64encode(signature).decode("utf-8")

        return stamp, signature_b64, {
            "X-PCK": self.api_key,
            "X-Stamp": stamp,
            "X-Signature": signature_b64,
            "Content-Type": "application/json",
        }

    def fiyat_al(self, parite="USDTTRY"):
        """
        USDT/TRY anlik fiyat bilgisi alir.
        Returns: {"bid": float, "ask": float, "last": float} veya None
        """
        url = f"{self.base_url}/api/v2/ticker?pairSymbol={parite}"
        status, veri = _http_istegi(url)

        if status == 200 and veri and "data" in veri:
            for ticker in veri["data"]:
                if ticker.get("pairNormalized") == "USDT_TRY" or \
                   ticker.get("pair") == parite:
                    return {
                        "bid": float(ticker.get("bid", 0)),      # En iyi alis
                        "ask": float(ticker.get("ask", 0)),      # En iyi satis
                        "last": float(ticker.get("last", 0)),    # Son islem
                        "volume": float(ticker.get("volume", 0)),
                        "timestamp": time.time(),
                    }
        return None

    def bakiye_al(self):
        """
        Hesap bakiyelerini sorgular.
        Returns: {"TRY": float, "USDT": float} veya None
        """
        if not self.api_key:
            return None

        stamp, sig, headers = self._imza_olustur()
        if headers is None:
            return None

        url = f"{self.base_url}/api/v1/users/balances"
        status, veri = _http_istegi(url, headers=headers)

        if status == 200 and veri and "data" in veri:
            bakiyeler = {"TRY": 0.0, "USDT": 0.0}
            for b in veri["data"]:
                asset = b.get("asset", "")
                if asset in ("TRY", "USDT"):
                    bakiyeler[asset] = float(b.get("free", 0))
            return bakiyeler
        return None

    def emir_gonder(self, yon, miktar, fiyat, parite="USDTTRY"):
        """
        Limit emir gonderir.

        Args:
            yon: "buy" veya "sell"
            miktar: USDT miktari
            fiyat: TRY fiyati
            parite: Islem cifti

        Returns: {"success": bool, "order_id": str, ...} veya None
        """
        if not self.api_key:
            return {"success": False, "error": "API key yok"}

        stamp, sig, headers = self._imza_olustur()
        if headers is None:
            return {"success": False, "error": "Imza olusturulamadi"}

        # BtcTurk order tipleri: 0=limit, 1=market
        # orderMethod: 0=buy, 1=sell (veya "buy"/"sell" string)
        body = {
            "quantity": round(miktar, 2),
            "price": round(fiyat, 4),
            "orderType": 0,  # Limit
            "orderMethod": yon,  # "buy" veya "sell"
            "pairSymbol": parite,
        }

        url = f"{self.base_url}/api/v1/order"
        status, veri = _http_istegi(url, method="POST", headers=headers, body=body)

        if status == 200 and veri:
            return {
                "success": True,
                "order_id": veri.get("data", {}).get("id", "?"),
                "detay": veri,
            }
        return {
            "success": False,
            "error": f"HTTP {status}",
            "detay": veri,
        }


# ============================================================
# BINANCE API
# ============================================================

class BinanceAPI:
    """Binance API istemcisi."""

    def __init__(self, api_key="", api_secret=""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.binance.com"
        self.ad = "Binance"

    def _imza_olustur(self, params):
        """Binance API imzasi (HMAC-SHA256)."""
        if not self.api_key or not self.api_secret:
            return None

        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        params["signature"] = signature
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def fiyat_al(self, parite="USDTTRY"):
        """
        USDT/TRY anlik fiyat bilgisi alir.
        Returns: {"bid": float, "ask": float, "last": float} veya None
        """
        # Orderbook ticker (bid/ask icin)
        url = f"{self.base_url}/api/v3/ticker/bookTicker?symbol={parite}"
        status, veri = _http_istegi(url)

        if status == 200 and veri:
            sonuc = {
                "bid": float(veri.get("bidPrice", 0)),
                "ask": float(veri.get("askPrice", 0)),
                "timestamp": time.time(),
            }

            # Son islem fiyati
            url2 = f"{self.base_url}/api/v3/ticker/price?symbol={parite}"
            status2, veri2 = _http_istegi(url2)
            if status2 == 200 and veri2:
                sonuc["last"] = float(veri2.get("price", 0))
            else:
                sonuc["last"] = (sonuc["bid"] + sonuc["ask"]) / 2

            return sonuc
        return None

    def bakiye_al(self):
        """
        Hesap bakiyelerini sorgular.
        Returns: {"TRY": float, "USDT": float} veya None
        """
        if not self.api_key:
            return None

        params = {
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000,
        }
        headers = self._imza_olustur(params)
        if headers is None:
            return None

        url = f"{self.base_url}/api/v3/account?{urlencode(params)}"
        status, veri = _http_istegi(url, headers=headers)

        if status == 200 and veri and "balances" in veri:
            bakiyeler = {"TRY": 0.0, "USDT": 0.0}
            for b in veri["balances"]:
                asset = b.get("asset", "")
                if asset in ("TRY", "USDT"):
                    bakiyeler[asset] = float(b.get("free", 0))
            return bakiyeler
        return None

    def emir_gonder(self, yon, miktar, fiyat, parite="USDTTRY"):
        """
        Limit emir gonderir.

        Args:
            yon: "BUY" veya "SELL"
            miktar: USDT miktari
            fiyat: TRY fiyati

        Returns: {"success": bool, "order_id": str, ...} veya None
        """
        if not self.api_key:
            return {"success": False, "error": "API key yok"}

        params = {
            "symbol": parite,
            "side": yon.upper(),
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": f"{miktar:.2f}",
            "price": f"{fiyat:.4f}",
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000,
        }
        headers = self._imza_olustur(params)
        if headers is None:
            return {"success": False, "error": "Imza olusturulamadi"}

        url = f"{self.base_url}/api/v3/order"
        body = urlencode(params)
        status, veri = _http_istegi(url, method="POST", headers=headers,
                                     body=body.encode("utf-8"))

        if status == 200 and veri:
            return {
                "success": True,
                "order_id": str(veri.get("orderId", "?")),
                "detay": veri,
            }
        return {
            "success": False,
            "error": f"HTTP {status}",
            "detay": veri,
        }


# ============================================================
# FABRIKA FONKSIYONU
# ============================================================

def borsa_olustur(borsa_adi, api_key="", api_secret=""):
    """Borsa adina gore API istemcisi olusturur."""
    if borsa_adi.lower() in ("btcturk", "btc_turk"):
        return BtcTurkAPI(api_key, api_secret)
    elif borsa_adi.lower() in ("binance", "binance_tr"):
        return BinanceAPI(api_key, api_secret)
    else:
        raise ValueError(f"Bilinmeyen borsa: {borsa_adi}")


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  BORSA API BAGLANTI TESTI")
    print("=" * 50)
    print()

    # BtcTurk fiyat testi (API key gerekmez)
    print("BtcTurk USDT/TRY fiyat sorgusu...")
    btcturk = BtcTurkAPI()
    fiyat = btcturk.fiyat_al()
    if fiyat:
        print(f"  Bid (alis) : {fiyat['bid']:.4f} TL")
        print(f"  Ask (satis): {fiyat['ask']:.4f} TL")
        print(f"  Son islem  : {fiyat['last']:.4f} TL")
        print(f"  Spread     : {(fiyat['ask'] - fiyat['bid']):.4f} TL "
              f"(%{(fiyat['ask'] - fiyat['bid'])/fiyat['bid']*100:.3f})")
    else:
        print("  BASARISIZ - API'ye ulasilamadi")
    print()

    # Binance fiyat testi
    print("Binance USDT/TRY fiyat sorgusu...")
    binance = BinanceAPI()
    fiyat2 = binance.fiyat_al()
    if fiyat2:
        print(f"  Bid (alis) : {fiyat2['bid']:.4f} TL")
        print(f"  Ask (satis): {fiyat2['ask']:.4f} TL")
        print(f"  Son islem  : {fiyat2['last']:.4f} TL")
        print(f"  Spread     : {(fiyat2['ask'] - fiyat2['bid']):.4f} TL "
              f"(%{(fiyat2['ask'] - fiyat2['bid'])/fiyat2['bid']*100:.3f})")
    else:
        print("  BASARISIZ - API'ye ulasilamadi")
    print()

    # Borsalar arasi fark
    if fiyat and fiyat2:
        # En iyi arbitraj: birinde bid (satabilecegimiz), digerinde ask (alabilecegimiz)
        # Senaryo 1: BtcTurk'ten al (ask), Binance'de sat (bid)
        fark1 = (fiyat2["bid"] - fiyat["ask"]) / fiyat["ask"] * 100
        # Senaryo 2: Binance'den al (ask), BtcTurk'te sat (bid)
        fark2 = (fiyat["bid"] - fiyat2["ask"]) / fiyat2["ask"] * 100

        print("Borsalar arasi fark:")
        print(f"  BtcTurk'ten AL -> Binance'de SAT : %{fark1:+.3f}")
        print(f"  Binance'den AL -> BtcTurk'te SAT : %{fark2:+.3f}")

        en_iyi = max(fark1, fark2)
        maliyet = 0.33  # Toplam maliyet ~%0.33
        print(f"  En iyi fark : %{en_iyi:.3f}")
        print(f"  Maliyet     : %{maliyet:.3f}")
        print(f"  Net kar     : %{en_iyi - maliyet:.3f}")
        if en_iyi > maliyet:
            print(f"  >>> FIRSAT VAR! <<<")
        else:
            print(f"  Firsat yok (fark < maliyet)")
    print()
    print("Baglanti testi tamamlandi.")
