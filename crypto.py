#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窩 Trading 大腦 — 加密永續監測模組（幣安 Binance ＋ Bitget）
------------------------------------------------------------------
24 小時掃描加密永續：即時價＋資金費率＋技術買賣點，重用美股那套訊號引擎
（alert_engine 的 eval_buy / eval_sell / 指標 / SQLite / Telegram），所以
「測過的、會跑的」邏輯一致；只是改成 24h 不分盤、市場 regime 用 BTC 當大盤。

公開行情免 API 金鑰；不下單、只讀。

用法（通常由 futu_bridge.py --crypto 啟動；也可單獨跑）：
  python crypto.py --source binance --symbols BTCUSDT,ETHUSDT,SOLUSDT
  python crypto.py --selftest
"""
import argparse, json, os, time, threading, urllib.request
from datetime import datetime

import alert_engine as ae

try:
    import bitget_private as bitget_mod   # Bitget 唯讀私有 API（自動同步真實倉位/保證金，給爆倉預警用）
except Exception:
    bitget_mod = None

HERE = os.path.dirname(os.path.abspath(__file__))
CRYPTO_WATCH = os.path.join(HERE, "crypto_watch.json")
CRYPTO_HOLDINGS = os.path.join(HERE, "crypto_holdings.json")
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
_IV_SEC = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


def load_crypto_holdings():
    try:
        return json.load(open(CRYPTO_HOLDINGS, encoding="utf-8")).get("holdings", [])
    except Exception:
        return []


def save_crypto_holdings(items):
    json.dump({"holdings": items, "updated": datetime.now().isoformat()},
              open(CRYPTO_HOLDINGS, "w", encoding="utf-8"), ensure_ascii=False)


def liq_price(entry, lev, side="long", mmr=0.005):
    """估算爆倉價（isolated 近似；各所階梯保證金不同，僅供參考）。"""
    try:
        entry = float(entry); lev = float(lev)
    except Exception:
        return None
    if entry <= 0 or lev <= 0:
        return None
    return entry * (1 - 1 / lev + mmr) if side == "long" else entry * (1 + 1 / lev - mmr)


def liq_distance_pct(price, lp, side="long"):
    if not price or not lp:
        return None
    return (price - lp) / price * 100 if side == "long" else (lp - price) / price * 100


CRYPTO_META = os.path.join(HERE, "crypto_meta.json")


def load_cmargin():
    try:
        return float(json.load(open(CRYPTO_META, encoding="utf-8")).get("avail", 0))
    except Exception:
        return 0.0


def save_cmargin(v):
    try:
        json.dump({"avail": float(v)}, open(CRYPTO_META, "w", encoding="utf-8"))
    except Exception:
        pass


def account_cross(holds, price_of, avail):
    """全倉帳戶層級總覽（與網頁 cryptoSummary 一致）：單腿不獨立爆倉，看整體緩衝。"""
    gross = net = upnl = used = 0.0
    for h in holds:
        try:
            sym = h.get("sym"); size = float(h.get("size", 0)); entry = float(h.get("entry", 0))
            lev = float(h.get("lev", 1)) or 1; side = h.get("side", "long")
        except Exception:
            continue
        if size <= 0 or entry <= 0:
            continue
        px = price_of.get(sym, entry) or entry
        n = size * px
        gross += n
        net += n * (-1 if side == "short" else 1)
        upnl += (entry - px) * size if side == "short" else (px - entry) * size
        used += size * entry / lev
    equity = avail + used
    maint = 0.005 * gross
    an = abs(net)
    buffer = (equity - maint) / an * 100 if (an > 1 and equity > maint) else (None if an <= 1 else 0.0)
    return {"gross": gross, "net": an, "net_signed": net, "upnl": upnl, "used": used,
            "avail": avail, "equity": equity, "buffer": buffer,
            "net_lev": an / equity if equity > 0 else 0}


def _get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "wo-crypto"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---- Binance 永續（fapi）----
# 真正的 24/7 加密（其餘 USDT 永續視為 tokenized 美股/商品，跟著美股盤）
REAL_CRYPTO = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "MATIC", "DOT",
    "LTC", "TRX", "TON", "SUI", "APT", "ARB", "OP", "NEAR", "ATOM", "FIL", "INJ", "SEI",
    "PEPE", "SHIB", "WIF", "BONK", "TIA", "RUNE", "AAVE", "UNI", "ORDI", "JUP", "PYTH",
    "ENA", "WLD", "FTM", "ALGO", "ICP", "HBAR", "KAS", "RENDER", "FET", "TAO", "BCH", "ETC",
}


def _base(sym):
    s = (sym or "").upper()
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q):
            return s[:-len(q)]
    return s


def is_stock_perp(sym):
    """tokenized 美股永續（COHR/QCOM/RKLB…）＝非真加密；週末/美股收盤時是低流動雜訊。"""
    return _base(sym) not in REAL_CRYPTO


def us_market_active():
    """美股是否在活躍時段（盤前~盤後，美東 04:00–20:00、週一~五）。tokenized 美股永續只在這時段掃才有意義。"""
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        n = _dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        n = _dt.datetime.utcnow() - _dt.timedelta(hours=4)
    if n.weekday() >= 5:
        return False
    mins = n.hour * 60 + n.minute
    return 4 * 60 <= mins <= 20 * 60


def binance_klines(sym, interval="5m", limit=200):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={interval}&limit={limit}"
    out = []
    for k in _get_json(url):
        t = datetime.utcfromtimestamp(k[0] / 1000).strftime("%Y-%m-%d %H:%M:%S")
        out.append({"time": t, "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
    return out


def binance_funding(sym):
    d = _get_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}")
    return {"mark": float(d.get("markPrice", 0)), "funding": float(d.get("lastFundingRate", 0))}


# ---- Bitget 永續（usdt-futures）----
def bitget_klines(sym, interval="5m", limit=200):
    gran = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H",
            "4h": "4H", "1d": "1D", "1w": "1W"}.get(interval, "5m")
    url = (f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}"
           f"&productType=usdt-futures&granularity={gran}&limit={limit}")
    d = _get_json(url)
    out = []
    for k in d.get("data", []):
        t = datetime.utcfromtimestamp(int(k[0]) / 1000).strftime("%Y-%m-%d %H:%M:%S")
        out.append({"time": t, "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
    out.sort(key=lambda b: b["time"])
    return out


def bitget_funding(sym):
    try:
        d = _get_json(f"https://api.bitget.com/api/v2/mix/market/current-fund-rate?symbol={sym}&productType=usdt-futures")
        r = (d.get("data") or [{}])[0]
        return {"mark": 0.0, "funding": float(r.get("fundingRate", 0))}
    except Exception:
        return {"mark": 0.0, "funding": 0.0}


SOURCES = {
    "binance": (binance_klines, binance_funding),
    "bitget": (bitget_klines, bitget_funding),
}


class CryptoScanner:
    """24h 加密掃描；重用 alert_engine 的訊號、DB、Telegram。"""
    def __init__(self, cfg):
        self.cfg = cfg
        self.source = cfg.get("source", "binance")
        self.kl, self.fund = SOURCES[self.source]
        self._stop = threading.Event()
        self._last_status = {}
        self._cooldown = {}   # 每標的上次推播時間，避免同一檔狂洗版

    @staticmethod
    def load_watch(default=None):
        try:
            return json.load(open(CRYPTO_WATCH, encoding="utf-8")).get("symbols", default or DEFAULT_SYMBOLS)
        except Exception:
            return default or DEFAULT_SYMBOLS

    @staticmethod
    def save_watch(symbols):
        json.dump({"symbols": symbols, "updated": datetime.now().isoformat()},
                  open(CRYPTO_WATCH, "w", encoding="utf-8"), ensure_ascii=False)

    def status(self):
        return dict(self._last_status)

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True); t.start(); return t

    def stop(self):
        self._stop.set()

    def _loop(self):
        sec = int(self.cfg.get("scan_sec", 60))
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as e:
                self._last_status["error"] = str(e)
            self._stop.wait(sec)

    def _regime(self):
        """用 BTC 當加密大盤：日線(用1h聚合)趨勢 + 盤中 VWAP。"""
        try:
            b = self.kl("BTCUSDT", "1h", 120)
        except Exception:
            return "neutral", None
        b = [x for x in b if x.get("close") is not None]
        if len(b) < 30:
            return "neutral", None
        closes = [x["close"] for x in b]
        up = closes[-1] > ae.ema(closes, 20)[-1]
        ret = (closes[-1] / closes[-13] - 1) * 100 if closes[-13] else 0
        return ("risk_on" if up else "risk_off"), ret

    def scan_once(self):
        con = ae._db()
        session = ae.session_key()
        regime, btc_ret = self._regime()
        # 週末（流動性低）自動更嚴：要求更大漲幅空間、只發 A 級
        import datetime as _dt
        weekend = (_dt.datetime.utcnow().weekday() >= 5)
        ctx = {"regime": regime, "spy_ret": btc_ret,
               "min_move": float(self.cfg.get("min_move", 0.06)) * (1.5 if weekend else 1.0),
               "min_rr": float(self.cfg.get("min_rr", 1.8)),
               "min_rvol": float(self.cfg.get("min_rvol", 0.8)) * (1.5 if weekend else 1.0)}
        order = {"A": 3, "B": 2, "C": 1}
        min_grade = "A" if weekend else self.cfg.get("min_grade", "A")
        token, chat = self.cfg.get("telegram_token"), self.cfg.get("telegram_chat")
        watch = self.load_watch(self.cfg.get("symbols"))
        holds = load_crypto_holdings()
        # 若設定了 Bitget 唯讀金鑰：每輪直接從交易所拉真實倉位＋權益（最準），寫回檔案。
        # 這樣 ⚠️ 全倉爆倉預警不必開著網頁也是即時的（手動/幣安部位＝src!='bitget' 會保留）。
        bpos = None; bequity = None
        if bitget_mod is not None and bitget_mod.configured():
            try:
                bpos = bitget_mod.positions()
                others = [h for h in holds if h.get("src") != "bitget"]
                holds = others + [{"sym": p["sym"], "size": p["size"], "entry": p["entry"],
                                   "lev": p["lev"], "side": p["side"], "src": "bitget"} for p in bpos]
                save_crypto_holdings(holds)
                bacct = bitget_mod.account()
                bequity = bacct.get("equity")
                used = sum((h.get("size") or 0) * (h.get("entry") or 0) / ((h.get("lev") or 1) or 1)
                           for h in holds if h.get("src") == "bitget")
                if bequity is not None:
                    save_cmargin(round(bequity - used, 2))      # 可用=權益−已用，讓檔案版引擎權益也≈真實
                elif bacct.get("avail") is not None:
                    save_cmargin(bacct["avail"])
            except Exception:
                bpos = None; bequity = None
        hmap = {h.get("sym"): h for h in holds if h.get("sym")}
        all_syms = list(dict.fromkeys(list(watch) + list(hmap)))
        pushed = 0
        price_of = {}
        for sym in all_syms:
            # tokenized 美股永續（COHRUSDT/QCOMUSDT…）：價格跟著美股走，但 Bitget 上的『量』是稀薄 crypto 量、
            # K 棒沒跳空，RSI/量都不代表真實個股 → 不在這裡發技術訊號。真實技術面看個股(牛牛引擎)。
            # 持倉者仍抓現價（給帳戶層級爆倉緩衝用，槓桿風險 24h 都在）。
            if is_stock_perp(sym):
                if sym in hmap:
                    try:
                        kb = self.kl(sym, "5m", 2)
                        if kb and kb[-1].get("close") is not None:
                            price_of[sym] = kb[-1]["close"]
                    except Exception:
                        pass
                continue
            # 加密買賣點推播已移除（太吵、你不需要）。這裡只取現價，給帳戶層級『全倉爆倉預警』用。
            try:
                kb = self.kl(sym, "5m", 2)
                if kb and kb[-1].get("close") is not None:
                    price_of[sym] = kb[-1]["close"]
            except Exception:
                continue
        # 全倉爆倉預警：帳戶層級（保命警示，預設保留；--no-crypto-liq 可關）
        avail = load_cmargin()
        if self.cfg.get("liq_alerts", True):
            buf = None; net = 0.0; net_lev = 0.0; ndir = "多"; eq_disp = 0.0
            if bequity is not None and bpos:      # Bitget 直算（用真實權益＋標記價，最準）
                gross = sum((p.get("size") or 0) * (p.get("mark") or 0) for p in bpos)
                net_signed = sum((p.get("size") or 0) * (p.get("mark") or 0) * (-1 if p.get("side") == "short" else 1) for p in bpos)
                net = abs(net_signed); maint = 0.005 * gross
                buf = ((bequity - maint) / net * 100) if (net > 1 and bequity > maint) else (None if net <= 1 else 0.0)
                net_lev = net / bequity if bequity > 0 else 0.0
                ndir = "多" if net_signed >= 0 else "空"; eq_disp = bequity
            elif holds and avail > 0:             # 沒 Bitget 金鑰時退回原本的檔案＋報價推估
                ac = account_cross(holds, price_of, avail)
                buf = ac["buffer"]; net = ac["net"]; net_lev = ac["net_lev"]
                ndir = "多" if ac["net_signed"] >= 0 else "空"; eq_disp = ac["equity"]
            if buf is not None:
                hourb = int(time.time() // 3600)   # 危險區內每小時再催一次（type 帶時段桶避免被去重擋掉）
                for thr in (7, 12, 20):            # 命中最嚴重的一級就推
                    if buf < thr:
                        sev = {7: "🚨 危急", 12: "⚠️ 警告", 20: "留意"}[thr]
                        warn = {"side": "exit", "type": f"全倉爆倉預警<{thr}%·h{hourb}", "grade": "A",
                                "price": 0, "stop": 0, "target": 0, "rr": None,
                                "reason": (f"{sev}：全倉帳戶緩衝僅 {buf:.1f}%（淨曝險 {net:.0f}U 偏{ndir}、淨槓桿 {net_lev:.1f}x、"
                                           f"權益 {eq_disp:.0f}U）—淨方向再逆走約 {buf:.1f}% 就接近強平，考慮補保證金／降淨曝險／加厚保險腿。"),
                                "feat": {}}
                        pushed += self._emit(con, session, "ACCOUNT", warn, "C", order, token, chat)
                        break
        try:
            ae.track_outcomes(con, price_of)
        except Exception:
            pass
        con.close()
        self._last_status = {"ts": datetime.now().isoformat(timespec="seconds"),
                             "source": self.source, "regime": regime,
                             "scanned": len(watch), "pushed": pushed}

    def _emit(self, con, session, sym, sig, min_grade, order, token, chat):
        if order.get(sig["grade"], 0) < order.get(min_grade, 1):
            return 0
        code = sym + "@" + self.source
        if ae.already_alerted(con, session, code, sig["side"], sig["type"]):
            return 0
        key = self.cfg.get("anthropic_key")
        allow = True
        if key:
            v = ae.ai_vet(sym, sig, key, self.cfg.get("ai_model", "claude-haiku-4-5-20251001"))
            if v:
                sig["reason"] += "｜AI複核：" + v["verdict"] + ("，" + v["note"] if v.get("note") else "")
                if v["verdict"] == "不建議":
                    allow = False
        side = "⚠️ 全倉爆倉預警" if "爆倉" in sig.get("type", "") else {"long": "🟢 加密買點", "exit": "🔴 加密賣點"}.get(sig["side"], sig["side"])
        msg = (f"<b>{side}・{sym}（{self.source}）</b>　等級 <b>{sig['grade']}</b>\n"
               f"型態：{sig['type']}\n現價 {sig['price']}　停損 {sig['stop']}　目標 {sig['target']}　R:R {sig.get('rr')}\n"
               f"理由：{sig['reason']}\n⚠️ 永續槓桿風險高、可能爆倉；非投資建議，務必設停損。")
        ok = ae.send_telegram(token, chat, msg) if (token and allow) else False
        ae.log_alert(con, session, code, sig, ok)
        return 1 if ok else 0


def _synth(n=200, seed=5):
    import random
    random.seed(seed)
    bars, px = [], 60000.0
    for i in range(n):
        o = px; px = max(1, px * (1 + random.uniform(-0.004, 0.0045)))
        bars.append({"time": "2026-06-19 %02d:%02d:00" % (i // 12 % 24, (i * 5) % 60),
                     "open": o, "high": max(o, px) * 1.001, "low": min(o, px) * 0.999,
                     "close": px, "volume": random.uniform(10, 90)})
    return bars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=list(SOURCES), default="binance")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--min-grade", default="B", choices=["A", "B", "C"])
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        b = _synth(seed=2); b15 = _synth(seed=3)
        print("eval_buy:", json.dumps(ae.eval_buy(b, None, b15, {"regime": "risk_on", "spy_ret": 1}), ensure_ascii=False)[:300])
        print("eval_sell:", json.dumps(ae.eval_sell(_synth(seed=9), None, {"regime": "risk_off"}), ensure_ascii=False)[:300])
        print("OK（合成資料；真實行情需連 Binance/Bitget）")
        return
    cfg = {"source": a.source, "symbols": [s.strip().upper() for s in a.symbols.split(",") if s.strip()],
           "min_grade": a.min_grade, "telegram_token": None, "scan_sec": 60}
    sc = CryptoScanner(cfg)
    print(f"加密掃描（{a.source}）：{cfg['symbols']}")
    sc.scan_once()
    print("status:", sc.status())


if __name__ == "__main__":
    main()
