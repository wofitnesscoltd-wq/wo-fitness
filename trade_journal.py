#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易日誌（唯讀）— 把你在 Bitget 的真實歷史成交/委託記錄成本機資料庫。

用途：
1. 給你自己複盤/未來回測用的完整交易紀錄（進出場價、量、方向、已實現損益）。
2. hedge_pattern_report()：從你「實際做過的動作」算出你自己的對鎖/保險腿操作習慣
   （加保險腿的時機、名目比例、持有多久才收）——這是未來要讓 AI 自動管理保險腿時，
   替「絕對上限」定數字用的依據，數字從你自己的歷史算出來，不是猜的。

設計原則（與 bitget_private.py 一致）：
- 全程唯讀。本模組不含任何下單/撤單呼叫，只讀 fill-history / orders-history。
- 原始 JSON 一併存進 DB（raw 欄位）：若命名對應日後發現有出入，資料不會丟、可重新解析。
- 這是「歷史行為分析」，不是即時風控引擎——不接爆倉預警、不接推播。
"""
import os
import json
import time
import sqlite3
import threading
import statistics

try:
    import bitget_private as bg
except Exception:
    bg = None

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "wo_trade_journal.db")

DAY_MS = 86400 * 1000
WINDOW_MS = 80 * DAY_MS          # 每次查詢的時間窗（保守抓 80 天，避開交易所常見的 90 天上限）
PAGE_LIMIT = 100


def _db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    # WAL：背景增量同步(JournalSync)跟使用者按按鈕觸發的 backfill/report 是不同執行緒各自開連線，
    # 會同時讀寫同一個檔案；WAL 模式讓讀取不被寫入卡住，busy_timeout 讓寫入互撞時等待重試而不是
    # 立刻丟 "database is locked"。
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("""CREATE TABLE IF NOT EXISTS fills(
        trade_id TEXT PRIMARY KEY, order_id TEXT, symbol TEXT, side TEXT, trade_side TEXT,
        size REAL, price REAL, quote_volume REAL, profit REAL, fee_detail TEXT,
        ts INTEGER, synced_at INTEGER, raw TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS orders_hist(
        order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, trade_side TEXT, pos_side TEXT,
        size REAL, base_volume REAL, price REAL, price_avg REAL, leverage REAL,
        margin_mode TEXT, order_type TEXT, status TEXT, total_profits REAL,
        enter_point_source TEXT, ts INTEGER, synced_at INTEGER, raw TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS sync_state(key TEXT PRIMARY KEY, value TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fills_sym_ts ON fills(symbol, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_orders_sym_ts ON orders_hist(symbol, ts)")
    return con


def _state_get(con, key, default=None):
    r = con.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def _state_set(con, key, value):
    con.execute("INSERT INTO sync_state(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def _upsert_fills(con, fills):
    n = 0
    for f in fills:
        if not f.get("tradeId"):
            continue
        cur = con.execute("""INSERT OR IGNORE INTO fills
            (trade_id,order_id,symbol,side,trade_side,size,price,quote_volume,profit,
             fee_detail,ts,synced_at,raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f["tradeId"], f.get("orderId"), f.get("sym"), f.get("side"), f.get("tradeSide"),
             f.get("size"), f.get("price"), f.get("quoteVolume"), f.get("profit"),
             json.dumps(f.get("feeDetail"), ensure_ascii=False) if f.get("feeDetail") is not None else None,
             int(f.get("ts") or 0), int(time.time() * 1000), json.dumps(f.get("raw"), ensure_ascii=False)))
        n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return n


def _upsert_orders(con, orders):
    n = 0
    for o in orders:
        if not o.get("orderId"):
            continue
        cur = con.execute("""INSERT OR IGNORE INTO orders_hist
            (order_id,symbol,side,trade_side,pos_side,size,base_volume,price,price_avg,
             leverage,margin_mode,order_type,status,total_profits,enter_point_source,
             ts,synced_at,raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (o["orderId"], o.get("sym"), o.get("side"), o.get("tradeSide"), o.get("posSide"),
             o.get("size"), o.get("baseVolume"), o.get("price"), o.get("priceAvg"),
             o.get("leverage"), o.get("marginMode"), o.get("orderType"), o.get("status"),
             o.get("totalProfits"), o.get("enterPointSource"), int(o.get("ts") or 0),
             int(time.time() * 1000), json.dumps(o.get("raw"), ensure_ascii=False)))
        n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return n


MAX_PAGES_PER_WINDOW = 500   # 防呆：萬一 idLessThan 這個游標參數名字猜錯、API 對它沒反應，
                             # 就會一直回傳同一頁——這個上限確保頂多空轉 500 頁就跳出，不會真的無窮迴圈掛住整個請求執行緒。


def _paginate_window(fetch_fn, symbol, start, end, sleep_s):
    """在 [start,end] 這個時間窗內，用 idLessThan 游標一路翻到底，回傳這個窗口全部原始記錄。
    backfill() 和 sync_incremental() 共用同一套分頁邏輯——一次寫對，兩處都安全：
    - 游標分頁不看絕對時間戳記，不會像『用 ts+1 前進』那樣在同毫秒有多筆時漏掉尾端資料。
    - 有 forward-progress 檢查（end_id 沒變就跳出）＋硬頁數上限，防呆游標參數萬一沒生效。"""
    out, id_lt, pages = [], None, 0
    while pages < MAX_PAGES_PER_WINDOW:
        items, end_id = fetch_fn(symbol=symbol, start_time=start, end_time=end,
                                 limit=PAGE_LIMIT, id_less_than=id_lt)
        pages += 1
        out.extend(items)
        time.sleep(sleep_s)
        if len(items) < PAGE_LIMIT or not end_id or end_id == id_lt:
            break
        id_lt = end_id
    return out


def backfill(days=365, symbol=None, sleep_s=0.2, progress_cb=None):
    """從現在往回拉最多 days 天的成交＋委託歷史。冪等：用 trade_id/order_id 當主鍵，
    重複執行不會產生重複資料，可放心中斷重跑。分頁：時間窗 80 天一段，每段內游標翻到底。
    刻意不做「連續空窗就提早停止」的優化——你可能真的有一段時間沒做加密永續交易，
    但那段空窗『之前』還有更早的真實紀錄；提早停止會把它們漏掉。完整性優先於省呼叫次數，
    多的 API 呼叫成本可忽略（每段空窗只多 2 次呼叫）。"""
    if bg is None or not bg.configured():
        return {"error": "未設定 Bitget 唯讀金鑰"}
    con = _db()
    now_ms = int(time.time() * 1000)
    end = now_ms
    oldest_seen = now_ms
    total_fills = total_orders = 0
    windows_left = max(1, -(-days * DAY_MS // WINDOW_MS))   # ceil(days*DAY_MS / WINDOW_MS)
    while windows_left > 0:
        start = max(0, end - WINDOW_MS)
        fills = _paginate_window(bg.fill_history, symbol, start, end, sleep_s)
        if fills:
            total_fills += _upsert_fills(con, fills)
            oldest_seen = min(oldest_seen, min(int(f.get("ts") or now_ms) for f in fills))
        orders = _paginate_window(bg.orders_history, symbol, start, end, sleep_s)
        if orders:
            total_orders += _upsert_orders(con, orders)
            oldest_seen = min(oldest_seen, min(int(o.get("ts") or now_ms) for o in orders))
        con.commit()   # 每個時間窗就 commit 一次，縮短單筆交易佔用時間，降低跟背景同步撞鎖的機率
        if progress_cb:
            progress_cb({"window_end": end, "fills": total_fills, "orders": total_orders})
        end = start
        windows_left -= 1
    # 把 sync_state 的游標推到「現在」，讓之後的增量同步從現在往前走，不再重複這段歷史
    _state_set(con, "last_fill_ts", now_ms)
    _state_set(con, "last_order_ts", now_ms)
    con.commit()
    con.close()
    return {"fills": total_fills, "orders": total_orders,
            "oldest_covered": oldest_seen, "days_requested": days}


def sync_incremental(symbol=None):
    """增量同步：只拉『上次拉到』之後的新資料。給背景常駐迴圈用，跑得快、幾乎不耗額度。
    跟 backfill() 共用同一套游標分頁（見 _paginate_window）——就算間隔很久沒跑（筆電關了
    幾天）導致這次要補的區間很大，也不會因為用『時間戳記+1』前進而在頁界剛好同毫秒多筆時漏資料。"""
    if bg is None or not bg.configured():
        return {"error": "未設定 Bitget 唯讀金鑰"}
    con = _db()
    now_ms = int(time.time() * 1000)
    last_fill = int(_state_get(con, "last_fill_ts", 0) or 0)
    last_order = int(_state_get(con, "last_order_ts", 0) or 0)
    fstart = last_fill + 1 if last_fill else now_ms - 7 * DAY_MS   # 從沒同步過：先補最近 7 天
    ostart = last_order + 1 if last_order else now_ms - 7 * DAY_MS
    total_fills = total_orders = 0
    # 區間可能因為久未同步而超過單窗上限（WINDOW_MS）——跟 backfill 一樣切段處理。
    end = now_ms
    while end > fstart:
        start = max(fstart, end - WINDOW_MS)
        fills = _paginate_window(bg.fill_history, symbol, start, end, 0.15)
        if fills:
            total_fills += _upsert_fills(con, fills)
        con.commit()
        end = start
    end = now_ms
    while end > ostart:
        start = max(ostart, end - WINDOW_MS)
        orders = _paginate_window(bg.orders_history, symbol, start, end, 0.15)
        if orders:
            total_orders += _upsert_orders(con, orders)
        con.commit()
        end = start
    _state_set(con, "last_fill_ts", now_ms)
    _state_set(con, "last_order_ts", now_ms)
    con.commit()
    con.close()
    return {"new_fills": total_fills, "new_orders": total_orders}


def stats():
    """健檢用：目前存了多少筆、涵蓋時間範圍。"""
    con = _db()
    nf = con.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM fills").fetchone()
    no = con.execute("SELECT COUNT(*) FROM orders_hist").fetchone()
    con.close()
    return {"fills": nf[0], "orders": no[0],
            "earliest": nf[1], "latest": nf[2],
            "configured": bool(bg and bg.configured())}


# ============================================================
# 對鎖/保險腿操作習慣分析 — 純算術，不呼叫 AI，不燒 API。
# 從「實際成交」重建你每個標的的多/空腿數量隨時間變化，抓出：
#   ①「加保險腿」事件：已有一腿倉位時，再開反向腿
#   ②「收保險腿」事件：對鎖/保險配置下，其中一腿被平掉
# 用這兩類事件的統計，回答「你自己平常怎麼用保險腿」。
# ============================================================
def _leg_events(fills):
    """把一個標的的成交序列，轉成 (long_size, short_size, avg_px) 的時間序列＋事件清單。
    Bitget 對鎖模式(hedge mode)語意：side(buy/sell) + tradeSide(open/close) 唯一決定動作：
      buy+open→開多  sell+close→平多  sell+open→開空  buy+close→平空
    這是雙向持倉的內在邏輯（不可能『buy 去 open 一個 short』），不依賴文件命名細節。"""
    fills = sorted(fills, key=lambda f: f.get("ts") or 0)
    long_sz, short_sz = 0.0, 0.0
    long_px, short_px = 0.0, 0.0
    long_last_open_ts, short_last_open_ts = None, None
    events = []   # {'type': 'hedge_open'|'hedge_close', ...}
    for f in fills:
        side, tside = f.get("side"), f.get("tradeSide")
        sz, px, ts = (f.get("size") or 0), (f.get("price") or 0), (f.get("ts") or 0)
        if sz <= 0 or px <= 0:
            continue
        if side == "buy" and tside == "open":
            other_notional = short_sz * short_px
            if long_sz <= 1e-12 and short_sz > 1e-12:
                events.append({"type": "hedge_open", "opened_side": "long", "opened_notional": sz * px,
                               "other_notional": other_notional, "ts": ts,
                               "lag_ms": (ts - short_last_open_ts) if short_last_open_ts else None})
            long_px = (long_px * long_sz + px * sz) / (long_sz + sz) if (long_sz + sz) > 0 else px
            long_sz += sz
            long_last_open_ts = ts
        elif side == "sell" and tside == "close":
            # 平多：只要平倉當下另一腿(空)還在，就算一次『收保險/對鎖腿』事件——
            # 不設近1:1門檻，因為保險腿常態上量本來就比主倉小，不是對鎖才算。
            if long_sz > 1e-12 and short_sz > 1e-12:
                events.append({"type": "hedge_close", "closed_side": "long", "closed_notional": sz * px,
                               "other_notional": short_sz * short_px, "ts": ts,
                               "held_ms": (ts - long_last_open_ts) if long_last_open_ts else None})
            long_sz = max(0.0, long_sz - sz)
        elif side == "sell" and tside == "open":
            other_notional = long_sz * long_px
            if short_sz <= 1e-12 and long_sz > 1e-12:
                events.append({"type": "hedge_open", "opened_side": "short", "opened_notional": sz * px,
                               "other_notional": other_notional, "ts": ts,
                               "lag_ms": (ts - long_last_open_ts) if long_last_open_ts else None})
            short_px = (short_px * short_sz + px * sz) / (short_sz + sz) if (short_sz + sz) > 0 else px
            short_sz += sz
            short_last_open_ts = ts
        elif side == "buy" and tside == "close":
            # 平空：同上，只要平倉當下多腿還在就算一次事件（不設近1:1門檻）。
            if long_sz > 1e-12 and short_sz > 1e-12:
                events.append({"type": "hedge_close", "closed_side": "short", "closed_notional": sz * px,
                               "other_notional": long_sz * long_px, "ts": ts,
                               "held_ms": (ts - short_last_open_ts) if short_last_open_ts else None})
            short_sz = max(0.0, short_sz - sz)
    return events


def hedge_pattern_report(lookback_days=180):
    """輸出純文字報告：你歷史上加/收保險腿的名目比例、時間差、持有天數統計。
    這份報告是設計『AI 自動管保險腿』的絕對上限時要用的依據——數字來自你自己的真實紀錄。"""
    con = _db()
    since = int(time.time() * 1000) - lookback_days * DAY_MS
    # 重建 long_sz/short_sz 一定要用「全部歷史」——只挑近 N 天的成交會讓 _leg_events()
    # 從 0 開始追蹤，把窗口之前就開好的那條主倉當成不存在，誤判/漏判窗口內的加碼事件。
    # 正確做法：先用全部資料重建事件，最後才依 since 篩「事件本身」發生的時間。
    rows = con.execute("SELECT symbol, side, trade_side, size, price, ts FROM fills ORDER BY ts").fetchall()
    con.close()
    by_sym = {}
    for symbol, side, trade_side, size, price, ts in rows:
        by_sym.setdefault(symbol, []).append(
            {"side": side, "tradeSide": trade_side, "size": size, "price": price, "ts": ts})
    open_ratios, open_lags_h, close_ratios, held_days = [], [], [], []
    max_hedge_notional = 0.0
    per_sym_lines = []
    for sym, fs in sorted(by_sym.items()):
        evs = [e for e in _leg_events(fs) if e.get("ts", 0) >= since]   # 全歷史重建，只篩事件時間
        opens = [e for e in evs if e["type"] == "hedge_open"]
        closes = [e for e in evs if e["type"] == "hedge_close"]
        for e in opens:
            if e["other_notional"] > 0:
                r = e["opened_notional"] / e["other_notional"]
                open_ratios.append(r)
                max_hedge_notional = max(max_hedge_notional, e["opened_notional"])
            if e.get("lag_ms") is not None and e["lag_ms"] >= 0:
                open_lags_h.append(e["lag_ms"] / 3600000)
        for e in closes:
            if e["other_notional"] > 0:
                close_ratios.append(e["closed_notional"] / e["other_notional"])
            if e.get("held_ms") is not None and e["held_ms"] >= 0:
                held_days.append(e["held_ms"] / 86400000)
        if opens or closes:
            per_sym_lines.append(f"・{sym}：加保險腿 {len(opens)} 次、收保險腿 {len(closes)} 次")
    def _fmt(xs, unit=""):
        if not xs:
            return "（無資料）"
        return (f"中位數 {statistics.median(xs):.2f}{unit}　"
               f"範圍 {min(xs):.2f}–{max(xs):.2f}{unit}　樣本數 {len(xs)}")
    lines = [
        f"📒 對鎖/保險腿操作習慣分析（近 {lookback_days} 天，來源：你的真實成交紀錄）",
        "",
        f"加保險腿時的『新腿名目／既有腿名目』比例：{_fmt(open_ratios)}",
        f"開主倉到加保險腿的時間差：{_fmt(open_lags_h, ' 小時')}",
        f"收保險腿時的『收掉腿名目／另一腿名目』比例：{_fmt(close_ratios)}",
        f"保險腿平均持有：{_fmt(held_days, ' 天')}",
        f"歷史單次保險腿最大名目：${max_hedge_notional:,.0f}",
        "",
        "每標的事件次數：",
    ] + (per_sym_lines or ["（近期無對鎖/保險腿事件）"]) + [
        "",
        "⚠️ 這是純統計、不是建議——僅供『未來設計 AI 自動管保險腿的絕對上限』時參考真實習慣用。",
    ]
    return "\n".join(lines)


class JournalSync:
    """背景常駐：每隔 sync_sec 秒跑一次增量同步。跟 crypto.py 的 CryptoScanner 同一套
    threading.Thread(daemon) 模式，各自獨立，不搶同一個輪詢頻率（成交紀錄不需要秒級新鮮度）。"""
    def __init__(self, sync_sec=900):
        self.sync_sec = sync_sec
        self._stop = threading.Event()
        self.last_result = {}

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.last_result = sync_incremental()
            except Exception as e:
                self.last_result = {"error": str(e)}
            self._stop.wait(self.sync_sec)


if __name__ == "__main__":
    # 離線自我檢查：用合成的成交資料驗證 hedge 事件偵測邏輯，不需要網路、不需要真金鑰。
    now = int(time.time() * 1000)
    synth = [
        # 開多 100 股 @30（主倉）
        {"side": "buy", "tradeSide": "open", "size": 100, "price": 30, "ts": now},
        # 2 小時後加保險空腿 20 股 @30（比例 20/100=0.2，插入即測 hedge_open）
        {"side": "sell", "tradeSide": "open", "size": 20, "price": 30, "ts": now + 2 * 3600 * 1000},
        # 3 天後把空腿收掉（held ≈ 3 天）
        {"side": "buy", "tradeSide": "close", "size": 20, "price": 28, "ts": now + (2 * 3600 + 3 * 86400) * 1000},
    ]
    evs = _leg_events(synth)
    opens = [e for e in evs if e["type"] == "hedge_open"]
    closes = [e for e in evs if e["type"] == "hedge_close"]
    assert len(opens) == 1 and abs(opens[0]["opened_notional"] / opens[0]["other_notional"] - 0.2) < 1e-6, \
        "hedge_open 比例應為 20*30/(100*30)=0.2"
    assert opens[0]["lag_ms"] == 2 * 3600 * 1000, "開主倉到加保險腿的時間差應為 2 小時"
    assert len(closes) == 1 and abs(closes[0]["held_ms"] - 3 * 86400 * 1000) < 1, "保險腿持有應約 3 天"
    print("hedge 事件偵測自我檢查：通過")
    print("configured() =", bool(bg and bg.configured()))
    if bg and bg.configured():
        print("stats():", stats())
    else:
        print("（未設金鑰，略過實際 API 呼叫）自我檢查通過。")
