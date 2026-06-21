#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共振進出場 — 歷史 K 線回測（驗證網頁圖上那套 🎯進出場 訊號到底有沒有用）

重點：這份 Python 的訊號邏輯與 wo_trade.html 的 chartSignals **逐條對齊**，
而且**只用到第 i 根(含)以前的資料**（無未來函數 / no look-ahead），所以回測＝可實盤。

資料來源（三選一）：
  1) 你的橋接(牛牛個股)：  python backtest_signals.py --bridge http://127.0.0.1:8888 --token <TOKEN> --code US.NVDA --ktype day --num 800
  2) 加密(幣安/Bitget)：    python backtest_signals.py --crypto BTCUSDT --source binance --interval 1d --limit 1000
  3) CSV(time,open,high,low,close,volume)： python backtest_signals.py --csv mydata.csv
進出場規則：進場/做空(順勢＋回踩＋動能＋確認，≥3項且趨勢過濾) → 平多/平空(MACD死叉/金叉 或 破/站回 EMA20)。
費用：--fee 0.04（單邊%）。做多做空都算。輸出勝率、期望值(R)、獲利因子、總報酬、最大回撤，並與 Buy&Hold 比。
"""
import argparse, json, math, urllib.request, urllib.parse, urllib.error


# ---------- 指標（與網頁同公式，全部因果） ----------
def ema(v, p):
    k = 2 / (p + 1); out = []; prev = None
    for i, x in enumerate(v):
        prev = x if i == 0 else x * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(c, p=14):
    out = [None] * len(c)
    if len(c) <= p:
        return out
    g = l = 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]; gg = max(d, 0.0); ll = max(-d, 0.0)
        if i <= p:
            g += gg; l += ll
            if i == p:
                g /= p; l /= p; out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
        else:
            g = (g * (p - 1) + gg) / p; l = (l * (p - 1) + ll) / p
            out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def macd(c, f=12, s=26, sig=9):
    ef, es = ema(c, f), ema(c, s)
    line = [ef[i] - es[i] for i in range(len(c))]
    signal = ema(line, sig)
    hist = [line[i] - signal[i] for i in range(len(c))]
    return line, signal, hist


def boll(c, p=20, k=2):
    up = [None] * len(c); mid = [None] * len(c); low = [None] * len(c)
    for i in range(len(c)):
        if i < p - 1:
            continue
        seg = c[i - p + 1:i + 1]; m = sum(seg) / p
        sd = math.sqrt(sum((x - m) ** 2 for x in seg) / p)
        mid[i] = m; up[i] = m + k * sd; low[i] = m - k * sd
    return up, mid, low


# ---------- 訊號（與 chartSignals 對齊，無未來函數） ----------
def signals(bars):
    c = [b["close"] for b in bars]; o = [b["open"] for b in bars]; n = len(c)
    trades = []
    if n < 60:
        return trades
    e20 = ema(c, 20); e50 = ema(c, 50); rs = rsi(c, 14); _, _, hist = macd(c)
    bu, _, bl = boll(c, 20, 2)
    ml, sgl, _ = macd(c)
    state = "flat"; since = -99; entry = None
    for i in range(55, n):
        slope = (e50[i] - e50[i - 10]) / (c[i] or 1)
        up = c[i] > e50[i] and e20[i] > e50[i] and slope > 0.002
        dn = c[i] < e50[i] and e20[i] < e50[i] and slope < -0.002
        nearE20 = abs(c[i] / e20[i] - 1) <= 0.02
        tagLow = bl[i] is not None and bars[i]["low"] <= bl[i]
        tagUp = bu[i] is not None and bars[i]["high"] >= bu[i]
        rsiUp = rs[i] is not None and rs[i - 1] is not None and rs[i] > rs[i - 1] and rs[i - 1] < 50
        rsiDn = rs[i] is not None and rs[i - 1] is not None and rs[i] < rs[i - 1] and rs[i - 1] > 50
        histUp = hist[i] > hist[i - 1] and hist[i - 1] <= hist[i - 2]
        histDn = hist[i] < hist[i - 1] and hist[i - 1] >= hist[i - 2]
        bull = c[i] > o[i] and c[i] > e20[i]; bear = c[i] < o[i] and c[i] < e20[i]
        ls = sum([up, (nearE20 or tagLow), (rsiUp or histUp), bull])
        ss = sum([dn, (nearE20 or tagUp), (rsiDn or histDn), bear])
        macdDead = ml[i] < sgl[i] and ml[i - 1] >= sgl[i - 1]
        macdGold = ml[i] > sgl[i] and ml[i - 1] <= sgl[i - 1]
        held = i - since
        if state == "flat":
            if up and ls >= 3 and held >= 3:
                state = "long"; since = i; entry = (i, c[i])
            elif dn and ss >= 3 and held >= 3:
                state = "short"; since = i; entry = (i, c[i])
        elif state == "long":
            if (macdDead or c[i] < e20[i]) and held >= 2:
                trades.append({"dir": "long", "i_in": entry[0], "i_out": i, "px_in": entry[1], "px_out": c[i]})
                state = "flat"; since = i
        elif state == "short":
            if (macdGold or c[i] > e20[i]) and held >= 2:
                trades.append({"dir": "short", "i_in": entry[0], "i_out": i, "px_in": entry[1], "px_out": c[i]})
                state = "flat"; since = i
    return trades


# ---------- 回測統計 ----------
def backtest(bars, fee=0.04, name=""):
    fee = fee / 100.0
    tr = signals(bars)
    if not tr:
        print(f"\n[{name}] 沒有任何訊號（資料太短或全程橫盤）。bars={len(bars)}")
        return
    rets = []
    for t in tr:
        if t["dir"] == "long":
            g = t["px_out"] / t["px_in"] - 1
        else:
            g = t["px_in"] / t["px_out"] - 1
        g -= 2 * fee   # 進出各一次手續費
        t["ret"] = g; rets.append(g)
    wins = [r for r in rets if r > 0]; losses = [r for r in rets if r <= 0]
    eq = 1.0; peak = 1.0; mdd = 0.0; curve = []
    for r in rets:
        eq *= (1 + r); peak = max(peak, eq); mdd = min(mdd, eq / peak - 1); curve.append(eq)
    bh = bars[-1]["close"] / bars[0]["close"] - 1
    avg_w = sum(wins) / len(wins) if wins else 0
    avg_l = sum(losses) / len(losses) if losses else 0
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    expR = (sum(rets) / len(rets)) / abs(avg_l) if avg_l else 0   # 期望值(以平均虧損為1R)
    print(f"\n========== [{name}]  K棒 {len(bars)}  交易 {len(tr)} 筆 ==========")
    print(f"  勝率        : {len(wins)/len(tr)*100:5.1f}%   ({len(wins)}勝 / {len(losses)}敗)")
    print(f"  平均獲利    : {avg_w*100:+5.2f}%    平均虧損 : {avg_l*100:+5.2f}%")
    print(f"  期望值      : {expR:+.2f} R/筆   獲利因子 : {pf:.2f}")
    print(f"  策略總報酬  : {(eq-1)*100:+6.1f}%   (複利)")
    print(f"  Buy & Hold  : {bh*100:+6.1f}%")
    print(f"  最大回撤    : {mdd*100:5.1f}%")
    longs = [t for t in tr if t['dir'] == 'long']; shorts = [t for t in tr if t['dir'] == 'short']
    print(f"  多單 {len(longs)} 筆 / 空單 {len(shorts)} 筆")
    print("  最近 6 筆:")
    for t in tr[-6:]:
        print(f"    {t['dir']:<5} 進 {t['px_in']:.2f} → 出 {t['px_out']:.2f}  {t['ret']*100:+6.2f}%  (持 {t['i_out']-t['i_in']} 根)")
    if len(bars) < 250 or len(tr) < 25:
        print("\n  ⚠️ 樣本太小（K棒 < 250 或 交易 < 25 筆），這些數字「沒有統計意義」，別當真。")
        print("     改用長歷史資料：BTCUSDT/ETHUSDT(幣安有數年) 或 透過橋接測真實個股(US.MU/US.NVDA…)。")


# ---------- 取資料 ----------
def _die(msg):
    import sys
    print("\n❌ " + msg)
    sys.exit(1)


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=25).read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        _die(f"伺服器回 HTTP {e.code}（多半是代碼打錯或該交易所沒這檔）。{body}")
    except Exception as e:
        _die(f"連不上網路或請求逾時：{e}\n   （在自己電腦跑、要能上網；幣安/Bitget 在某些地區需翻牆）")
    try:
        return json.loads(raw)
    except Exception:
        _die("回傳的不是 JSON，可能被防火牆/代理擋了。")


def from_bridge(url, token, code, ktype, num):
    q = urllib.parse.urlencode({"code": code, "ktype": ktype, "num": num, "live": 0, "token": token})
    r = _get_json(url.rstrip("/") + "/kline?" + q)
    if isinstance(r, dict) and r.get("error"):
        _die(f"橋接回錯誤：{r.get('error')}（檢查 --token 對不對、牛牛有沒有開）")
    bars = [b for b in (r.get("kline") or []) if b.get("close") is not None] if isinstance(r, dict) else []
    if not bars:
        _die(f"橋接沒給到 {code} 的 K 線（檢查代碼像 US.NVDA、牛牛已登入、--num 不要太大）。")
    return bars


def from_crypto(sym, source, interval, limit):
    """自己抓幣安/Bitget 真實 K 線：大小寫都吃、日線/週線正確、不依賴其他檔。"""
    source = (source or "binance").strip().lower()
    sym = (sym or "").strip().upper()
    if source not in ("binance", "bitget"):
        _die(f"--source 只能填 binance 或 bitget，你填了「{source}」。")
    if source == "bitget":
        gran = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H",
                "4h": "4H", "1d": "1D", "1w": "1W"}.get(interval, "1D")
        url = (f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}"
               f"&productType=usdt-futures&granularity={gran}&limit={limit}")
        resp = _get_json(url)
        d = (resp.get("data") if isinstance(resp, dict) else None) or []
        if isinstance(resp, dict) and str(resp.get("code", "00000")) not in ("00000", "0") and not d:
            _die(f"Bitget 回錯誤：{resp.get('msg')}（代碼像 BTCUSDT，且要是 USDT 永續）。")
        bars = [{"time": r[0], "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                 "close": float(r[4]), "volume": float(r[5])} for r in d]
        bars.sort(key=lambda b: int(b["time"]))   # 由舊到新
    else:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={interval}&limit={limit}"
        d = _get_json(url)
        if isinstance(d, dict):   # 幣安出錯時回 {"code":-1121,"msg":"Invalid symbol."}
            _die(f"幣安回錯誤：{d.get('msg')}（代碼像 BTCUSDT；--interval 只能 1m/5m/15m/1h/4h/1d/1w）。")
        bars = [{"time": k[0], "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                 "close": float(k[4]), "volume": float(k[5])} for k in d]
    if not bars:
        _die(f"{source} 沒給到 {sym} 的 K 線（檢查代碼/來源/interval）。")
    return bars


def from_csv(path):
    out = []
    for ln in open(path, encoding="utf-8"):
        p = ln.strip().split(",")
        if len(p) < 5 or not p[1].replace(".", "").replace("-", "").isdigit():
            continue
        out.append({"time": p[0], "open": float(p[1]), "high": float(p[2]),
                    "low": float(p[3]), "close": float(p[4]),
                    "volume": float(p[5]) if len(p) > 5 else 0})
    return out


def synth(n=600, seed=7):
    """多空＋橫盤混合的合成資料，驗證邏輯正確性（非真實 edge）。"""
    import random
    random.seed(seed); px = 100.0; bars = []
    regimes = [("up", 130, 0.45), ("chop", 110, 0.0), ("down", 130, -0.45),
               ("chop", 90, 0.0), ("up", 140, 0.5)]
    i = 0
    for kind, length, drift in regimes:
        for _ in range(length):
            px += drift + random.uniform(-1.2, 1.2)
            px = max(px, 1)
            hi = px + abs(random.uniform(0, 1.0)); lo = px - abs(random.uniform(0, 1.0))
            op = px - random.uniform(-0.5, 0.5)
            bars.append({"time": f"t{i}", "open": op, "high": max(hi, op, px),
                         "low": min(lo, op, px), "close": px, "volume": random.uniform(800, 1500)})
            i += 1
    return bars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge"); ap.add_argument("--token", default="")
    ap.add_argument("--code", default="US.NVDA"); ap.add_argument("--ktype", default="day"); ap.add_argument("--num", type=int, default=800)
    ap.add_argument("--crypto"); ap.add_argument("--source", default="binance"); ap.add_argument("--interval", default="1d"); ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--csv"); ap.add_argument("--fee", type=float, default=0.04)
    ap.add_argument("--synth", action="store_true")
    a = ap.parse_args()
    if a.bridge:
        bars = from_bridge(a.bridge, a.token, a.code, a.ktype, a.num); backtest(bars, a.fee, f"{a.code} {a.ktype}")
    elif a.crypto:
        bars = from_crypto(a.crypto, a.source, a.interval, a.limit); backtest(bars, a.fee, f"{a.crypto} {a.interval}")
    elif a.csv:
        bars = from_csv(a.csv); backtest(bars, a.fee, a.csv)
    else:
        # 預設跑合成資料驗證邏輯
        backtest(synth(), a.fee, "SYNTH 多空+橫盤混合")


if __name__ == "__main__":
    main()
