#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窩 Trading 大腦 — 富途牛牛本地數據橋接（read-only / 只讀，不下單）
------------------------------------------------------------------
把你已付費的富途 / moomoo 即時報價與實際持股，安全地提供給本機的看盤網頁與 AI 使用。

安全設計：
  • 只「讀」資料：報價、K 線、持股、帳戶；完全不含下單功能。
  • 只綁定 127.0.0.1（本機），外部網路無法連入。
  • 需要 token 才能存取資料端點，避免你瀏覽的其他網站偷讀你的持股。

前置需求：
  1. 安裝並登入「FutuOpenD」（富途官方的本地行情/交易閘道程式）。
  2. pip install futu-api
  3. python futu_bridge.py        # 啟動後會印出 token，貼到網頁的 ⚙ 設定裡

用法範例：
  python futu_bridge.py --port 8888 --firm FUTUSECURITIES
"""
import os, json, argparse, threading, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    from futu import (OpenQuoteContext, OpenSecTradeContext, RET_OK,
                      TrdEnv, TrdMarket, SecurityFirm, KLType)
except ImportError:
    raise SystemExit("找不到富途 SDK，請先安裝：pip install futu-api")

ARGS = None
QUOTE = None
TRD = None
LOCK = threading.Lock()

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".futu_bridge_token")
HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wo_trade.html")


def load_token():
    if os.path.exists(TOKEN_FILE):
        return open(TOKEN_FILE).read().strip()
    tok = secrets.token_urlsafe(18)
    open(TOKEN_FILE, "w").write(tok)
    return tok


TOKEN = None


def get_quote():
    global QUOTE
    if QUOTE is None:
        QUOTE = OpenQuoteContext(host=ARGS.futu_host, port=ARGS.futu_port)
    return QUOTE


def get_trd():
    global TRD
    if TRD is None:
        firm = getattr(SecurityFirm, ARGS.firm)
        TRD = OpenSecTradeContext(filter_trdmarket=TrdMarket.US,
                                  host=ARGS.futu_host, port=ARGS.futu_port,
                                  security_firm=firm)
    return TRD


def num(v):
    try:
        if v is None or v == "" or v != v:  # NaN 檢查
            return None
        return float(v)
    except Exception:
        return None


def do_quote(codes):
    with LOCK:
        ret, data = get_quote().get_market_snapshot(codes)
    if ret != RET_OK:
        return {"error": str(data)}
    out = []
    for _, r in data.iterrows():
        last = num(r.get("last_price"))
        prev = num(r.get("prev_close_price"))
        chg = round((last - prev) / prev * 100, 2) if (last and prev) else None
        out.append({
            "code": r.get("code"), "name": r.get("name"),
            "last": last, "open": num(r.get("open_price")),
            "high": num(r.get("high_price")), "low": num(r.get("low_price")),
            "prev_close": prev, "change_rate": chg,
            "volume": num(r.get("volume")), "turnover": num(r.get("turnover")),
            "update_time": r.get("update_time"),
        })
    return {"quotes": out}


def do_positions():
    with LOCK:
        ret, data = get_trd().position_list_query(trd_env=TrdEnv.REAL)
    if ret != RET_OK:
        return {"error": str(data)}
    out = []
    for _, r in data.iterrows():
        out.append({
            "code": r.get("code"), "name": r.get("stock_name"),
            "qty": num(r.get("qty")), "can_sell": num(r.get("can_sell_qty")),
            "cost": num(r.get("cost_price")), "price": num(r.get("nominal_price")),
            "pl_ratio": num(r.get("pl_ratio")), "pl_val": num(r.get("pl_val")),
            "market_val": num(r.get("market_val")),
        })
    return {"positions": out}


def do_account():
    with LOCK:
        ret, data = get_trd().accinfo_query(trd_env=TrdEnv.REAL)
    if ret != RET_OK:
        return {"error": str(data)}
    r = data.iloc[0]
    return {"account": {
        "total_assets": num(r.get("total_assets")), "cash": num(r.get("cash")),
        "market_val": num(r.get("market_val")), "power": num(r.get("power")),
    }}


def do_kline(code, n):
    with LOCK:
        res = get_quote().request_history_kline(code, ktype=KLType.K_DAY, max_count=n)
    ret, data = res[0], res[1]
    if ret != RET_OK:
        return {"error": str(data)}
    out = []
    for _, r in data.iterrows():
        out.append({
            "time": r.get("time_key"), "open": num(r.get("open")),
            "high": num(r.get("high")), "low": num(r.get("low")),
            "close": num(r.get("close")), "volume": num(r.get("volume")),
        })
    return {"kline": out}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "X-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _check_token(self, q):
        tok = self.headers.get("X-Token") or (q.get("token", [""])[0])
        return tok == TOKEN

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)

        if path == "/" or path == "/index.html":
            if os.path.exists(HTML_FILE):
                body = open(HTML_FILE, "rb").read()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "wo_trade.html not found next to bridge"}, 404)
            return

        if path == "/health":
            ok = True
            try:
                get_quote()
            except Exception as e:
                ok = False
            self._json({"ok": True, "futu": ok})
            return

        if not self._check_token(q):
            self._json({"error": "invalid token"}, 403)
            return

        try:
            if path == "/quote":
                codes = [c for c in q.get("codes", [""])[0].split(",") if c]
                self._json(do_quote(codes) if codes else {"quotes": []})
            elif path == "/positions":
                self._json(do_positions())
            elif path == "/account":
                self._json(do_account())
            elif path == "/kline":
                code = q.get("code", [""])[0]
                n = int(q.get("num", ["120"])[0])
                self._json(do_kline(code, n) if code else {"error": "missing code"})
            else:
                self._json({"error": "unknown endpoint"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)


def main():
    global ARGS, TOKEN
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1", help="橋接監聽位址（預設只綁本機）")
    p.add_argument("--port", type=int, default=8888)
    p.add_argument("--futu-host", default="127.0.0.1", help="FutuOpenD 位址")
    p.add_argument("--futu-port", type=int, default=11111, help="FutuOpenD 連接埠")
    p.add_argument("--firm", default="FUTUSECURITIES",
                   help="券商：FUTUSECURITIES(港) / FUTUINC(美moomoo) / FUTUSG / FUTUAU")
    ARGS = p.parse_args()
    TOKEN = load_token()

    print("=" * 56)
    print(" 窩 Trading 大腦 — 富途數據橋接（只讀）")
    print("=" * 56)
    print(f"  本機網址 : http://{ARGS.host}:{ARGS.port}")
    print(f"  Token    : {TOKEN}")
    print("  → 用瀏覽器開上面網址，或在 GitHub Pages 版的 ⚙ 設定貼入網址與 Token")
    print(f"  FutuOpenD: {ARGS.futu_host}:{ARGS.futu_port}（請確認已啟動並登入）")
    print("  Ctrl+C 結束。只讀、不下單、僅綁定本機。")
    print("=" * 56)

    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n關閉中…")
    finally:
        if QUOTE:
            QUOTE.close()
        if TRD:
            TRD.close()


if __name__ == "__main__":
    main()
