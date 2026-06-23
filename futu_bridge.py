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
import os, json, argparse, threading, secrets, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    from futu import (OpenQuoteContext, OpenSecTradeContext, RET_OK,
                      TrdEnv, TrdMarket, SecurityFirm, KLType, SubType)
except ImportError:
    raise SystemExit("找不到富途 SDK，請先安裝：pip install futu-api")

try:
    import alert_engine
except Exception:                       # 引擎可選；缺檔也不影響看盤橋接
    alert_engine = None

try:
    import crypto as crypto_mod
except Exception:
    crypto_mod = None

try:
    import bitget_private as bitget_mod    # Bitget 唯讀私有 API（自動同步合約倉位/保證金/掛單）
except Exception:
    bitget_mod = None

# 橋接版本：每次改 .py 都會 bump。網頁與啟動橫幅都會顯示，方便確認本機程式有沒有更新到。
BRIDGE_VERSION = "2026.06.24"

ARGS = None
QUOTE = None
TRD = None
ENGINE = None
CRYPTO = None
LOCK = threading.Lock()

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".futu_bridge_token")
HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wo_trade.html")
RAW_URL = "https://raw.githubusercontent.com/wofitnesscoltd-wq/wo-fitness/main/wo_trade.html"


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".alert_config.json")


def load_alert_config():
    """讀一次性設定檔（Telegram / Claude 金鑰），讓使用者不必每次打指令重貼。"""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


API_URL = "https://api.github.com/repos/wofitnesscoltd-wq/wo-fitness/contents/wo_trade.html?ref=main"


_HTML_CACHE = {"html": None, "ts": 0.0}


def fetch_latest_html():
    """Pull the latest app HTML from GitHub. Short in-process cache so refresh bursts
    don't burn GitHub's 60/hr unauthenticated API limit (which caused stale fallbacks).
    API first (no CDN lag) with cache-buster; fall back to raw; last-known on failure."""
    import time as _t
    now = _t.time()
    if _HTML_CACHE["html"] and (now - _HTML_CACHE["ts"]) < 8:
        return _HTML_CACHE["html"]
    cb = str(int(now * 1000))
    attempts = [
        (API_URL + "&t=" + cb, {"User-Agent": "wo-bridge", "Accept": "application/vnd.github.raw",
                                "Cache-Control": "no-cache"}),
        (RAW_URL + "?t=" + cb,
         {"User-Agent": "wo-bridge", "Cache-Control": "no-cache", "Pragma": "no-cache"}),
    ]
    for url, headers in attempts:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read().decode("utf-8")
                _HTML_CACHE["html"] = html
                _HTML_CACHE["ts"] = now
                return html
        except Exception:
            continue
    return _HTML_CACHE["html"]


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


def _row(r):
    last = num(r.get("last_price"))
    prev = num(r.get("prev_close_price"))
    chg = round((last - prev) / prev * 100, 2) if (last and prev) else None
    return {
        "code": r.get("code"), "name": r.get("name"),
        "last": last, "open": num(r.get("open_price")),
        "high": num(r.get("high_price")), "low": num(r.get("low_price")),
        "prev_close": prev, "change_rate": chg,
        "volume": num(r.get("volume")), "turnover": num(r.get("turnover")),
        "after": num(r.get("after_price")), "after_rate": num(r.get("after_change_rate")),
        "pre": num(r.get("pre_price")), "pre_rate": num(r.get("pre_change_rate")),
        "update_time": r.get("update_time"),
    }


def _snap(cs):
    """Snapshot a list of codes; on error, split so one bad code doesn't fail all."""
    if not cs:
        return []
    with LOCK:
        ret, data = get_quote().get_market_snapshot(cs)
    if ret != RET_OK:
        if len(cs) == 1:
            return []  # skip an unsupported/invalid code instead of failing the batch
        mid = len(cs) // 2
        return _snap(cs[:mid]) + _snap(cs[mid:])
    return [_row(r) for _, r in data.iterrows()]


def do_quote(codes):
    out = []
    for i in range(0, len(codes), 50):
        out += _snap(codes[i:i + 50])
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


def _ktype(name):
    """Map our short interval codes to Futu KLType, defaulting to daily."""
    table = {"1m": "K_1M", "3m": "K_3M", "5m": "K_5M", "15m": "K_15M",
             "30m": "K_30M", "60m": "K_60M", "day": "K_DAY", "week": "K_WEEK",
             "month": "K_MON", "quarter": "K_QUARTER", "year": "K_YEAR"}
    return getattr(KLType, table.get(name, "K_DAY"), KLType.K_DAY)


def _subtype(ktype):
    table = {"1m": "K_1M", "3m": "K_3M", "5m": "K_5M", "15m": "K_15M",
             "30m": "K_30M", "60m": "K_60M"}
    return getattr(SubType, table.get(ktype, "K_5M"), SubType.K_5M)


def do_kline(code, n, ktype="day", live=False):
    kt = _ktype(ktype)
    intraday = ktype in ("1m", "3m", "5m", "15m", "30m", "60m")
    with LOCK:
        q = get_quote()
        if live and intraday:
            # 盤中即時K：用「即時行情訂閱額度」(你的付費權限)，不吃有限的歷史K額度
            try:
                q.subscribe([code], [_subtype(ktype)], subscribe_push=False)
            except TypeError:
                try:
                    q.subscribe([code], [_subtype(ktype)])
                except Exception:
                    pass
            except Exception:
                pass
            ret, data = q.get_cur_kline(code, min(n, 1000), kt)
        else:
            res = q.request_history_kline(code, ktype=kt, max_count=n)
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
    return {"kline": out, "ktype": ktype}


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
            html = fetch_latest_html()
            if html is None and os.path.exists(HTML_FILE):
                html = open(HTML_FILE, "r", encoding="utf-8").read()
            if html is None:
                self._json({"error": "無法載入 wo_trade.html（沒網路且本機也沒有副本）"}, 502)
                return
            # 回呼網址跟著「這次請求」走：本機＝127.0.0.1、同WiFi＝內網IP、外網通道(Cloudflare/Tailscale)＝該網域。
            # 用 X-Forwarded-Proto 判斷 http/https，避免 HTTPS 通道下的 mixed-content 被瀏覽器擋掉。
            host_hdr = self.headers.get("Host") or ("127.0.0.1:%d" % ARGS.port)
            proto = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip() or "http"
            inject = '<script>window.__WO_FUTU_TOKEN__=%s;window.__WO_FUTU_URL__="%s://%s";</script>' % (json.dumps(TOKEN), proto, host_hdr)
            html = html.replace("</head>", inject + "</head>", 1)
            body = html.encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/health":
            ok = True
            try:
                get_quote()
            except Exception as e:
                ok = False
            self._json({"ok": True, "futu": ok, "version": BRIDGE_VERSION,
                        "bitget": bool(bitget_mod and bitget_mod.configured())})
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
                n = int(q.get("num", ["300"])[0])
                ktype = q.get("ktype", ["day"])[0]
                live = q.get("live", ["0"])[0] == "1"
                self._json(do_kline(code, n, ktype, live) if code else {"error": "missing code"})
            elif path == "/setwatch":
                codes = [c for c in q.get("codes", [""])[0].split(",") if c]
                if alert_engine and codes:
                    alert_engine.AlertEngine.save_watch(codes)
                self._json({"ok": True, "count": len(codes)})
            elif path == "/setholdings":
                # 格式 h=US.NVDA:10:200.5,US.AMD:5:120（code:股數:成本）
                items = []
                for part in q.get("h", [""])[0].split(","):
                    bits = part.split(":")
                    if len(bits) >= 3 and bits[0]:
                        try:
                            items.append({"code": bits[0], "qty": float(bits[1]), "cost": float(bits[2])})
                        except ValueError:
                            pass
                if alert_engine:
                    alert_engine.AlertEngine.save_holdings(items)
                self._json({"ok": True, "count": len(items)})
            elif path == "/holdings":
                self._json({"holdings": alert_engine.AlertEngine.load_holdings() if alert_engine else []})
            elif path == "/alerts":
                if not ENGINE:
                    self._json({"alerts": [], "engine": "off"})
                else:
                    n = int(q.get("num", ["50"])[0])
                    self._json({"alerts": ENGINE.recent_alerts(n), "engine": "on", "status": ENGINE.status()})
            elif path == "/engine":
                self._json({"engine": "on" if ENGINE else "off",
                            "status": ENGINE.status() if ENGINE else None})
            elif path == "/crypto":
                self._json({"crypto": "on" if CRYPTO else "off",
                            "status": CRYPTO.status() if CRYPTO else None})
            elif path == "/setcryptoholdings":
                # 格式 h=BTCUSDT:0.5:60000:10:long,ETHUSDT:2:3000:5:short（sym:size:entry:lev:side）
                items = []
                for part in q.get("h", [""])[0].split(","):
                    b = part.split(":")
                    if len(b) >= 5 and b[0]:
                        try:
                            items.append({"sym": b[0].upper(), "size": float(b[1]), "entry": float(b[2]),
                                          "lev": float(b[3]), "side": b[4] if b[4] in ("long", "short") else "long"})
                        except ValueError:
                            pass
                if crypto_mod:
                    crypto_mod.save_crypto_holdings(items)
                self._json({"ok": True, "count": len(items)})
            elif path == "/cryptoholdings":
                self._json({"holdings": crypto_mod.load_crypto_holdings() if crypto_mod else []})
            elif path == "/bitgetsync":
                # Bitget 唯讀同步：拉真實合約倉位 + 可用保證金 + 掛單。金鑰只從本機環境變數讀。
                if not bitget_mod:
                    self._json({"configured": False, "error": "找不到 bitget_private.py"})
                elif not bitget_mod.configured():
                    self._json({"configured": False,
                                "error": "未設定 Bitget 唯讀金鑰（環境變數 BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE）"})
                else:
                    try:
                        self._json(bitget_mod.sync())
                    except Exception as e:
                        self._json({"configured": True, "error": str(e)})
            elif path == "/setcmargin":
                # 可用保證金（全倉）—引擎用來算帳戶層級爆倉緩衝
                try:
                    v = float(q.get("v", ["0"])[0])
                except ValueError:
                    v = 0.0
                if crypto_mod:
                    crypto_mod.save_cmargin(v)
                self._json({"ok": True, "avail": v})
            elif path == "/cryptoquote":
                out = {}
                if crypto_mod:
                    kl = (CRYPTO.kl if CRYPTO else crypto_mod.SOURCES[ARGS.crypto_source][0])
                    for s in [x for x in q.get("syms", [""])[0].split(",") if x]:
                        try:
                            bars = kl(s.upper(), "5m", 2)
                            if bars:
                                out[s.upper()] = bars[-1]["close"]
                        except Exception:
                            pass
                self._json({"quotes": out})
            elif path == "/cryptokline":
                # 加密永續 K 線（給自製圖畫進出場/背離用）。iv 對齊網頁時間週期。
                sym = q.get("sym", [""])[0].upper()
                iv = q.get("iv", ["day"])[0]
                num = int(q.get("num", ["400"])[0])
                out = []
                if crypto_mod and sym:
                    ivmap = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
                             "60m": "1h", "day": "1d", "week": "1w"}
                    kl = (CRYPTO.kl if CRYPTO else crypto_mod.SOURCES[ARGS.crypto_source][0])
                    try:
                        out = kl(sym, ivmap.get(iv, "1d"), min(num, 1000))
                    except Exception:
                        out = []
                self._json({"kline": out})
            elif path == "/cryptofunding":
                # 目前資金費率（每結算週期）；先試 Bitget 再試 Binance（tokenized 美股永續多在 Bitget）
                out = {}
                if crypto_mod:
                    order = ["bitget", "binance"]
                    src = ARGS.crypto_source
                    if src in order:
                        order.remove(src); order.insert(0, src)
                    funds = [crypto_mod.SOURCES[k][1] for k in order if k in crypto_mod.SOURCES]
                    for s in [x for x in q.get("syms", [""])[0].split(",") if x]:
                        for fn in funds:
                            try:
                                d = fn(s.upper())
                                if d and d.get("funding") is not None:
                                    out[s.upper()] = d.get("funding")
                                    break
                            except Exception:
                                pass
                self._json({"funding": out})
            elif path == "/stats":
                self._json(ENGINE.stats() if ENGINE else {"open": 0})
            elif path == "/weights":
                self._json(alert_engine.load_weights() if alert_engine else {})
            elif path == "/report":
                if not ENGINE:
                    self._json({"error": "engine off"})
                else:
                    push = q.get("push", ["0"])[0] == "1"
                    self._json({"report": ENGINE.build_report("即時", push=push)})
            else:
                self._json({"error": "unknown endpoint"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)


def _provider_kline(code, ktype, num):
    r = do_kline(code, num, ktype)
    return r.get("kline", [])


def _provider_snapshot(codes):
    return do_quote(codes).get("quotes", [])


def _provider_positions():
    r = do_positions()
    return r.get("positions", [])


def _provider_account():
    r = do_account()
    return r.get("account")


def _perp_underlyings():
    """tokenized 美股永續部位 → 對應真實個股，給引擎用真實數據(牛牛)盯背離賣點。"""
    out = []
    if not crypto_mod:
        return out
    try:
        for h in crypto_mod.load_crypto_holdings():
            sym = (h.get("sym") or "").upper()
            if sym and crypto_mod.is_stock_perp(sym):
                base = crypto_mod._base(sym)
                if base:
                    out.append({"code": "US." + base, "perp": sym, "side": h.get("side", "long")})
    except Exception:
        pass
    return out


def start_engine():
    """有指定 --alerts 或設好 Telegram 時，啟動常駐警示引擎。"""
    global ENGINE
    if alert_engine is None:
        print("  警示引擎: 找不到 alert_engine.py，略過。")
        return
    fc = load_alert_config()
    token = ARGS.telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN") or fc.get("telegram_token")
    chat = ARGS.telegram_chat or os.environ.get("TELEGRAM_CHAT_ID") or fc.get("telegram_chat")
    if not (ARGS.alerts or token):
        return
    ai_key = ARGS.anthropic_key or os.environ.get("ANTHROPIC_API_KEY") or fc.get("anthropic_key")
    cfg = {
        "telegram_token": token, "telegram_chat": chat,
        "scan_sec": ARGS.scan_sec, "min_grade": ARGS.min_grade,
        "batch": ARGS.batch, "phases": [s.strip() for s in ARGS.phases.split(",") if s.strip()],
        "anthropic_key": ai_key, "ai_model": ARGS.ai_model,
        "get_account": _provider_account, "daily_loss": ARGS.daily_loss,
        "account_size": ARGS.account_size or fc.get("account_size"),
        "min_move": ARGS.min_move, "min_rr": ARGS.min_rr, "min_rvol": ARGS.min_rvol,
        "perp_underlyings": _perp_underlyings,
    }
    ENGINE = alert_engine.AlertEngine(_provider_kline, _provider_snapshot, _provider_positions, cfg)
    ENGINE.start()
    tg = "已設定" if token and chat else "未設定（只記錄到 DB，不推播）"
    print(f"  警示引擎: 已啟動　掃描每 {ARGS.scan_sec}s　最低等級 {ARGS.min_grade}　時段 {ARGS.phases}")
    print(f"  Telegram : {tg}")
    print(f"  AI 複核  : {'開（每則訊號過 Claude 複核）' if ai_key else '關（未提供 Anthropic 金鑰，純規則訊號）'}")


def start_crypto():
    """有 --crypto 時，啟動 24h 加密永續監測（幣安/Bitget），共用 Telegram/DB。"""
    global CRYPTO
    if not ARGS.crypto:
        return
    if crypto_mod is None or alert_engine is None:
        print("  加密監測: 找不到 crypto.py / alert_engine.py，略過。")
        return
    fc = load_alert_config()
    token = ARGS.telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN") or fc.get("telegram_token")
    chat = ARGS.telegram_chat or os.environ.get("TELEGRAM_CHAT_ID") or fc.get("telegram_chat")
    ai_key = ARGS.anthropic_key or os.environ.get("ANTHROPIC_API_KEY") or fc.get("anthropic_key")
    syms = [s.strip().upper() for s in (ARGS.crypto_symbols or "").split(",") if s.strip()] or None
    cfg = {"source": ARGS.crypto_source, "symbols": syms, "min_grade": ARGS.min_grade,
           "scan_sec": ARGS.scan_sec, "telegram_token": token, "telegram_chat": chat,
           "anthropic_key": ai_key, "ai_model": ARGS.ai_model,
           "min_move": max(ARGS.min_move, 0.06), "min_rr": max(ARGS.min_rr, 1.8),
           "min_rvol": max(ARGS.min_rvol, 0.8),
           "liq_alerts": not ARGS.no_crypto_liq}
    CRYPTO = crypto_mod.CryptoScanner(cfg)
    CRYPTO.start()
    _la = "關" if ARGS.no_crypto_liq else "開"
    print(f"  加密監測: 已啟動（{ARGS.crypto_source}）24h　幣種 {syms or '預設主流幣'}　買賣點推播:已移除　爆倉預警:{_la}")


def main():
    global ARGS, TOKEN
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1", help="橋接監聽位址（預設只綁本機）")
    p.add_argument("--lan", action="store_true", help="開放區域網路（手機同 WiFi 可連）；等同 --host 0.0.0.0")
    p.add_argument("--port", type=int, default=8888)
    p.add_argument("--futu-host", default="127.0.0.1", help="FutuOpenD 位址")
    p.add_argument("--futu-port", type=int, default=11111, help="FutuOpenD 連接埠")
    p.add_argument("--firm", default="FUTUSECURITIES",
                   help="券商：FUTUSECURITIES(港) / FUTUINC(美moomoo) / FUTUSG / FUTUAU")
    p.add_argument("--alerts", action="store_true", help="啟動常駐警示引擎（開盤自動掃描）")
    p.add_argument("--telegram-token", default=None, help="Telegram bot token（或設環境變數 TELEGRAM_BOT_TOKEN）")
    p.add_argument("--telegram-chat", default=None, help="Telegram chat id（或設環境變數 TELEGRAM_CHAT_ID）")
    p.add_argument("--scan-sec", type=int, default=60, help="掃描間隔秒數")
    p.add_argument("--min-grade", default="A", choices=["A", "B", "C"], help="推播的最低訊號等級（預設A：少而精）")
    p.add_argument("--min-move", type=float, default=0.05, help="目標最小漲幅（0.05=5%；波段而非抄短線）")
    p.add_argument("--min-rr", type=float, default=1.6, help="最低風險報酬比")
    p.add_argument("--min-rvol", type=float, default=0.7, help="最低相對量（過濾低流動性，如週日）")
    p.add_argument("--batch", type=int, default=25, help="每輪掃描的自選股數（round-robin，尊重行情速率）")
    p.add_argument("--phases", default="regular", help="掃描時段，逗號分隔：pre,regular,post")
    p.add_argument("--anthropic-key", default=None, help="Claude 金鑰，開啟每則訊號 AI 複核（或設環境變數 ANTHROPIC_API_KEY）")
    p.add_argument("--ai-model", default="claude-haiku-4-5-20251001", help="AI 複核用模型")
    p.add_argument("--daily-loss", type=float, default=0.06, help="單日虧損熔斷門檻（0.06=未實現-6%暫停買訊）")
    p.add_argument("--account-size", type=float, default=None, help="帳戶總額(USD)，給熔斷算百分比用（你部位在國泰/永豐/加密所時填）")
    p.add_argument("--crypto", action="store_true", help="同時啟動 24h 加密永續監測")
    p.add_argument("--crypto-source", default="binance", choices=["binance", "bitget"], help="加密行情來源")
    p.add_argument("--crypto-symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT", help="監測幣種，逗號分隔")
    p.add_argument("--crypto-alerts", action="store_true", help="（已停用）加密買賣點推播已永久移除；此旗標保留為相容用，無作用")
    p.add_argument("--no-crypto-liq", action="store_true", help="連加密『全倉爆倉預警』也關掉（預設保留這個保命警示）")
    ARGS = p.parse_args()
    if ARGS.lan:
        ARGS.host = "0.0.0.0"
    TOKEN = load_token()

    def _lan_ip():
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    print("=" * 56)
    print(" 窩 Trading 大腦 — 富途數據橋接（只讀）")
    print(f"  版本     : {BRIDGE_VERSION}")
    print("=" * 56)
    print(f"  本機網址 : http://127.0.0.1:{ARGS.port}")
    if ARGS.host == "0.0.0.0":
        print(f"  📱 手機開 : http://{_lan_ip()}:{ARGS.port}　（手機與電腦要同一個 WiFi）")
    else:
        print("  📱 手機看即時數據：加參數 --lan 重啟，會印出手機用的網址（需同 WiFi）")
    print(f"  Token    : {TOKEN}")
    print("  → 用瀏覽器開上面網址，或在 GitHub Pages 版的 ⚙ 設定貼入網址與 Token")
    print(f"  FutuOpenD: {ARGS.futu_host}:{ARGS.futu_port}（請確認已啟動並登入）")
    start_engine()
    start_crypto()
    if bitget_mod and bitget_mod.configured():
        print("  Bitget   : 唯讀金鑰已設定 → 網頁可按「🔄 同步 Bitget」自動帶入倉位/保證金/掛單")
    else:
        print("  Bitget   : 未設金鑰（要自動同步倉位，設環境變數 BITGET_API_KEY/SECRET/PASSPHRASE，唯讀權限即可）")
    print("  Ctrl+C 結束。只讀、不下單。" + ("⚠️ 已開放區網(--lan)，端點有 Token 保護；請在自家 WiFi 用。" if ARGS.host == "0.0.0.0" else "僅綁定本機。"))
    print("=" * 56)

    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n關閉中…")
    finally:
        if ENGINE:
            ENGINE.stop()
        if CRYPTO:
            CRYPTO.stop()
        if QUOTE:
            QUOTE.close()
        if TRD:
            TRD.close()


if __name__ == "__main__":
    main()
