#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窩 Trading 大腦 — 一鍵啟動
------------------------------------------------------------------
做三件事，讓你「馬上用」：
  1. 確認 futu-api 已安裝（沒有就自動裝）。
  2. 啟動本機橋接＋警示引擎（serve 最新網頁、自動帶 token、開盤自動掃描）。
  3. 開瀏覽器到 http://127.0.0.1:8888 —— 即時牛牛報價、自製圖、AI、警示全到位。

第一次會問一次（可全部 Enter 跳過）：Telegram 與 Claude 金鑰，存到 .alert_config.json，
之後就不必再打。跳過也能用：警示只記錄不推 Telegram、AI 複核關閉，但看盤與 App 內 AI 照常。

用法：
  python start.py
  （Windows 點 start.bat；Mac 點 start.command）
"""
import os, sys, json, time, subprocess, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, ".alert_config.json")
PORT = "8888"


def ensure_deps():
    try:
        import futu  # noqa: F401
        return
    except Exception:
        pass
    print("• 第一次啟動，安裝 futu-api 中（約 1 分鐘）…")
    subprocess.call([sys.executable, "-m", "pip", "install", "-q", "futu-api"])


def first_run_setup():
    if os.path.exists(CFG):
        return
    cfg = {}
    if sys.stdin and sys.stdin.isatty():
        print("\n（可選）填一次就好，要跳過就直接按 Enter：")
        cfg["telegram_token"] = input("  Telegram bot token（跳過＝不推播，只記錄）: ").strip()
        cfg["telegram_chat"] = input("  Telegram chat id: ").strip()
        cfg["anthropic_key"] = input("  Claude 金鑰 sk-ant…（給警示做 AI 複核，跳過＝純規則）: ").strip()
    try:
        json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("  已存到 %s（日後可直接編輯這個檔）\n" % CFG)
    except Exception:
        pass


def main():
    print("=" * 56)
    print(" 窩 Trading 大腦 — 一鍵啟動")
    print("=" * 56)
    ensure_deps()
    first_run_setup()
    # 預設開 --lan：手機同 WiFi 就能連（端點都有 Token 保護）。啟動訊息會印出手機用的網址。
    cmd = [sys.executable, os.path.join(HERE, "futu_bridge.py"), "--alerts", "--crypto", "--lan"]
    if len(sys.argv) > 1:                       # 允許附加參數，如 --firm FUTUINC
        cmd += sys.argv[1:]
    print("• 啟動橋接＋警示引擎…（Ctrl+C 結束）")
    proc = subprocess.Popen(cmd)
    time.sleep(3)
    url = "http://127.0.0.1:%s" % PORT
    print("• 開瀏覽器：%s" % url)
    try:
        webbrowser.open(url)
    except Exception:
        print("  （開不了瀏覽器就自己貼上面網址）")
    print("\n提示：請確認 FutuOpenD 已登入（牛牛官方閘道），才有即時美股報價。")
    print("第一次進網頁，右上 ⚙ 設定貼上你的 Anthropic 金鑰即可用 AI 大腦。\n")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
