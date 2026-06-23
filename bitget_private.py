#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitget 私有 API（唯讀）— 拉你真實的合約倉位、可用保證金、未成交掛單。

設計原則：
- 金鑰只從「環境變數」讀，不寫死、不進版控、也不從命令列傳（避免出現在 ps/行程列表）：
    BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE
- 只需要「唯讀（Read-only）」權限。本模組不含任何下單/撤單/轉帳的呼叫。
- 簽名：ACCESS-SIGN = base64(HMAC_SHA256(secret, timestamp + METHOD + requestPath + body))
        headers 帶 ACCESS-KEY / ACCESS-SIGN / ACCESS-TIMESTAMP / ACCESS-PASSPHRASE。
- 只用標準庫（urllib），不需額外安裝。

Bitget V2 端點（USDT 永續＝productType "USDT-FUTURES"）：
- 倉位：   GET /api/v2/mix/position/all-position
- 帳戶：   GET /api/v2/mix/account/accounts
- 掛單：   GET /api/v2/mix/order/orders-pending
"""
import os
import time
import hmac
import json
import base64
import hashlib
import urllib.parse
import urllib.request
import urllib.error

BASE = "https://api.bitget.com"
PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"
_TIMEOUT = 10


class BitgetError(Exception):
    pass


def _creds():
    return (os.environ.get("BITGET_API_KEY", "").strip(),
            os.environ.get("BITGET_API_SECRET", "").strip(),
            os.environ.get("BITGET_API_PASSPHRASE", "").strip())


def configured():
    """三個環境變數都設好才算 configured。"""
    k, s, p = _creds()
    return bool(k and s and p)


def _sign(secret, ts, method, request_path, body=""):
    msg = ts + method.upper() + request_path + (body or "")
    mac = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(mac).decode()


def _f(x):
    try:
        if x in (None, ""):
            return None
        return float(x)
    except Exception:
        return None


def _get(path, params=None):
    key, secret, passphrase = _creds()
    if not (key and secret and passphrase):
        raise BitgetError("未設定 Bitget 唯讀金鑰（環境變數 BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE）")
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    request_path = path + qs
    ts = str(int(time.time() * 1000))
    sign = _sign(secret, ts, "GET", request_path, "")
    req = urllib.request.Request(BASE + request_path, method="GET")
    req.add_header("ACCESS-KEY", key)
    req.add_header("ACCESS-SIGN", sign)
    req.add_header("ACCESS-TIMESTAMP", ts)
    req.add_header("ACCESS-PASSPHRASE", passphrase)
    req.add_header("Content-Type", "application/json")
    req.add_header("locale", "en-US")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8"))
            raise BitgetError("Bitget API %s：%s" % (d.get("code"), d.get("msg")))
        except BitgetError:
            raise
        except Exception:
            raise BitgetError("Bitget HTTP %s（多半是金鑰/簽名/權限或 IP 白名單問題）" % getattr(e, "code", "?"))
    except Exception as e:
        raise BitgetError("連線 Bitget 失敗：%s" % e)
    if str(data.get("code")) not in ("0", "00000"):
        raise BitgetError("Bitget API %s：%s" % (data.get("code"), data.get("msg")))
    return data.get("data")


def positions():
    """回傳目前有倉的合約部位（已過濾 size=0）。"""
    data = _get("/api/v2/mix/position/all-position",
                {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN}) or []
    out = []
    for p in data:
        size = _f(p.get("total")) or 0
        if size == 0:
            continue
        lev = _f(p.get("leverage")) or 1
        entry = _f(p.get("openPriceAvg"))
        margin = _f(p.get("marginSize"))
        if margin is None and entry:
            margin = size * entry / lev
        out.append({
            "sym": (p.get("symbol") or "").upper(),
            "side": "short" if (p.get("holdSide") == "short") else "long",
            "size": size,
            "entry": entry,
            "lev": lev,
            "margin": margin,
            "upnl": _f(p.get("unrealizedPL")),
            "liq": _f(p.get("liquidationPrice")),
            "mark": _f(p.get("markPrice")),
            "marginMode": p.get("marginMode"),
            "marginCoin": p.get("marginCoin") or MARGIN_COIN,
            "src": "bitget",
        })
    return out


def account():
    """回傳 USDT 合約帳戶層級的權益與可用保證金（給全倉爆倉緩衝用）。"""
    data = _get("/api/v2/mix/account/accounts", {"productType": PRODUCT_TYPE}) or []
    for a in data:
        if (a.get("marginCoin") or "").upper() == MARGIN_COIN:
            return {
                "avail": _f(a.get("available")),
                "crossedAvail": _f(a.get("crossedMaxAvailable")),
                "equity": _f(a.get("accountEquity")) or _f(a.get("usdtEquity")),
                "upnl": _f(a.get("unrealizedPL")),
                "marginCoin": MARGIN_COIN,
            }
    return {}


def orders():
    """回傳未成交的掛單（唯讀）。"""
    data = _get("/api/v2/mix/order/orders-pending", {"productType": PRODUCT_TYPE}) or {}
    lst = data.get("entrustedList") if isinstance(data, dict) else data
    out = []
    for o in (lst or []):
        out.append({
            "sym": (o.get("symbol") or "").upper(),
            "side": o.get("side") or o.get("tradeSide") or o.get("posSide"),
            "size": _f(o.get("size")),
            "price": _f(o.get("price")) or _f(o.get("priceAvg")),
            "type": o.get("orderType"),
            "reduceOnly": o.get("reduceOnly"),
            "status": o.get("status"),
            "id": o.get("orderId"),
        })
    return out


def sync():
    """一次拉齊倉位 + 帳戶 + 掛單。倉位/帳戶失敗就拋（多半金鑰問題）；掛單失敗則略過。"""
    out = {"configured": True, "ts": int(time.time() * 1000)}
    out["positions"] = positions()
    out["account"] = account()
    try:
        out["orders"] = orders()
    except BitgetError:
        out["orders"] = []
    return out


if __name__ == "__main__":
    # 離線自我檢查：驗證簽名是決定性的、configured() 行為正確。不需要網路、不需要真金鑰。
    s1 = _sign("secretkey", "1700000000000", "GET", "/api/v2/mix/account/accounts?productType=USDT-FUTURES", "")
    s2 = _sign("secretkey", "1700000000000", "GET", "/api/v2/mix/account/accounts?productType=USDT-FUTURES", "")
    assert s1 == s2 and isinstance(s1, str) and len(s1) > 0, "簽名應為決定性字串"
    assert _sign("a", "1", "GET", "/x", "") != _sign("b", "1", "GET", "/x", ""), "不同 secret 應產生不同簽名"
    print("簽名範例：", s1)
    print("configured() =", configured(),
          "（要設好 BITGET_API_KEY/SECRET/PASSPHRASE 才會 True）")
    if configured():
        try:
            data = sync()
            print("倉位數：", len(data["positions"]),
                  "／掛單數：", len(data.get("orders", [])),
                  "／可用保證金：", (data.get("account") or {}).get("avail"))
        except BitgetError as e:
            print("呼叫失敗：", e)
    else:
        print("（未設金鑰，略過實際 API 呼叫）自我檢查通過。")
