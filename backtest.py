#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窩 Trading 大腦 — 日線回測（Phase 4：上線前驗證）
------------------------------------------------------------------
用「點時間(point-in-time)」方式回測波段/日線級別的邏輯，驗證**進場有沒有邊際**，
再決定要不要讓引擎上線。計入滑價與手續費、用 ATR 反推停損、固定風險%資金管理。

老實話（與 ARCHITECTURE §6 一致）：
  • 這支驗證的是「日線可回測」的部分：趨勢順勢 + 唐奇安(Donchian)突破 + ATR 風險 + regime 濾網。
  • 盤中專屬訊號（VWAP 回踩、開盤區間 ORB、盤中 RVOL）**日線資料測不了**，要靠
    前向紙測累積，或選配 Polygon 盤中歷史另測。不假裝日線能驗證盤中。

用法：
  # 用富途日線（需裝 futu-api、開著 FutuOpenD）
  python backtest.py --symbols US.NVDA,US.AMD,US.AAPL --num 2500 --risk 1
  # 沒有 SDK 也能跑內建合成資料自測：
  python backtest.py --selftest
"""
import argparse, json, statistics
from datetime import datetime

import alert_engine as ae   # 重用 ema / rsi / macd / atr，避免重造輪子


# ====================================================================
# 指標序列（point-in-time：第 i 根只用 <= i 的資料）
# ====================================================================
def _series(bars):
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    e20 = ae.ema(closes, 20)
    e50 = ae.ema(closes, 50)
    rsi = ae.rsi(closes, 14)
    # ATR 序列（Wilder）
    atr = [None] * len(bars)
    trs = [0.0]
    for i in range(1, len(bars)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if len(bars) > 14:
        a = sum(trs[1:15]) / 14
        atr[14] = a
        for i in range(15, len(bars)):
            a = (a * 13 + trs[i]) / 14
            atr[i] = a
    return {"close": closes, "high": highs, "low": lows, "e20": e20, "e50": e50, "rsi": rsi, "atr": atr}


def donchian_high(highs, i, n=20):
    if i < n:
        return None
    return max(highs[i - n:i])      # 不含當根，避免未來函數


# ====================================================================
# 單檔回測（狀態機）
# ====================================================================
def backtest_symbol(symbol, bars, p):
    if len(bars) < 80:
        return []
    s = _series(bars)
    trades = []
    pos = None
    slip = p["slippage"]
    rr = p["rr"]
    max_hold = p["max_hold"]
    for i in range(60, len(bars) - 1):
        if pos is None:
            dh = donchian_high(s["high"], i, p["donchian"])
            trend_ok = s["e20"][i] is not None and s["e50"][i] is not None and s["e20"][i] > s["e50"][i]
            breakout = dh is not None and s["close"][i] > dh
            atr = s["atr"][i]
            if breakout and trend_ok and atr:
                entry = bars[i + 1]["open"] * (1 + slip)        # 隔日開盤進場 + 滑價
                stop = entry - p["atr_mult"] * atr
                risk = entry - stop
                if risk <= 0:
                    continue
                pos = {"i": i + 1, "entry": entry, "stop": stop,
                       "target": entry + rr * risk, "risk": risk, "sym": symbol}
        else:
            bar = bars[i]
            exit_px = exit_kind = None
            if bar["low"] <= pos["stop"]:                       # 同根同時觸發：保守假設先停損
                exit_px, exit_kind = pos["stop"] * (1 - slip), "stop"
            elif bar["high"] >= pos["target"]:
                exit_px, exit_kind = pos["target"] * (1 - slip), "target"
            elif i - pos["i"] >= max_hold:
                exit_px, exit_kind = bar["close"] * (1 - slip), "time"
            if exit_px is not None:
                R = (exit_px - pos["entry"]) / pos["risk"]
                trades.append({"sym": symbol, "entry_date": bars[pos["i"]]["time"][:10],
                               "exit_date": bar["time"][:10], "entry": round(pos["entry"], 2),
                               "exit": round(exit_px, 2), "kind": exit_kind, "R": round(R, 3)})
                pos = None
    return trades


# ====================================================================
# 統計
# ====================================================================
def _max_drawdown(equity):
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    return round(mdd * 100, 1)


def summarize(trades, p):
    if not trades:
        return {"n": 0, "note": "沒有產生任何交易（資料太短或條件太嚴）。"}
    trades = sorted(trades, key=lambda t: t["exit_date"])
    Rs = [t["R"] for t in trades]
    wins = [r for r in Rs if r > 0]
    losses = [r for r in Rs if r <= 0]
    fee = p["fee"]
    riskfrac = p["risk"] / 100.0
    equity = [1.0]
    for r in Rs:
        equity.append(equity[-1] * (1 + riskfrac * r - fee))     # 每筆扣手續費(以R單位近似)
    gain = sum(wins) or 0.0
    loss = -sum(losses) or 0.0
    by_year = {}
    for t in trades:
        y = t["exit_date"][:4]
        by_year.setdefault(y, []).append(t["R"])
    return {
        "n": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "expectancy_R": round(statistics.mean(Rs), 3),
        "avg_win_R": round(statistics.mean(wins), 3) if wins else 0,
        "avg_loss_R": round(statistics.mean(losses), 3) if losses else 0,
        "profit_factor": round(gain / loss, 2) if loss > 0 else float("inf"),
        "total_return_pct": round((equity[-1] - 1) * 100, 1),
        "max_drawdown_pct": _max_drawdown(equity),
        "exit_mix": {k: sum(1 for t in trades if t["kind"] == k) for k in ("target", "stop", "time")},
        "by_year": {y: {"n": len(v), "expectancy_R": round(statistics.mean(v), 3)} for y, v in sorted(by_year.items())},
    }


def run_backtest(bars_by_symbol, p):
    all_trades = []
    per_symbol = {}
    for sym, bars in bars_by_symbol.items():
        t = backtest_symbol(sym, bars, p)
        per_symbol[sym] = len(t)
        all_trades += t
    return {"params": p, "symbols": per_symbol, "summary": summarize(all_trades, p),
            "trades": all_trades}


DEFAULT_PARAMS = {"donchian": 20, "atr_mult": 1.5, "rr": 2.0, "max_hold": 20,
                  "slippage": 0.0005, "fee": 0.0005, "risk": 1.0}


# ====================================================================
# 資料載入（富途日線）＋ CLI
# ====================================================================
def load_daily_futu(codes, num, host="127.0.0.1", port=11111):
    from futu import OpenQuoteContext, RET_OK, KLType
    ctx = OpenQuoteContext(host=host, port=port)
    out = {}
    try:
        for code in codes:
            res = ctx.request_history_kline(code, ktype=KLType.K_DAY, max_count=num)
            ret, data = res[0], res[1]
            if ret != RET_OK:
                print(f"  ⚠️ {code}: {data}"); continue
            bars = [{"time": r["time_key"], "open": r["open"], "high": r["high"],
                     "low": r["low"], "close": r["close"], "volume": r["volume"]}
                    for _, r in data.iterrows()]
            out[code] = bars
            print(f"  {code}: {len(bars)} 根日線")
    finally:
        ctx.close()
    return out


def _synthetic(n=1200, seed=7):
    import random
    random.seed(seed)
    bars, px = [], 50.0
    for i in range(n):
        drift = 0.06 if (i // 120) % 2 == 0 else -0.03       # 多空交替的市況
        o = px
        px = max(1.0, px * (1 + random.uniform(-0.02, 0.02) + drift / 100))
        hi, lo = max(o, px) * 1.01, min(o, px) * 0.99
        bars.append({"time": "20%02d-%02d-%02d" % (18 + i // 252, (i % 252) // 21 + 1, i % 21 + 1),
                     "open": round(o, 2), "high": round(hi, 2), "low": round(lo, 2),
                     "close": round(px, 2), "volume": 1000 + i})
    return bars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="US.NVDA", help="逗號分隔，如 US.NVDA,US.AMD")
    ap.add_argument("--num", type=int, default=2500, help="每檔抓幾根日線（~10年=2500）")
    ap.add_argument("--risk", type=float, default=1.0, help="每筆風險% 用於資金曲線")
    ap.add_argument("--rr", type=float, default=2.0, help="目標風險報酬比")
    ap.add_argument("--atr-mult", type=float, default=1.5, help="停損 = 進場 - atr_mult×ATR")
    ap.add_argument("--donchian", type=int, default=20, help="突破回看天數")
    ap.add_argument("--max-hold", type=int, default=20, help="時間停損（持有天數上限）")
    ap.add_argument("--futu-host", default="127.0.0.1")
    ap.add_argument("--futu-port", type=int, default=11111)
    ap.add_argument("--selftest", action="store_true", help="用內建合成資料自測（免 SDK）")
    ap.add_argument("--json", action="store_true", help="輸出完整 JSON（含每筆交易）")
    a = ap.parse_args()

    p = dict(DEFAULT_PARAMS)
    p.update({"risk": a.risk, "rr": a.rr, "atr_mult": a.atr_mult,
              "donchian": a.donchian, "max_hold": a.max_hold})

    if a.selftest:
        data = {"SYN1": _synthetic(seed=1), "SYN2": _synthetic(seed=2), "SYN3": _synthetic(seed=3)}
    else:
        codes = [c.strip() for c in a.symbols.split(",") if c.strip()]
        print(f"抓富途日線：{codes} …")
        data = load_daily_futu(codes, a.num, a.futu_host, a.futu_port)
        if not data:
            raise SystemExit("沒抓到資料；確認 FutuOpenD 已登入、代號正確，或用 --selftest。")

    res = run_backtest(data, p)
    print("=" * 56)
    print(" 回測結果（日線：趨勢順勢 + Donchian 突破 + ATR 風險）")
    print("=" * 56)
    print(" 參數：", json.dumps(p, ensure_ascii=False))
    s = res["summary"]
    if not s.get("n"):
        print(" ", s.get("note")); return
    print(f" 交易數 {s['n']}　勝率 {s['win_rate']}%　期望值 {s['expectancy_R']}R")
    print(f" 平均賺 {s['avg_win_R']}R／平均虧 {s['avg_loss_R']}R　獲利因子 {s['profit_factor']}")
    print(f" 資金曲線總報酬 {s['total_return_pct']}%　最大回落 {s['max_drawdown_pct']}%")
    print(f" 出場分布 {s['exit_mix']}")
    print(" 分年期望值：")
    for y, v in s["by_year"].items():
        print(f"   {y}: {v['n']} 筆　{v['expectancy_R']}R")
    print(" ⚠️ 本回測只驗證日線可測的邏輯；盤中 VWAP/ORB 需前向紙測或 Polygon 另測。")
    if a.json:
        print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
