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
    # D5：暫時性網路/伺服器錯誤重試＋退避（0.4s/0.8s/1.6s）；金鑰/簽名類(4xx)不重試，直接拋。
    backoffs = [0.4, 0.8, 1.6]
    last_err = None
    for attempt in range(len(backoffs) + 1):
        ts = str(int(time.time() * 1000))             # 每次重簽（timestamp 會變）
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
            if str(data.get("code")) not in ("0", "00000"):
                raise BitgetError("Bitget API %s：%s" % (data.get("code"), data.get("msg")))
            return data.get("data")
        except urllib.error.HTTPError as e:
            code = getattr(e, "code", 0)
            if code and code < 500 and code != 429:    # 4xx(金鑰/簽名/權限)：不重試
                try:
                    d = json.loads(e.read().decode("utf-8"))
                    raise BitgetError("Bitget API %s：%s" % (d.get("code"), d.get("msg")))
                except BitgetError:
                    raise
                except Exception:
                    raise BitgetError("Bitget HTTP %s（多半是金鑰/簽名/權限或 IP 白名單問題）" % code)
            last_err = "HTTP %s" % code                # 5xx/429：可重試
        except BitgetError:
            raise
        except Exception as e:                         # 連線/逾時：可重試
            last_err = str(e)
        if attempt < len(backoffs):
            time.sleep(backoffs[attempt])
    raise BitgetError("連線 Bitget 失敗（已重試 %d 次）：%s" % (len(backoffs), last_err))


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
            "mmr": _f(p.get("keepMarginRate")),       # 維持保證金率（tiered，交易所真實值；給彈藥線/爆倉線用，勝過固定 0.5%）
            "marginRatio": _f(p.get("marginRatio")),  # 此倉保證金率（交叉檢核本地估算用）
            "marginMode": p.get("marginMode"),
            "marginCoin": p.get("marginCoin") or MARGIN_COIN,
            "src": "bitget",
        })
    _tag_leg_roles(out)
    return out


def _tag_leg_roles(positions_list):
    """P1-1：算一次、存成欄位、全系統讀同一份的『腿角色』leg_role。
    同合約多名目 L、空名目 S：|L−S|/(L+S) < 0.2 → 兩腿皆 lock（對鎖）；
    否則量小邊 insurance（保險腿）、量大邊 directional（方向腿）；單腿一律 directional。
    名目用標記價×size；缺標記價退用進場價。此為預設值，前端手動覆蓋優先於此。"""
    notion = {}
    for p in positions_list:
        px = p.get("mark") or p.get("entry") or 0
        n = (p.get("size") or 0) * (px or 0)
        b = notion.setdefault(p["sym"], {"long": 0.0, "short": 0.0})
        b[p["side"]] += n
    for p in positions_list:
        b = notion.get(p["sym"], {})
        L, S = b.get("long", 0.0), b.get("short", 0.0)
        if L > 0 and S > 0:
            gross = L + S
            if gross > 0 and abs(L - S) / gross < 0.2:
                role = "lock"
            else:
                mine = L if p["side"] == "long" else S
                other = S if p["side"] == "long" else L
                role = "insurance" if mine < other else "directional"
        else:
            role = "directional"
        p["leg_role"] = role


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
                "riskRate": _f(a.get("crossedRiskRate")),   # 全倉風險率(0→1，近 1 接近強平)；交叉檢核本地緩衝估算
                "locked": _f(a.get("locked")),
                "maxTransferOut": _f(a.get("maxTransferOut")),
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
