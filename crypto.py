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


def _get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "wo-crypto"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---- Binance 永續（fapi）----
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
    gran = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H"}.get(interval, "5m")
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
        ctx = {"regime": regime, "spy_ret": btc_ret}
        order = {"A": 3, "B": 2, "C": 1}
        min_grade = self.cfg.get("min_grade", "B")
        token, chat = self.cfg.get("telegram_token"), self.cfg.get("telegram_chat")
        watch = self.load_watch(self.cfg.get("symbols"))
        holds = load_crypto_holdings()
        hmap = {h.get("sym"): h for h in holds if h.get("sym")}
        all_syms = list(dict.fromkeys(list(watch) + list(hmap)))
        pushed = 0
        price_of = {}
        for sym in all_syms:
            try:
                bars = [b for b in self.kl(sym, "5m", 160) if b.get("close") is not None]
                bars15 = [b for b in self.kl(sym, "15m", 80) if b.get("close") is not None]
            except Exception:
                continue
            if len(bars) < 40:
                continue
            last = bars[-1]["close"]
            price_of[sym] = last
            try:
                fr = self.fund(sym).get("funding", 0)
            except Exception:
                fr = 0
            fnote = f"；資金費率 {fr*100:.3f}%" + ("（多頭過熱付費，留意反轉）" if fr > 0.0005 else "（空方付費，偏多有利）" if fr < -0.0005 else "")
            if sym in watch:                                  # 自選找買點
                for sig in ae.eval_buy(bars, None, bars15, ctx):
                    sig["reason"] += fnote
                    pushed += self._emit(con, session, sym, sig, min_grade, order, token, chat)
            if sym in hmap:                                   # 持有的找賣點＋爆倉預警
                h = hmap[sym]
                for sig in ae.eval_sell(bars, None, ctx):
                    sig["reason"] += fnote
                    pushed += self._emit(con, session, sym, sig, min_grade, order, token, chat)
                lp = liq_price(h.get("entry"), h.get("lev"), h.get("side", "long"))
                dist = liq_distance_pct(last, lp, h.get("side", "long"))
                if dist is not None and dist < self.cfg.get("liq_warn_pct", 15):
                    warn = {"side": "exit", "type": "爆倉預警", "grade": "A",
                            "price": round(last, 4), "stop": round(lp, 4), "target": round(last, 4), "rr": None,
                            "reason": f"距估算爆倉價 {lp:.4f} 僅 {dist:.1f}%（{h.get('lev')}x {h.get('side','long')}）—考慮減倉/補保證金/降槓桿",
                            "feat": {}}
                    pushed += self._emit(con, session, sym, warn, "C", order, token, chat)
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
        side = {"long": "🟢 加密買點", "exit": "🔴 加密賣點"}.get(sig["side"], sig["side"])
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
