#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窩 Trading 大腦 — 券商持股自動同步（永豐 Shioaji ＋ 國泰 ＋ CSV 匯入）
------------------------------------------------------------------
把你在國泰／永豐（複委託美股現股）的**真實持股**自動寫進 wo_holdings.json，
警示引擎就用即時報價盯它們、到賣點通知你——免每天手動輸入。

老實說明（很重要）：
  各券商 API 對「複委託美股」的支援差很多。本檔提供三條路，挑能用的：
    1) CSV 匯入（最穩、今天就能用）：券商網頁把持股匯出成 CSV，丟進來轉換。
    2) 永豐 Shioaji（pip install shioaji）：依官方 API 抓 list_positions。
    3) 國泰 API：介面留好，接法依你帳戶實際回傳。
  先用 `--probe` 把你帳戶實際欄位印出來，我再幫你把映射對準（尤其複委託美股）。

只讀持股、不下單。金鑰放 .broker_config.json（已 gitignore），別外流。

用法：
  python broker_sync.py --probe --broker sinopac          # 先看回傳長相
  python broker_sync.py --sync --broker sinopac --watch 300
  python broker_sync.py --csv my_holdings.csv             # CSV 匯入（code,qty,cost）
"""
import argparse, csv, json, os, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
HOLDINGS_PATH = os.path.join(HERE, "wo_holdings.json")
BROKER_CFG = os.path.join(HERE, ".broker_config.json")


def load_cfg():
    try:
        return json.load(open(BROKER_CFG, encoding="utf-8"))
    except Exception:
        return {}


def _norm_code(sym):
    """統一成引擎用的 US.<ticker>（美股）。已是 US./指數則原樣。"""
    s = (sym or "").strip().upper()
    if not s:
        return None
    if s.startswith("US."):
        return s
    return "US." + s


def write_holdings(items, source):
    """合併寫入：保留其他來源、覆蓋同來源（與 alert_engine 同一份 wo_holdings.json）。"""
    try:
        from alert_engine import AlertEngine
        AlertEngine.save_holdings(items, source=source)
        return AlertEngine.load_holdings()
    except Exception:
        # 後備：自己寫（格式相同）
        try:
            cur = json.load(open(HOLDINGS_PATH, encoding="utf-8")).get("holdings", [])
        except Exception:
            cur = []
        cur = [h for h in cur if h.get("source") != source]
        for it in items:
            it["source"] = source
        merged = cur + items
        json.dump({"holdings": merged, "updated": datetime.now().isoformat()},
                  open(HOLDINGS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return merged


# ====================================================================
# 1) CSV 匯入（最穩）
# ====================================================================
def from_csv(path):
    """接受欄位（不分大小寫，彈性）：code/symbol/代號、qty/shares/股數、cost/price/成本/均價。"""
    items = []
    with open(path, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        for row in rd:
            low = {(k or "").strip().lower(): v for k, v in row.items()}
            code = low.get("code") or low.get("symbol") or low.get("代號") or low.get("ticker")
            qty = low.get("qty") or low.get("shares") or low.get("股數") or low.get("quantity")
            cost = low.get("cost") or low.get("price") or low.get("成本") or low.get("均價") or low.get("avg_cost")
            try:
                c = _norm_code(code); q = float(qty); k = float(cost)
            except Exception:
                continue
            if c and q > 0 and k > 0:
                items.append({"code": c, "qty": q, "cost": k})
    return items


# ====================================================================
# 2) 永豐 Shioaji
# ====================================================================
def shioaji_positions(cfg, probe=False):
    try:
        import shioaji as sj
    except Exception:
        raise SystemExit("請先安裝永豐 API：pip install shioaji")
    api = sj.Shioaji()
    key = cfg.get("sinopac_api_key"); sec = cfg.get("sinopac_secret_key")
    if not (key and sec):
        raise SystemExit("缺 sinopac_api_key / sinopac_secret_key（放 .broker_config.json）")
    api.login(api_key=key, secret_key=sec)
    try:
        positions = api.list_positions(api.stock_account)
    finally:
        try:
            api.logout()
        except Exception:
            pass
    if probe:
        print("=== 永豐 list_positions 原始回傳（給映射用）===")
        for p in positions:
            print(p)
        return []
    items = []
    for p in positions:
        code = getattr(p, "code", None)
        qty = getattr(p, "quantity", None)
        cost = getattr(p, "price", None)            # 平均成本
        if code and qty and cost:
            items.append({"code": _norm_code(code), "qty": float(qty), "cost": float(cost)})
    return items


# ====================================================================
# 3) 國泰（介面留好，接法依 probe 結果）
# ====================================================================
def cathay_positions(cfg, probe=False):
    """國泰證券 API：請先 --probe 把回傳貼給我對映射。
    若你已知道 SDK，填 cfg['cathay_*'] 後在此實作；目前先支援 CSV 路徑。"""
    raise SystemExit("國泰自動接法需先用 --probe 取得你帳戶回傳格式（或先用 --csv 國泰匯出檔）。"
                     "把回傳貼回來，我幫你把這段補完。")


ADAPTERS = {"sinopac": shioaji_positions, "cathay": cathay_positions}


def do_sync(broker, cfg):
    fn = ADAPTERS[broker]
    items = fn(cfg, probe=False)
    merged = write_holdings(items, broker)
    print(f"[{datetime.now():%H:%M:%S}] {broker}: 同步 {len(items)} 檔 → wo_holdings.json（共 {len(merged)} 檔）")
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", choices=list(ADAPTERS), help="sinopac(永豐) / cathay(國泰)")
    ap.add_argument("--probe", action="store_true", help="只印出券商回傳格式，不寫檔")
    ap.add_argument("--sync", action="store_true", help="抓持股寫入 wo_holdings.json")
    ap.add_argument("--csv", help="改用 CSV 匯入（code,qty,cost）")
    ap.add_argument("--watch", type=int, default=0, help="每 N 秒自動同步一次（0=只跑一次）")
    a = ap.parse_args()
    cfg = load_cfg()

    if a.csv:
        items = from_csv(a.csv)
        merged = write_holdings(items, "csv:" + os.path.basename(a.csv))
        print(f"CSV 匯入 {len(items)} 檔 → wo_holdings.json（共 {len(merged)} 檔）")
        return
    if not a.broker:
        raise SystemExit("請指定 --broker sinopac|cathay，或用 --csv 檔案。")
    if a.probe:
        ADAPTERS[a.broker](cfg, probe=True)
        return
    if not a.sync:
        raise SystemExit("加 --sync 才會寫入；或 --probe 先看格式。")
    do_sync(a.broker, cfg)
    if a.watch > 0:
        print(f"每 {a.watch}s 自動同步中…（Ctrl+C 結束）")
        try:
            while True:
                time.sleep(a.watch); do_sync(a.broker, cfg)
        except KeyboardInterrupt:
            print("\n結束。")


if __name__ == "__main__":
    main()
