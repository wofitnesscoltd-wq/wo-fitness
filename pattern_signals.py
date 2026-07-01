#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窩 Trading — 訊號記分卡（P6-1）。**純確定性比對，不預測方向、不呼叫任何 AI。**

兩個可自動化訊號（起始參數見各函式，皆可調）：
  1. 低波動蓄積：永續 15m K 的近 20 根滾動實現波動率，低於樣本分布的 25 百分位 → 觸發。
  2. 資金費率趨勢/水位異常：近 8 期資金費移動平均，偏離基準 > 1 個標準差且連續 ≥ 3 期同向 → 觸發。

輸出永遠標「N/2」（未觸發顯示 0/2，不留空——留空易被誤讀成「沒檢查」）。
固定寫死在輸出裡的免責句：「僅供比對，非預測」。

⚠️ 基準說明（誠實）：理想是「該標的近 90 天基準」，但 15m K 難拉 90 天。
本模組的基準＝「當次傳入的資料窗」本身的分布，故意標明「(近窗基準)」，不假裝有 90 天。
未平倉量成長率、監理層評論 → 本輪不做（前者需新資料源、後者屬對話層語意判讀）。
"""
import math

DISCLAIMER = "僅供比對，非預測"


def _log_returns(closes):
    out = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a and b and a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def _stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _percentile(sorted_xs, pct):
    if not sorted_xs:
        return None
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    k = (len(sorted_xs) - 1) * (pct / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] * (hi - k) + sorted_xs[hi] * (k - lo)


def low_vol_signal(bars, window=20, pct=25):
    """近 window 根滾動實現波動率 < 樣本 pct 百分位 → 低波動蓄積。bars: [{'close':..}]。"""
    closes = [b.get("close") for b in bars if b.get("close") is not None]
    if len(closes) < window + 10:
        return {"triggered": False, "note": "資料不足", "ok": False}
    rets = _log_returns(closes)
    rolls = []
    for i in range(window, len(rets) + 1):
        rolls.append(_stdev(rets[i - window:i]))
    if len(rolls) < 8:
        return {"triggered": False, "note": "資料不足", "ok": False}
    cur = rolls[-1]
    thr = _percentile(sorted(rolls), pct)
    triggered = thr is not None and cur <= thr
    note = ("近 %d 根實現波動 %.4f%% ≤ 近窗 %d 百分位 %.4f%% → 低波動蓄積(近窗基準)"
            % (window, cur * 100, pct, (thr or 0) * 100)) if triggered else \
           ("波動 %.4f%% 未低於近窗 %d 百分位 %.4f%%" % (cur * 100, pct, (thr or 0) * 100))
    return {"triggered": bool(triggered), "note": note, "ok": True}


def funding_signal(funding_hist, recent=8, consec=3, k_std=1.0):
    """近 recent 期資金費 MA 偏離近窗基準 > k_std 標準差，且尾端連續 ≥ consec 期同向 → 觸發。
    funding_hist: 由舊到新的資金費率 list（小數，如 0.0001）。"""
    xs = [x for x in funding_hist if x is not None]
    if len(xs) < max(recent + 3, consec + 3):
        return {"triggered": False, "note": "資料不足", "ok": False}
    base_mean = sum(xs) / len(xs)
    base_std = _stdev(xs)
    recent_ma = sum(xs[-recent:]) / recent
    offset = recent_ma - base_mean
    sign = 1 if offset >= 0 else -1
    c = 0
    for v in reversed(xs):
        if (1 if (v - base_mean) >= 0 else -1) == sign:
            c += 1
        else:
            break
    triggered = base_std > 0 and abs(offset) > k_std * base_std and c >= consec
    dirw = "偏高(多方付費重)" if sign > 0 else "偏低(空方付費重)"
    note = ("近 %d 期資金費均 %.4f%% 較基準 %.4f%% %s、連續 %d 期同向 → 資金費異常"
            % (recent, recent_ma * 100, base_mean * 100, dirw, c)) if triggered else \
           ("近 %d 期均 %.4f%% vs 基準 %.4f%%（偏移 %.4f%% 未達 %.1f×σ 或連續不足）"
            % (recent, recent_ma * 100, base_mean * 100, offset * 100, k_std))
    return {"triggered": bool(triggered), "note": note, "ok": True}


def scan(bars, funding_hist):
    """回傳 {count, total, signals, line}。line 固定含 N/2（未觸發為 0/2）與免責句。"""
    lv = low_vol_signal(bars or [])
    fs = funding_signal(funding_hist or [])
    sigs = [
        {"key": "低波動蓄積", **lv},
        {"key": "資金費異常", **fs},
    ]
    hit = [s for s in sigs if s.get("triggered")]
    names = "、".join(s["key"] for s in hit) if hit else "無"
    line = ("目前可自動比對訊號：%d/2 項觸發（%s）｜%s" % (len(hit), names, DISCLAIMER))
    return {"count": len(hit), "total": 2, "signals": sigs, "line": line}


if __name__ == "__main__":
    # 離線自我檢查：造低波動窗 + 資金費持續偏高，確認觸發；再造正常資料確認 0/2。
    import random
    random.seed(3)
    calm = [{"close": 100 + random.uniform(-0.02, 0.02)} for _ in range(60)]      # 極低波動
    noisy = [{"close": 100 * (1 + random.uniform(-0.03, 0.03))} for _ in range(60)]
    calm_win = noisy[:40] + calm[:20]                                             # 前吵後靜 → 尾窗低波動
    fund_hi = [0.0001] * 20 + [0.0009, 0.001, 0.0011, 0.001, 0.0012]              # 尾端持續偏高
    fund_norm = [random.uniform(-0.0003, 0.0003) for _ in range(30)]
    print("低波動(尾靜):", low_vol_signal(calm_win)["triggered"], "| 正常:", low_vol_signal(noisy)["triggered"])
    print("資金費異常:", funding_signal(fund_hi)["triggered"], "| 正常:", funding_signal(fund_norm)["triggered"])
    print(scan(calm_win, fund_hi)["line"])
    print(scan(noisy, fund_norm)["line"])
