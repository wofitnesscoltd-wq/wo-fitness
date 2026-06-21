# 🚀 馬上用（最快上手）

## 方式 A — 立刻開（免安裝，AI 大腦＋圖＋新聞）
直接開這個網址（電腦或手機都行）：

**https://wofitnesscoltd-wq.github.io/wo-fitness/wo_trade.html**

第一次進去點右上 **⚙ 設定** → 貼上你的 **Anthropic（Claude）金鑰** → 儲存。
立刻可用：🌡️ 市場溫度、🎯 強勢選股、🔬 個股分析、🔍 深入研究、⚡ 即時新聞、TradingView 圖。

> 這個版本會自動更新到最新，不必下載檔案。

---

## 方式 B — 完整版（即時牛牛報價＋自製圖＋自動警示）一鍵啟動
要「即時報價、無延遲自製圖（RSI/MACD/VWAP）、持股、開盤自動買賣點警示＋Telegram」，
在你那台跑富途的電腦上做：

1. 先開 **FutuOpenD**（富途官方閘道）並登入（要有美股即時權限）。
2. 下載這個專案（或 `git clone`），然後：
   - **Windows**：雙擊 **`start.bat`**
   - **Mac**：雙擊 **`start.command`**
   - 或終端機：`python start.py`
3. 第一次它會問（可全部 Enter 跳過）：Telegram token / chat id、Claude 金鑰 —— 填一次存起來。
4. 它會自動裝套件、啟動引擎、**開瀏覽器到 http://127.0.0.1:8888** —— 完成。

就這樣。之後每天開盤前點一下 `start` 就好，網頁關著警示也照跑、照推 Telegram。

### Telegram 推播（2 分鐘設定一次）
搜 **@BotFather** → `/newbot` 拿 token；跟你的 bot 傳句話後，開
`https://api.telegram.org/bot<token>/getUpdates` 找 `chat id`。填進第一次的問答即可。
細節見 **ALERTS_SETUP.md**。

### 上線前先回測（建議）
日線長窗（波段邏輯）：
```bash
python backtest.py --symbols US.NVDA,US.AMD,US.AAPL --num 2500
```
盤中多年（**驗的就是會上線的盤中訊號**，Polygon 多年 1/5 分 K；你說花費不在意）：
```bash
python backtest_intraday.py --source polygon --polygon-key <KEY> --symbols NVDA,AMD --years 5 --spy
# 沒有 Polygon 也能用富途近 1–2 年：--source futu --symbols US.NVDA,US.AMD
```
看分等級的勝率/期望值/獲利因子，確認有邊際再放心用。

---

## 我用哪個？
- 人在外面、只想問盤、看研究方向 → **方式 A**（手機開網址）。
- 在交易的電腦、要即時數據與自動警示 → **方式 B**（一鍵啟動）。
- 兩個同時用也行：手機看 A、桌機跑 B 收 Telegram。

⚠️ 全程只讀不下單、只綁本機、金鑰存在你自己的瀏覽器/電腦。非投資建議，務必設停損。
