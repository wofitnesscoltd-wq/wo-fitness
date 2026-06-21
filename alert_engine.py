#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窩 Trading 大腦 — 警示引擎（Phase 2）
------------------------------------------------------------------
常駐在本機、開盤期間自動掃描自選清單（買點）與持股（賣點），
每則訊號做技術評分(A/B/C)＋逐項佐證，去重後推 Telegram，並全部寫入
point-in-time 的 SQLite，供之後的結果追蹤與自我優化（Phase 3/5）。

設計原則（與 ARCHITECTURE.md 一致）：
  • 只讀、不下單；最後一鍵永遠是人。
  • 程序重於結果：每則訊號存「觸發當下的完整快照」，方便事後歸因。
  • 環境決定打法：訊號帶方向、等級、停損/目標/風險報酬比。

本模組「不」直接 import futu。資料由 futu_bridge.py 以 callback 餵進來，
因此可單獨匯入/測試（無 SDK 也能 import）。
"""
import json, os, sqlite3, threading, time, urllib.request, urllib.parse
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover
    ET = None

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "wo_alerts.db")
WATCH_PATH = os.path.join(HERE, "wo_watch.json")


# ====================================================================
# 指標（純函式，stdlib，無 numpy）
# ====================================================================
def ema(vals, p):
    if not vals:
        return []
    k = 2.0 / (p + 1)
    out, prev = [], None
    for i, v in enumerate(vals):
        prev = v if i == 0 else v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(closes, p=14):
    out = [None] * len(closes)
    if len(closes) <= p:
        return out
    g = l = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain, loss = max(d, 0.0), max(-d, 0.0)
        if i <= p:
            g += gain; l += loss
            if i == p:
                g /= p; l /= p
                out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
        else:
            g = (g * (p - 1) + gain) / p
            l = (l * (p - 1) + loss) / p
            out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def macd(closes, f=12, s=26, sig=9):
    ef, es = ema(closes, f), ema(closes, s)
    line = [ef[i] - es[i] for i in range(len(closes))]
    signal = ema(line, sig)
    hist = [line[i] - signal[i] for i in range(len(closes))]
    return line, signal, hist


def atr(bars, p=14):
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < p:
        return sum(trs) / len(trs) if trs else None
    a = sum(trs[:p]) / p
    for t in trs[p:]:
        a = (a * (p - 1) + t) / p
    return a


def session_vwap(bars):
    """分盤重置 VWAP，回傳與 bars 等長的序列。"""
    out, pv, vv, day = [], 0.0, 0.0, None
    for b in bars:
        d = (b.get("time") or "")[:10]
        if d != day:
            day, pv, vv = d, 0.0, 0.0
        tp = (b["high"] + b["low"] + b["close"]) / 3.0
        v = b.get("volume") or 0
        pv += tp * v; vv += v
        out.append(pv / vv if vv > 0 else b["close"])
    return out


def rvol(bars, lookback=20):
    """最後一根量 / 前 lookback 根均量。"""
    vols = [b.get("volume") or 0 for b in bars]
    if len(vols) < lookback + 1:
        return None
    base = sum(vols[-lookback - 1:-1]) / lookback
    return (vols[-1] / base) if base > 0 else None


def opening_range(bars, mins=30, bar_min=5):
    """當日開盤區間 (high, low)；bars 為當日。"""
    n = max(1, mins // bar_min)
    day = (bars[-1].get("time") or "")[:10] if bars else None
    todays = [b for b in bars if (b.get("time") or "")[:10] == day]
    seg = todays[:n]
    if not seg:
        return None, None
    return max(b["high"] for b in seg), min(b["low"] for b in seg)


# ====================================================================
# 訊號評估
# ====================================================================
def _feat(bars5):
    closes = [b["close"] for b in bars5]
    e9, e20, e50 = ema(closes, 9), ema(closes, 20), ema(closes, 50)
    r = rsi(closes, 14)
    ml, ms, mh = macd(closes)
    vw = session_vwap(bars5)
    return {
        "close": closes[-1], "prev": closes[-2] if len(closes) > 1 else closes[-1],
        "e9": e9[-1], "e20": e20[-1], "e50": e50[-1],
        "e9p": e9[-2] if len(e9) > 1 else e9[-1], "e20p": e20[-2] if len(e20) > 1 else e20[-1],
        "rsi": r[-1], "rsi_p": r[-2] if len(r) > 1 else None,
        "macd": ml[-1], "sig": ms[-1], "hist": mh[-1],
        "hist_p": mh[-2] if len(mh) > 1 else 0.0,
        "vwap": vw[-1], "vwap_p": vw[-2] if len(vw) > 1 else vw[-1],
        "atr": atr(bars5, 14), "rvol": rvol(bars5, 20),
    }


def _grade(conf, f, daily_aligned):
    """等級＝匯流條件數 × RVOL × 與日線同向。"""
    score = conf
    rv = f.get("rvol") or 1.0
    if rv >= 1.8:
        score += 1
    if daily_aligned:
        score += 1
    return "A" if score >= 4 else "B" if score >= 3 else "C"


def _plan(side, f):
    """用 ATR 反推停損/目標/風險報酬。"""
    px, a = f["close"], f.get("atr") or (f["close"] * 0.01)
    if side == "long":
        stop = round(min(f["vwap"], px - 1.5 * a), 2)
        risk = max(px - stop, a * 0.5)
        target = round(px + 2 * risk, 2)
    else:  # short / exit reference
        stop = round(max(f["vwap"], px + 1.5 * a), 2)
        risk = max(stop - px, a * 0.5)
        target = round(px - 2 * risk, 2)
    rr = round(abs(target - px) / risk, 1) if risk else None
    return stop, target, rr


# ====================================================================
# 多流派集成（§2B）：權重為先驗，可由走動式優化學出來（存 wo_weights.json）
# ====================================================================
DEFAULT_WEIGHTS = {
    "trend": 0.25, "breakout": 0.20, "vwap": 0.18, "meanrev": 0.12,
    "sr": 0.10, "ma": 0.08, "seasonality": 0.03, "other": 0.04,
}
WEIGHTS_PATH = os.path.join(HERE, "wo_weights.json")
SCHOOL_LABEL = {"trend": "趨勢/動能", "breakout": "突破", "vwap": "VWAP/量價",
                "meanrev": "均值回歸", "sr": "支撐反彈", "ma": "均線多頭"}


def load_weights():
    try:
        w = json.load(open(WEIGHTS_PATH, encoding="utf-8"))
        return {k: float(w.get(k, v)) for k, v in DEFAULT_WEIGHTS.items()}
    except Exception:
        return dict(DEFAULT_WEIGHTS)


def save_weights(w):
    json.dump(w, open(WEIGHTS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def schools_long(bars5, f):
    """各流派對『做多』的 0..1 子分數；回傳 (scores, triggered)。沒有真實觸發 → triggered 為空。"""
    s = {k: 0.0 for k in DEFAULT_WEIGHTS}
    closes = [b["close"] for b in bars5]
    rv = f.get("rvol") or 1.0
    up_stack = f["e9"] > f["e20"] > f["e50"]
    above_vwap = f["close"] > f["vwap"]
    triggered = []

    # 趨勢/動能：均線多頭排列＋價在均線上＋MACD 多方
    s["trend"] = (0.5 * (1 if up_stack else 0) + 0.3 * (1 if f["close"] > f["e20"] else 0)
                  + 0.2 * (1 if f["hist"] > 0 else 0))
    s["ma"] = 1.0 if f["e20"] > f["e50"] else 0.0

    # 突破：開盤區間 / 近 20 根高，帶量
    orh, _orl = opening_range(bars5)
    if orh and f["prev"] <= orh < f["close"]:
        s["breakout"] = _clamp(0.6 + (rv - 1) * 0.3, 0, 1); triggered.append("帶量突破開盤區間高")
    recent_high = max(closes[-21:-1]) if len(closes) > 21 else None
    if recent_high and f["prev"] <= recent_high < f["close"]:
        s["breakout"] = max(s["breakout"], _clamp(0.5 + (rv - 1) * 0.3, 0, 1))
        if "突破" not in "".join(triggered): triggered.append("突破近期高點")

    # VWAP/量價：站回或量價守住
    if f["prev"] <= f["vwap_p"] and f["close"] > f["vwap"]:
        s["vwap"] = _clamp(0.6 + (rv - 1) * 0.2, 0, 1); triggered.append("站回 VWAP")
    elif above_vwap and rv >= 1.3:
        s["vwap"] = _clamp(0.4 + (rv - 1) * 0.2, 0, 1)

    # 均值回歸：RSI 由超賣翻揚
    if f["rsi_p"] is not None and f["rsi_p"] < 35 and f["rsi"] > f["rsi_p"]:
        s["meanrev"] = _clamp((35 - f["rsi_p"]) / 20 + 0.4, 0, 1)
        if f["rsi_p"] < 30 <= f["rsi"]: triggered.append("RSI 由超賣翻揚")

    # 支撐反彈：觸近 20 根低點後反彈
    recent_low = min(closes[-21:-1]) if len(closes) > 21 else None
    if recent_low and f["prev"] <= recent_low * 1.01 and f["close"] > f["prev"]:
        s["sr"] = 0.6; triggered.append("關鍵支撐反彈")

    return s, triggered


def eval_buy(bars5, daily_chg=None, bars15=None, ctx=None, weights=None):
    """多流派加權集成的買點。回傳 0 或 1 則（綜合成單一信心分數）。"""
    if len(bars5) < 30:
        return []
    f = _feat(bars5)
    if f["rsi"] is None:
        return []
    weights = weights or load_weights()
    ctx = ctx or {}
    s, triggered = schools_long(bars5, f)
    if not triggered:
        return []                                   # 沒有真實觸發就不發

    base = sum(weights[k] * s[k] for k in weights)  # 0..~1

    # 多週期匯流：15 分趨勢同向加成 / 背離降級
    mt_mult, mt_note = 1.0, ""
    if bars15 and len(bars15) >= 30:
        f15 = _feat(bars15)
        if f15["rsi"] is not None:
            if f15["e9"] > f15["e20"] > f15["e50"] and f15["close"] > f15["vwap"]:
                mt_mult, mt_note = 1.2, "，15分同向多頭"
            elif f15["close"] < f15["e20"]:
                mt_mult, mt_note = 0.8, "，15分偏弱降級"

    # 相對強度：個股 vs SPY 近段報酬
    rs_mult, spy_ret = 1.0, ctx.get("spy_ret")
    closes = [b["close"] for b in bars5]
    if spy_ret is not None and len(closes) >= 13 and closes[-13]:
        stock_ret = (closes[-1] / closes[-13] - 1) * 100
        rs_mult = _clamp(1 + (stock_ret - spy_ret) * 0.03, 0.8, 1.25)

    rv = f.get("rvol") or 1.0
    rvol_mult = _clamp(1 + (rv - 1) * 0.1, 0.9, 1.3)

    # 閘門：市場 regime / 流動性 / 日線逆向
    regime = ctx.get("regime", "neutral")
    regime_gate = {"risk_on": 1.0, "neutral": 0.9, "risk_off": 0.6}.get(regime, 0.9)
    liq_gate = 0.5 if rv < 0.5 else 1.0
    daily_gate = 0.85 if (daily_chg is not None and daily_chg < -1) else 1.0

    conviction = _clamp(base * mt_mult * rs_mult * rvol_mult * regime_gate * liq_gate * daily_gate, 0, 1)
    grade = ("A" if conviction >= 0.68 else "B" if conviction >= 0.5
             else "C" if conviction >= 0.36 else None)
    if grade is None:
        return []

    stop, target, rr = _plan("long", f)
    contributors = sorted([k for k in s if s[k] >= 0.5 and k in SCHOOL_LABEL],
                          key=lambda k: -s[k] * weights[k])
    type_str = "＋".join(SCHOOL_LABEL[k] for k in contributors) or "多訊號匯流"
    reason = "；".join(triggered) + f"；綜合信心 {conviction:.2f}（regime={regime}{mt_note}，RS×{rs_mult:.2f}，RVOL {rv:.1f}）"
    return [{
        "side": "long", "type": type_str, "grade": grade,
        "price": round(f["close"], 2), "stop": stop, "target": target, "rr": rr,
        "reason": reason, "conviction": round(conviction, 3),
        "scores": {k: round(s[k], 2) for k in s if s[k] > 0},
        "feat": _round_feat(f),
    }]


def eval_sell(bars5, pos=None, ctx=None):
    """回傳持股賣點/減碼訊號。risk-off 時更敏感（升級）。"""
    if len(bars5) < 30:
        return []
    f = _feat(bars5)
    if f["rsi"] is None:
        return []
    ctx = ctx or {}
    triggers = []
    if f["prev"] >= f["vwap_p"] and f["close"] < f["vwap"]:
        triggers.append(("跌破 VWAP", "由上跌破 VWAP，盤中轉弱"))
    if f["e9p"] >= f["e20p"] and f["e9"] < f["e20"]:
        triggers.append(("均線死叉", "EMA9 下穿 EMA20，動能轉弱"))
    if f["hist_p"] >= 0 > f["hist"]:
        triggers.append(("MACD 死叉", "MACD 柱由正翻負"))
    if f["rsi_p"] is not None and f["rsi_p"] > 70 >= f["rsi"]:
        triggers.append(("RSI 超買回落", "RSI 由 >70 回落，過熱降溫"))
    if f["close"] < f["e50"]:
        triggers.append(("跌破 EMA50", "失守 EMA50，趨勢轉弱"))
    if not triggers:
        return []
    stop, target, rr = _plan("short", f)
    n = len(triggers)
    if ctx.get("regime") == "risk_off":     # 大盤轉弱：賣訊更敏感，升一級
        n += 1
    grade = "A" if n >= 3 else "B" if n == 2 else "C"
    note = ""
    if ctx.get("regime") == "risk_off":
        note += "；大盤 risk-off 全面降槓桿"
    if pos and pos.get("pl_ratio") is not None:
        note += f"；目前部位損益 {pos.get('pl_ratio')}%"
    return [{
        "side": "exit", "type": "＋".join(t[0] for t in triggers),
        "grade": grade, "price": round(f["close"], 2), "stop": stop, "target": target, "rr": rr,
        "reason": "；".join(t[1] for t in triggers) + note,
        "feat": _round_feat(f),
    }]


def _round_feat(f):
    out = {}
    for k, v in f.items():
        out[k] = round(v, 3) if isinstance(v, float) else v
    return out


# ====================================================================
# Telegram
# ====================================================================
def send_telegram(token, chat_id, text):
    if not token or not chat_id:
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


def ai_vet(sym, sig, key, model="claude-haiku-4-5-20251001"):
    """用 Claude 對單一訊號做全情境複核。回傳 {verdict, note} 或 None(失敗→放行)。
    verdict ∈ 進場 / 觀望 / 不建議。失敗一律 fail-open，避免 AI 當機就全靜音。"""
    if not key:
        return None
    f = sig.get("feat", {})
    side = {"long": "買進(做多)", "exit": "賣出/減碼"}.get(sig["side"], sig["side"])
    user = (
        f"短線美股訊號複核。標的 {sym}，方向 {side}，技術型態：{sig['type']}。\n"
        f"現價 {sig['price']}，建議停損 {sig['stop']}，目標 {sig['target']}，R:R {sig.get('rr')}。\n"
        f"指標快照：RSI={f.get('rsi')} MACD柱={f.get('hist')} VWAP={f.get('vwap')} "
        f"EMA9/20/50={f.get('e9')}/{f.get('e20')}/{f.get('e50')} RVOL={f.get('rvol')} ATR={f.get('atr')}。\n"
        f"觸發理由：{sig['reason']}\n"
        "請以頂尖短線操盤手角度，判斷這個觸發此刻『值不值得照計畫進場/出場』。"
        "只看技術與風險合理性即可（不需上網）。只輸出 JSON："
        '{"verdict":"進場/觀望/不建議","note":"一句話理由(繁中,精簡)"}'
    )
    body = json.dumps({
        "model": model, "max_tokens": 300,
        "system": "你是嚴格的短線交易風控複核員，寧可錯過不要做錯；型態勉強、追高、逆勢、量能不足就降級為觀望或不建議。只輸出要求的 JSON。",
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        txt = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        m = txt[txt.find("{"): txt.rfind("}") + 1]
        o = json.loads(m)
        return {"verdict": o.get("verdict", "觀望"), "note": o.get("note", "")}
    except Exception:
        return None


def fmt_msg(sym, sig):
    side = {"long": "🟢 買點", "short": "🔴 賣點", "exit": "🔴 賣點/減碼"}.get(sig["side"], sig["side"])
    f = sig.get("feat", {})
    lines = [
        f"<b>{side}・{sym}</b>　等級 <b>{sig['grade']}</b>",
        f"型態：{sig['type']}",
        f"現價 {sig['price']}　停損 {sig['stop']}　目標 {sig['target']}　R:R {sig.get('rr')}",
        f"RSI {f.get('rsi')}　MACD柱 {f.get('hist')}　VWAP {f.get('vwap')}　RVOL {f.get('rvol')}",
        f"理由：{sig['reason']}",
        "⚠️ 非投資建議；務必設停損，最後一鍵是你。",
    ]
    return "\n".join(lines)


# ====================================================================
# SQLite（point-in-time，可稽核）
# ====================================================================
def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session TEXT, symbol TEXT,
        side TEXT, type TEXT, grade TEXT, price REAL, stop REAL, target REAL, rr REAL,
        reason TEXT, snapshot TEXT, pushed INTEGER DEFAULT 0)""")
    con.execute("""CREATE TABLE IF NOT EXISTS outcomes(
        alert_id INTEGER PRIMARY KEY, status TEXT, mfe REAL, mae REAL,
        last_px REAL, resolved_ts TEXT, updated TEXT)""")
    return con


def already_alerted(con, session, symbol, side, type_):
    cur = con.execute(
        "SELECT 1 FROM alerts WHERE session=? AND symbol=? AND side=? AND type=? LIMIT 1",
        (session, symbol, side, type_))
    return cur.fetchone() is not None


def log_alert(con, session, symbol, sig, pushed):
    con.execute(
        """INSERT INTO alerts(ts,session,symbol,side,type,grade,price,stop,target,rr,reason,snapshot,pushed)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now().isoformat(timespec="seconds"), session, symbol, sig["side"], sig["type"],
         sig["grade"], sig["price"], sig["stop"], sig["target"], sig.get("rr"), sig["reason"],
         json.dumps(sig.get("feat", {}), ensure_ascii=False), 1 if pushed else 0))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT OR IGNORE INTO outcomes(alert_id,status,mfe,mae,last_px,updated) VALUES(?,?,?,?,?,?)",
                (aid, "open", 0.0, 0.0, sig["price"], datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return aid


def track_outcomes(con, price_of):
    """對未結算的訊號更新 MFE/MAE、是否先碰目標或停損。price_of: symbol->last。"""
    rows = con.execute("""SELECT a.id,a.symbol,a.side,a.price,a.stop,a.target,o.mfe,o.mae
                          FROM alerts a JOIN outcomes o ON a.id=o.alert_id
                          WHERE o.status='open'""").fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for aid, sym, side, entry, stop, target, mfe, mae in rows:
        px = price_of.get(sym)
        if px is None:
            continue
        long = side == "long"
        move = (px - entry) if long else (entry - px)
        mfe = max(mfe or 0, move); mae = min(mae or 0, move)
        status = "open"
        if long:
            if px >= target: status = "hit"
            elif px <= stop: status = "miss"
        else:
            if px <= target: status = "hit"
            elif px >= stop: status = "miss"
        con.execute("""UPDATE outcomes SET status=?,mfe=?,mae=?,last_px=?,updated=?,
                       resolved_ts=CASE WHEN ?<>'open' THEN ? ELSE resolved_ts END WHERE alert_id=?""",
                    (status, mfe, mae, px, now, status, now, aid))
    con.commit()


# ====================================================================
# 市場時段
# ====================================================================
def market_phase(now=None):
    """回傳 'pre' / 'regular' / 'post' / 'closed'（美東）。"""
    if ET is None:
        now = now or datetime.now()
    else:
        now = (now or datetime.now(ET)).astimezone(ET)
    if now.weekday() >= 5:
        return "closed"
    hm = now.hour * 60 + now.minute
    if 4 * 60 <= hm < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= hm < 16 * 60:
        return "regular"
    if 16 * 60 <= hm < 20 * 60:
        return "post"
    return "closed"


def session_key(now=None):
    if ET is not None:
        now = (now or datetime.now(ET)).astimezone(ET)
    else:
        now = now or datetime.now()
    return now.strftime("%Y-%m-%d")


# ====================================================================
# 引擎主體
# ====================================================================
class AlertEngine:
    """
    providers:
      get_kline(code, ktype, num) -> [bar,...]
      get_snapshot(codes) -> [row,...]   (含 code/last/change_rate)
      get_positions() -> [pos,...]
    cfg: dict（telegram_token, telegram_chat, scan_sec, min_grade, batch, phases）
    """
    def __init__(self, get_kline, get_snapshot, get_positions, cfg):
        self.get_kline = get_kline
        self.get_snapshot = get_snapshot
        self.get_positions = get_positions
        self.cfg = cfg
        self._stop = threading.Event()
        self._rr = 0                      # round-robin 指標
        self._last_status = {}
        self._halt_session = None         # 已熔斷的交易日

    # ---- 自選清單持久化（網頁推給橋接、引擎讀檔，網頁關了也能掃）----
    @staticmethod
    def load_watch():
        try:
            return json.load(open(WATCH_PATH, encoding="utf-8")).get("codes", [])
        except Exception:
            return list(DEFAULT_WATCH)

    @staticmethod
    def save_watch(codes):
        json.dump({"codes": codes, "updated": datetime.now().isoformat()},
                  open(WATCH_PATH, "w", encoding="utf-8"), ensure_ascii=False)

    def status(self):
        return dict(self._last_status)

    def recent_alerts(self, n=50):
        con = _db()
        rows = con.execute("""SELECT a.ts,a.symbol,a.side,a.type,a.grade,a.price,a.stop,a.target,a.rr,
                              a.reason,o.status,o.mfe,o.mae,o.last_px FROM alerts a
                              LEFT JOIN outcomes o ON a.id=o.alert_id ORDER BY a.id DESC LIMIT ?""", (n,))
        cols = ["ts", "symbol", "side", "type", "grade", "price", "stop", "target", "rr",
                "reason", "status", "mfe", "mae", "last_px"]
        out = [dict(zip(cols, r)) for r in rows]
        con.close()
        return out

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()

    def _loop(self):
        scan_sec = int(self.cfg.get("scan_sec", 60))
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as e:
                self._last_status["error"] = str(e)
            self._stop.wait(scan_sec)

    def _regime(self):
        """用 SPY 日線趨勢＋盤中 VWAP 判 risk_on/neutral/risk_off，並回傳 SPY 近段報酬。"""
        try:
            spy5 = [b for b in (self.get_kline("US.SPY", "5m", 80) or []) if b.get("close") is not None]
            spyd = [b for b in (self.get_kline("US.SPY", "day", 60) or []) if b.get("close") is not None]
        except Exception:
            return "neutral", None
        if len(spy5) < 13 or len(spyd) < 21:
            return "neutral", None
        daily_up = spyd[-1]["close"] > ema([b["close"] for b in spyd], 20)[-1]
        intraday_up = _feat(spy5)["close"] > _feat(spy5)["vwap"]
        spy_ret = (spy5[-1]["close"] / spy5[-13]["close"] - 1) * 100 if spy5[-13]["close"] else 0
        if daily_up and intraday_up:
            return "risk_on", spy_ret
        if (not daily_up) and (not intraday_up):
            return "risk_off", spy_ret
        return "neutral", spy_ret

    def _risk_halt(self, positions, account, session):
        """單日虧損熔斷：未實現損益 / 總資產 跌破門檻 → 本日暫停買訊（賣訊照發）。"""
        limit = float(self.cfg.get("daily_loss", 0.06))
        if not account or not account.get("total_assets"):
            return False
        pl = sum((p.get("pl_val") or 0) for p in positions)
        dd = pl / account["total_assets"]
        if dd <= -abs(limit):
            if self._halt_session != session:    # 一日一次警示
                self._halt_session = session
                t, c = self.cfg.get("telegram_token"), self.cfg.get("telegram_chat")
                send_telegram(t, c, f"🛑 <b>單日虧損熔斷</b>　未實現 {dd*100:.1f}%（門檻 -{limit*100:.0f}%）\n今日暫停買進訊號，只發賣出/減碼。先停手、檢視、別報復性交易。")
            return True
        return False

    def scan_once(self):
        phase = market_phase()
        allow = self.cfg.get("phases", ["regular"])
        self._last_status = {"phase": phase, "ts": datetime.now().isoformat(timespec="seconds")}
        if phase not in allow:
            return
        con = _db()
        session = session_key()
        min_grade = self.cfg.get("min_grade", "C")
        order = {"A": 3, "B": 2, "C": 1}
        token, chat = self.cfg.get("telegram_token"), self.cfg.get("telegram_chat")
        weights = load_weights()

        # 市場 regime（每輪算一次）
        regime, spy_ret = self._regime()
        ctx = {"regime": regime, "spy_ret": spy_ret}

        # 持股：每輪都掃（賣點最重要）
        positions, account = [], None
        try:
            positions = self.get_positions() or []
        except Exception:
            positions = []
        try:
            account = self.cfg.get("get_account") and self.cfg["get_account"]()
        except Exception:
            account = None
        pos_codes = [p.get("code") for p in positions if p.get("code")]

        # 風控：單日虧損熔斷
        halt_buys = self._risk_halt(positions, account, session)
        # risk-off 自動拉高買訊門檻
        buy_min = min_grade
        if regime == "risk_off" and order.get(min_grade, 1) < order["B"]:
            buy_min = "B"

        # 自選：round-robin 分批，尊重牛牛 K 線速率限制
        watch = self.load_watch()
        batch = int(self.cfg.get("batch", 25))
        if watch:
            start = self._rr % len(watch)
            sel = (watch + watch)[start:start + batch]
            self._rr = (self._rr + batch) % max(len(watch), 1)
        else:
            sel = []

        snap_map = {}
        try:
            for r in self.get_snapshot(list(set(sel + pos_codes))) or []:
                snap_map[r.get("code")] = r
        except Exception:
            pass

        pushed_cnt = 0
        if not halt_buys:                       # 熔斷時不發買訊
            for code in sel:
                c2 = dict(ctx); c2["daily_chg"] = snap_map.get(code, {}).get("change_rate")
                sigs = self._eval(code, "buy", c2.get("daily_chg"), None, c2, weights)
                pushed_cnt += self._emit(con, session, code, sigs, buy_min, order, token, chat)
        pmap = {p.get("code"): p for p in positions}
        for code in pos_codes:
            sigs = self._eval(code, "sell", None, pmap.get(code), ctx, weights)
            pushed_cnt += self._emit(con, session, code, sigs, min_grade, order, token, chat)

        # 結果追蹤
        price_of = {c: (snap_map.get(c, {}).get("last")) for c in snap_map}
        try:
            track_outcomes(con, {k: v for k, v in price_of.items() if v is not None})
        except Exception:
            pass
        con.close()
        self._last_status.update({"pushed": pushed_cnt, "scanned": len(sel),
                                  "regime": regime, "halt_buys": halt_buys})

    def _eval(self, code, kind, daily_chg=None, pos=None, ctx=None, weights=None):
        try:
            bars = self.get_kline(code, "5m", 120) or []
        except Exception:
            return []
        bars = [b for b in bars if b.get("close") is not None]
        if kind == "buy":
            bars15 = None
            try:
                bars15 = [b for b in (self.get_kline(code, "15m", 80) or []) if b.get("close") is not None]
            except Exception:
                bars15 = None
            return eval_buy(bars, daily_chg, bars15, ctx, weights)
        return eval_sell(bars, pos, ctx)

    def _emit(self, con, session, code, sigs, min_grade, order, token, chat):
        sym = (code or "").replace("US.", "")
        key = self.cfg.get("anthropic_key")
        model = self.cfg.get("ai_model", "claude-haiku-4-5-20251001")
        n = 0
        for sig in sigs:
            if order.get(sig["grade"], 0) < order.get(min_grade, 1):
                continue
            if already_alerted(con, session, code, sig["side"], sig["type"]):
                continue
            allow = True
            if key:                     # 每則訊號都過 AI 複核
                v = ai_vet(sym, sig, key, model)
                if v:
                    sig["reason"] += "｜AI複核：" + v["verdict"] + ("，" + v["note"] if v.get("note") else "")
                    if v["verdict"] == "不建議":
                        allow = False   # AI 否決：仍記錄，但不推播
            ok = send_telegram(token, chat, fmt_msg(sym, sig)) if (token and allow) else False
            log_alert(con, session, code, sig, ok)
            if ok:
                n += 1
        return n


# 預設自選（無 wo_watch.json 時用；網頁連上後會覆寫成你的清單）
DEFAULT_WATCH = [
    "US.NVDA", "US.AMD", "US.TSM", "US.AVGO", "US.MU", "US.ARM", "US.MRVL", "US.SMCI",
    "US.AAPL", "US.MSFT", "US.GOOGL", "US.META", "US.AMZN", "US.TSLA", "US.PLTR",
    "US.COIN", "US.MSTR", "US.SOXL", "US.QQQ", "US.SPY",
]


if __name__ == "__main__":
    # 簡單自測：用假資料驗證指標與訊號不會壞
    import random
    bars = []
    px = 100.0
    for i in range(120):
        o = px
        px = max(1.0, px + random.uniform(-1, 1.2))
        bars.append({"time": "2026-06-19 %02d:%02d:00" % (9 + i // 12, (i * 5) % 60),
                     "open": o, "high": max(o, px) + 0.3, "low": min(o, px) - 0.3,
                     "close": px, "volume": random.randint(1000, 5000)})
    print("phase:", market_phase(), "session:", session_key())
    print("buy signals:", json.dumps(eval_buy(bars, 1.2), ensure_ascii=False)[:400])
    print("sell signals:", json.dumps(eval_sell(bars), ensure_ascii=False)[:400])
    print("DB at:", DB_PATH)
