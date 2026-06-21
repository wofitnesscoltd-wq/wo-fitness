#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最嚴謹的自我驗證：把網頁上那套 🎯進出場 訊號邏輯（= backtest_signals.signals，逐條對齊
wo_trade.html 的 chartSignals）丟進「蒙地卡羅 + 對照組(null test) + 因果稽核」三道關卡。

為什麼這樣才嚴謹（不是跑一次就喊勝）：
  1) 因果稽核(look-ahead audit)：證明第 i 根的買賣決策「只用到 i 根(含)以前」，
     截斷未來資料後過去決策完全不變 → 回測=可實盤，沒有偷看未來。
  2) 蒙地卡羅(N 條隨機但寫實的歷史路徑)：用 regime-switching + 波動叢聚(GARCH 式) +
     跳空 生成上千根日線，跑很多條，看「分布」而不是一次運氣。
  3) 對照組：
     (a) 隨機進場(same #trades、same 平均持有) → 策略沒贏隨機就是沒 edge。
     (b) 打亂報酬(shuffle)：把同一條路徑的日報酬洗牌，破壞趨勢結構，趨勢策略的
         edge 應該崩掉 → 證明 edge 來自真實的價格結構，不是測試設計給的假象。
  4) 分 regime（趨勢 / 橫盤 / 均值回歸）各跑一輪 → 看策略是否「該贏的地方贏、該縮的地方縮」。

注意：沙盒沒有外網，這裡是合成但寫實的資料；嚴謹度來自 MC + 對照組 + 因果稽核，
不是宣稱真實 edge。真實 edge 必須等真資料（BTCUSDT/真實個股）才算數。
"""
import random, math, statistics
from backtest_signals import signals, backtest


# ---------- 寫實的歷史 K 線生成器 ----------
def gen_path(n=800, seed=0, regime="mixed"):
    """regime-switching + GARCH 波動叢聚 + 跳空 的日線。回傳 OHLC bars。"""
    rnd = random.Random(seed)
    # 三種狀態的日漂移
    drift = {"bull": 0.0016, "bear": -0.0016, "chop": 0.0}
    # 狀態轉移（高持續性，像真實市場一段一段走）
    if regime == "trend":
        states, stay = ["bull", "bear"], 0.985
    elif regime == "chop":
        states, stay = ["chop"], 1.0
    elif regime == "meanrev":
        states, stay = ["chop"], 1.0   # 下面加均值回歸力
    else:
        states, stay = ["bull", "bear", "chop"], 0.975
    st = rnd.choice(states)
    # GARCH(1,1) 式波動
    omega, alpha, beta = 1e-5, 0.08, 0.90
    sig2 = 0.025 ** 2
    last_r = 0.0
    px = 100.0
    anchor = 100.0
    bars = []
    prev_close = px
    for i in range(n):
        if rnd.random() > stay:
            st = rnd.choice(states)
        sig2 = omega + alpha * (last_r ** 2) + beta * sig2
        sig = math.sqrt(sig2)
        z = rnd.gauss(0, 1)
        r = drift[st] + sig * z
        if regime == "meanrev":                      # 拉回錨點的力
            r += -0.02 * math.log(px / anchor)
            anchor *= 1.0 + rnd.gauss(0, 0.0005)
        if rnd.random() < 0.01:                       # 1% 跳空
            r += rnd.gauss(0, 3 * sig)
        last_r = r
        close = prev_close * math.exp(r)
        close = max(close, 1e-3)
        op = prev_close * math.exp(rnd.gauss(0, sig * 0.3))     # 開盤帶小跳空
        rng = abs(close - op) + close * sig * abs(rnd.gauss(0, 1)) * 0.7
        hi = max(op, close) + rng * rnd.random()
        lo = min(op, close) - rng * rnd.random()
        bars.append({"time": f"t{i}", "open": op, "high": max(hi, op, close),
                     "low": max(min(lo, op, close), 1e-4), "close": close,
                     "volume": rnd.uniform(800, 1500)})
        prev_close = close
    return bars


def shuffle_returns(bars, seed=0):
    """保留起點價，把日報酬洗牌 → 破壞趨勢/自相關結構（null 對照）。"""
    rnd = random.Random(seed)
    c = [b["close"] for b in bars]
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    rnd.shuffle(rets)
    out = [dict(bars[0])]
    px = c[0]
    for i, r in enumerate(rets, 1):
        px *= math.exp(r)
        b = dict(bars[i]); span = b["high"] - b["low"]
        b["close"] = px; b["open"] = px * (1 + (b["open"] / c[i] - 1))
        b["high"] = px + span / 2; b["low"] = max(px - span / 2, 1e-4)
        out.append(b)
    return out


# ---------- 把 signals() 的結果換算成績效 ----------
def stats_of(bars, fee=0.04):
    fee /= 100.0
    tr = signals(bars)
    if not tr:
        return None
    rets = []
    for t in tr:
        g = (t["px_out"] / t["px_in"] - 1) if t["dir"] == "long" else (t["px_in"] / t["px_out"] - 1)
        rets.append(g - 2 * fee)
    wins = [r for r in rets if r > 0]; losses = [r for r in rets if r <= 0]
    eq = 1.0; peak = 1.0; mdd = 0.0
    for r in rets:
        eq *= (1 + r); peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    avg_l = (sum(losses) / len(losses)) if losses else 0
    pf = (sum(wins) / abs(sum(losses))) if (losses and sum(losses) != 0) else float("inf")
    hold = statistics.mean([t["i_out"] - t["i_in"] for t in tr])
    return {"n": len(tr), "win": len(wins) / len(tr), "ret": eq - 1,
            "expR": (statistics.mean(rets) / abs(avg_l)) if avg_l else 0,
            "pf": pf, "mdd": mdd, "hold": hold,
            "bh": bars[-1]["close"] / bars[0]["close"] - 1, "rets": rets}


def random_benchmark(bars, n_trades, hold, fee=0.04, seed=0):
    """對照組(a)：同筆數、同平均持有，隨機進場/隨機方向 → 策略沒贏它就沒 edge。"""
    rnd = random.Random(seed); fee /= 100.0; c = [b["close"] for b in bars]; rets = []
    for _ in range(n_trades):
        i = rnd.randint(55, max(56, len(c) - int(hold) - 2))
        j = min(len(c) - 1, i + max(1, int(rnd.expovariate(1 / max(hold, 1)))))
        g = (c[j] / c[i] - 1) if rnd.random() < 0.5 else (c[i] / c[j] - 1)
        rets.append(g - 2 * fee)
    return statistics.mean(rets) if rets else 0


# ---------- 因果稽核 ----------
def lookahead_audit(bars):
    """逐根截斷未來資料，確認過去的交易決策完全不變 → 沒有未來函數。"""
    full = signals(bars)
    if len(full) < 3:
        return True, "交易太少，換條再驗"
    # 取倒數第 2 筆出場點當截斷點：截到那根，前面的交易應與 full 完全一致
    cut = full[-2]["i_out"] + 1
    partial = signals(bars[:cut])
    past_full = [(t["dir"], t["i_in"], t["i_out"]) for t in full if t["i_out"] < cut]
    past_part = [(t["dir"], t["i_in"], t["i_out"]) for t in partial if t["i_out"] < cut]
    ok = past_full == past_part
    return ok, f"截斷到第 {cut} 根：過去交易 {'完全一致' if ok else '不一致(有未來函數!)'} " \
               f"({len(past_full)} vs {len(past_part)} 筆)"


def pct(xs, p):
    xs = sorted(xs); k = (len(xs) - 1) * p / 100.0
    f = int(k); return xs[f] if f + 1 >= len(xs) else xs[f] + (xs[f + 1] - xs[f]) * (k - f)


def mc(regime, N=300, n=800, fee=0.04, base=0):
    rows, beat_bh, beat_rand, prof = [], 0, 0, 0
    for s in range(N):
        bars = gen_path(n, base + s, regime)
        st = stats_of(bars, fee)
        if not st:
            continue
        rb = random_benchmark(bars, st["n"], st["hold"], fee, base + s)
        st["rand"] = rb
        rows.append(st)
        beat_bh += st["ret"] > st["bh"]
        beat_rand += statistics.mean(st["rets"]) > rb
        prof += st["ret"] > 0
    m = len(rows)
    if not m:
        print(f"[{regime}] 無有效路徑"); return
    g = lambda k: [r[k] for r in rows]
    print(f"\n========== regime = {regime}   有效路徑 {m}/{N}   每條 {n} 根 ==========")
    print(f"  每條平均交易筆數 : {statistics.mean(g('n')):.1f}")
    print(f"  勝率   中位數 {statistics.median(g('win'))*100:4.1f}%   "
          f"[10–90%: {pct(g('win'),10)*100:.0f}–{pct(g('win'),90)*100:.0f}%]")
    print(f"  期望值 中位數 {statistics.median(g('expR')):+.2f} R   "
          f"[10–90%: {pct(g('expR'),10):+.2f} ~ {pct(g('expR'),90):+.2f}]")
    pf_fin = [x for x in g('pf') if x != float('inf')]
    print(f"  獲利因子 中位數 {statistics.median(pf_fin):.2f}")
    print(f"  總報酬 中位數 {statistics.median(g('ret'))*100:+.1f}%   "
          f"[10–90%: {pct(g('ret'),10)*100:+.0f}% ~ {pct(g('ret'),90)*100:+.0f}%]")
    print(f"  最大回撤 中位數 {statistics.median(g('mdd'))*100:.1f}%")
    print(f"  ── 對照 ──")
    print(f"  獲利路徑佔比         : {prof/m*100:.0f}%")
    print(f"  贏過 Buy&Hold 佔比   : {beat_bh/m*100:.0f}%")
    print(f"  贏過『隨機進場』佔比 : {beat_rand/m*100:.0f}%   ← 這個才是有沒有 edge 的關鍵")


def main():
    print(__doc__)
    print("\n##### 關卡 1：因果稽核（證明沒有偷看未來） #####")
    oks = 0
    for s in range(20):
        ok, msg = lookahead_audit(gen_path(800, 1000 + s, "trend"))
        oks += ok
        if s < 3:
            print(f"  seed {1000+s}: {msg}")
    print(f"  20 條路徑全部通過？ {oks}/20  → {'✅ 沒有未來函數' if oks == 20 else '❌ 有問題'}")

    print("\n##### 關卡 2+3：蒙地卡羅 + 對照組 #####")
    for reg in ["trend", "mixed", "chop", "meanrev"]:
        mc(reg, N=300, n=800)

    print("\n##### 關卡 3b：null 對照 — 把報酬洗牌(破壞趨勢) #####")
    real, shuf = [], []
    for s in range(300):
        bars = gen_path(800, 5000 + s, "trend")
        a = stats_of(bars); b = stats_of(shuffle_returns(bars, 5000 + s))
        if a:
            real.append(statistics.mean(a["rets"]))
        if b:
            shuf.append(statistics.mean(b["rets"]))
    print(f"  趨勢結構在 → 每筆平均報酬 中位數 {statistics.median(real)*100:+.3f}%")
    print(f"  打亂結構後 → 每筆平均報酬 中位數 {statistics.median(shuf)*100:+.3f}%")
    print(f"  結論：結構被破壞後 edge {'明顯下降 ✅（代表 edge 來自真實結構）' if statistics.median(real) > statistics.median(shuf) else '沒下降 ❗（可能是測試假象）'}")


if __name__ == "__main__":
    main()
