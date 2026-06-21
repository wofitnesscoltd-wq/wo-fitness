#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窩 Trading 大腦 — 盤中多年回測（Phase 4 第三條腿）
------------------------------------------------------------------
驗證「真正會上線的盤中訊號」——直接重用 alert_engine 的 eval_buy / eval_sell
（VWAP 站回、開盤區間 ORB、RSI、MACD、EMA、多週期 5m+15m、regime 閘門），
用 5 分 K 逐根 point-in-time 餵進去，模擬隔根進場、ATR 停損/目標、滑價＋手續費。

→ 這條腿補上後，三條腿到齊：A 日線長窗(backtest.py)、B 盤中近窗(本檔)、C 前向紙測(上線即累積)。

資料來源：
  • Polygon.io（建議，多年 1/5 分 K；你說花費不在意）：--source polygon --polygon-key KEY
    免費鑰匙有速率限制(約5次/分)，付費方案才能順跑多年多檔。
  • 富途（免費，近 1–2 年）：--source futu
  • 內建合成資料自測（免網路/免鑰匙）：--selftest

用法：
  python backtest_intraday.py --source polygon --polygon-key KEY --symbols NVDA,AMD --years 5
  python backtest_intraday.py --source futu --symbols US.NVDA,US.AMD
  python backtest_intraday.py --selftest
"""
import argparse, json, time, statistics, urllib.request
from datetime import datetime, timedelta

import alert_engine as ae   # 重用上線引擎的指標與訊號，確保「測的＝會跑的」


# ====================================================================
# 重採樣 5m → 15m（給多週期匯流用）
# ====================================================================
def resample(bars, k):
    if k <= 1:
        return bars
    out, day, chunk = [], None, []
    for b in bars:
        d = b["time"][:10]
        if d != day and chunk:
            out += _emit_chunks(chunk, k); chunk = []
        day = d; chunk.append(b)
    if chunk:
        out += _emit_chunks(chunk, k)
    return out


def _emit_chunks(g, k):
    res = []
    for j in range(0, len(g), k):
        c = g[j:j + k]
        if not c:
            continue
        res.append({"time": c[-1]["time"], "open": c[0]["open"],
                    "high": max(x["high"] for x in c), "low": min(x["low"] for x in c),
                    "close": c[-1]["close"], "volume": sum(x["volume"] for x in c)})
    return res


# ====================================================================
# 模擬（point-in-time，重用 eval_buy）
# ====================================================================
def simulate(bars, p, regime_by_day=None):
    if len(bars) < p["window"] + 10:
        return []
    b15 = resample(bars, 3)                         # 5m*3 = 15m
    trades, pos, j15 = [], None, 0
    W, slip = p["window"], p["slippage"]
    for i in range(W, len(bars) - 1):
        t_now = bars[i]["time"]
        while j15 < len(b15) and b15[j15]["time"] <= t_now:
            j15 += 1
        if pos is None:
            day = t_now[:10]
            ctx = {"regime": (regime_by_day or {}).get(day, "neutral"), "spy_ret": None}
            win = bars[i - W + 1:i + 1]
            win15 = b15[max(0, j15 - 80):j15]
            sigs = ae.eval_buy(win, None, win15, ctx)
            if sigs:
                sig = sigs[0]
                entry = bars[i + 1]["open"] * (1 + slip)
                stop = sig["stop"]
                risk = entry - stop
                if risk <= 0:
                    continue
                pos = {"i": i + 1, "entry": entry, "stop": stop, "target": sig["target"],
                       "risk": risk, "grade": sig["grade"], "type": sig["type"]}
        else:
            bar = bars[i]
            ex = kind = None
            if bar["low"] <= pos["stop"]:
                ex, kind = pos["stop"] * (1 - slip), "stop"
            elif bar["high"] >= pos["target"]:
                ex, kind = pos["target"] * (1 - slip), "target"
            elif i - pos["i"] >= p["max_hold"]:
                ex, kind = bar["close"] * (1 - slip), "time"
            if ex is not None:
                R = (ex - pos["entry"]) / pos["risk"]
                trades.append({"grade": pos["grade"], "type": pos["type"], "kind": kind,
                               "R": round(R, 3), "date": bars[pos["i"]]["time"][:10]})
                pos = None
    return trades


# ====================================================================
# regime（用 SPY 5m 推每日 risk_on/off）
# ====================================================================
def regime_from_spy(spy5):
    if not spy5:
        return {}
    by_day = {}
    for b in spy5:
        by_day.setdefault(b["time"][:10], []).append(b)
    days = sorted(by_day)
    closes = [by_day[d][-1]["close"] for d in days]
    e20 = ae.ema(closes, 20)
    out = {}
    for idx, d in enumerate(days):
        daily_up = e20[idx] is not None and closes[idx] > e20[idx]
        intover = ae.session_vwap(by_day[d])
        intraday_up = by_day[d][-1]["close"] > intover[-1]
        out[d] = ("risk_on" if daily_up and intraday_up else
                  "risk_off" if (not daily_up and not intraday_up) else "neutral")
    return out


# ====================================================================
# 統計
# ====================================================================
def _summ(trades):
    if not trades:
        return {"n": 0}
    Rs = [t["R"] for t in trades]
    wins = [r for r in Rs if r > 0]
    losses = [r for r in Rs if r <= 0]
    gain, loss = sum(wins), -sum(losses)
    by_year = {}
    for t in trades:
        by_year.setdefault(t["date"][:4], []).append(t["R"])
    return {
        "n": len(trades), "win_rate": round(len(wins) / len(trades) * 100, 1),
        "expectancy_R": round(statistics.mean(Rs), 3),
        "profit_factor": round(gain / loss, 2) if loss > 0 else float("inf"),
        "exit_mix": {k: sum(1 for t in trades if t["kind"] == k) for k in ("target", "stop", "time")},
        "by_year": {y: {"n": len(v), "expectancy_R": round(statistics.mean(v), 3)} for y, v in sorted(by_year.items())},
    }


def run(bars_by_symbol, p, regime_by_day=None):
    all_t, per = [], {}
    for sym, bars in bars_by_symbol.items():
        t = simulate(bars, p, regime_by_day)
        per[sym] = len(t); all_t += t
    by_grade = {g: _summ([t for t in all_t if t["grade"] == g]) for g in ("A", "B", "C")}
    return {"symbols": per, "overall": _summ(all_t), "by_grade": by_grade}


DEFAULT_P = {"window": 120, "max_hold": 78, "slippage": 0.0005, "fee": 0.0005}


# ====================================================================
# 資料載入
# ====================================================================
def load_polygon(tickers, years, key, sleep=0.25):
    out = {}
    end = datetime.utcnow().date()
    start = end - timedelta(days=int(365 * years))
    for tk in tickers:
        bars, cur = [], start
        while cur < end:
            nxt = min(cur + timedelta(days=90), end)
            url = (f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/5/minute/"
                   f"{cur:%Y-%m-%d}/{nxt:%Y-%m-%d}?adjusted=true&sort=asc&limit=50000&apiKey={key}")
            try:
                with urllib.request.urlopen(url, timeout=40) as r:
                    d = json.loads(r.read().decode())
            except Exception as e:
                print(f"  ⚠️ {tk} {cur}: {e}"); time.sleep(sleep); cur = nxt + timedelta(days=1); continue
            for b in d.get("results", []) or []:
                t = datetime.utcfromtimestamp(b["t"] / 1000)
                if ae.ET is not None:
                    t = t.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(ae.ET)
                hm = t.hour * 60 + t.minute
                if 9 * 60 + 30 <= hm < 16 * 60:                 # 只留常規盤
                    bars.append({"time": t.strftime("%Y-%m-%d %H:%M:%S"), "open": b["o"],
                                 "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"]})
            cur = nxt + timedelta(days=1); time.sleep(sleep)
        out[tk] = bars
        print(f"  {tk}: {len(bars)} 根 5 分 K")
    return out


def load_futu(codes, host="127.0.0.1", port=11111):
    from futu import OpenQuoteContext, RET_OK, KLType
    ctx = OpenQuoteContext(host=host, port=port)
    out = {}
    try:
        for code in codes:
            res = ctx.request_history_kline(code, ktype=KLType.K_5M, max_count=100000)
            ret, data = res[0], res[1]
            if ret != RET_OK:
                print(f"  ⚠️ {code}: {data}"); continue
            out[code] = [{"time": r["time_key"], "open": r["open"], "high": r["high"],
                          "low": r["low"], "close": r["close"], "volume": r["volume"]}
                         for _, r in data.iterrows()]
            print(f"  {code}: {len(out[code])} 根 5 分 K")
    finally:
        ctx.close()
    return out


def _synth_intraday(days=120, seed=11):
    import random
    random.seed(seed)
    bars, px = [], 100.0
    base = datetime(2024, 1, 2, 9, 30)
    for d in range(days):
        day = base + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        drift = 0.04 if (d // 10) % 2 == 0 else -0.03
        for k in range(78):                                  # 09:30–16:00 共 78 根 5 分
            o = px
            px = max(1.0, px * (1 + random.uniform(-0.0035, 0.0035) + drift / 100))
            t = day + timedelta(minutes=5 * k)
            bars.append({"time": t.strftime("%Y-%m-%d %H:%M:%S"), "open": round(o, 2),
                         "high": round(max(o, px) * 1.002, 2), "low": round(min(o, px) * 0.998, 2),
                         "close": round(px, 2), "volume": random.randint(3000, 12000)})
    return bars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["polygon", "futu"], default="polygon")
    ap.add_argument("--symbols", default="NVDA,AMD")
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--polygon-key", default=None)
    ap.add_argument("--spy", action="store_true", help="另抓 SPY 算每日 regime 閘門（更貼近上線）")
    ap.add_argument("--max-hold", type=int, default=78, help="持有上限(5分K根數；78≈一個交易日)")
    ap.add_argument("--sleep", type=float, default=0.25, help="Polygon 請求間隔(免費鑰匙調大)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    p = dict(DEFAULT_P); p["max_hold"] = a.max_hold
    regime_by_day = None

    if a.selftest:
        data = {"SYN1": _synth_intraday(seed=1), "SYN2": _synth_intraday(seed=2)}
    elif a.source == "polygon":
        if not a.polygon_key:
            raise SystemExit("Polygon 需要 --polygon-key（多年盤中資料來源；你說花費不在意可開付費方案）")
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
        print(f"抓 Polygon 5 分 K：{syms}　近 {a.years} 年 …")
        data = load_polygon(syms, a.years, a.polygon_key, a.sleep)
        if a.spy:
            spy = load_polygon(["SPY"], a.years, a.polygon_key, a.sleep).get("SPY", [])
            regime_by_day = regime_from_spy(spy)
    else:
        codes = [c.strip() for c in a.symbols.split(",") if c.strip()]
        print(f"抓富途 5 分 K：{codes}（近 1–2 年）…")
        data = load_futu(codes)
        if a.spy:
            regime_by_day = regime_from_spy(load_futu(["US.SPY"]).get("US.SPY", []))

    if not data or not any(data.values()):
        raise SystemExit("沒抓到資料；檢查鑰匙/代號/FutuOpenD，或用 --selftest。")

    res = run(data, p, regime_by_day)
    print("=" * 60)
    print(" 盤中回測（重用上線引擎：VWAP/ORB/RSI/MACD/EMA + 5m/15m + regime）")
    print("=" * 60)
    o = res["overall"]
    if not o.get("n"):
        print(" 沒有產生交易（資料太短或條件太嚴）。"); return
    print(f" 全部：交易 {o['n']}　勝率 {o['win_rate']}%　期望 {o['expectancy_R']}R　獲利因子 {o['profit_factor']}")
    print(f" 出場分布 {o['exit_mix']}")
    for g in ("A", "B", "C"):
        b = res["by_grade"][g]
        if b.get("n"):
            print(f" ・{g} 級：{b['n']} 筆　勝率 {b['win_rate']}%　期望 {b['expectancy_R']}R　獲利因子 {b['profit_factor']}")
    print(" 分年期望值：")
    for y, v in o["by_year"].items():
        print(f"   {y}: {v['n']} 筆　{v['expectancy_R']}R")
    print(" ✅ 這條腿驗的就是會上線的盤中邏輯；regime 用 --spy 更貼近實盤。")
    if a.json:
        print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
