# 警示引擎（買賣點自動掃描 ＋ Telegram）— 安裝說明

開盤期間自動掃描你的**自選清單找買點**、掃**持股找賣點**，每則訊號給 **A/B/C 等級＋逐項佐證**，
去重後推 **Telegram**，並全部寫進本機 SQLite（`wo_alerts.db`）供結果追蹤與之後的自我優化。

> 這是 `futu_bridge.py` 的延伸（`alert_engine.py`）。**只讀、不下單、只綁本機**。最後一鍵永遠是你。
> 引擎跑在本機常駐程式裡，所以**網頁關著也會掃、也會推 Telegram**。

---

## 一、先建一個 Telegram bot（一次性，2 分鐘）
1. Telegram 搜尋 **@BotFather** → 傳 `/newbot` → 取個名字 → 它會給你一串 **bot token**（像 `12345:ABC...`）。
2. 跟你的新 bot 傳一句話（隨便打 hi，啟用對話）。
3. 取得你的 **chat id**：瀏覽器開
   `https://api.telegram.org/bot<你的token>/getUpdates`
   找到 `"chat":{"id":123456789}` 那個數字，就是 chat id。

## 二、啟動引擎
在原本的橋接指令後面加參數：
```bash
python futu_bridge.py --alerts \
  --telegram-token "12345:ABC..." \
  --telegram-chat  "123456789" \
  --min-grade B \
  --phases regular
```
或用環境變數（不想把 token 寫在指令裡）：
```bash
export TELEGRAM_BOT_TOKEN="12345:ABC..."
export TELEGRAM_CHAT_ID="123456789"
python futu_bridge.py --alerts --min-grade B
```

### 開啟「每則訊號 AI 複核」（建議）
加上 Claude 金鑰，引擎會在推播前用 Claude **逐則複核**訊號（型態勉強/追高/逆勢/量能不足會被降為觀望或不建議；被判「不建議」就不推播、但仍記進 DB）。複核結果會附在訊息裡。
```bash
python futu_bridge.py --alerts \
  --telegram-token "<token>" --telegram-chat "<id>" \
  --anthropic-key "sk-ant-..." --ai-model claude-haiku-4-5-20251001 --min-grade B
# 或設環境變數 ANTHROPIC_API_KEY
```
> AI 複核採 **fail-open**：Claude 當機/逾時就回退成純規則訊號照推，不會讓你整個靜音漏訊。預設用 Haiku（快又省）；要更嚴謹可改 `--ai-model claude-opus-4-8`。

啟動後會印出「警示引擎: 已啟動」。開盤時段（美東 9:30–16:00）就會自動掃描並推播。

### 參數
| 參數 | 預設 | 說明 |
|---|---|---|
| `--alerts` | 關 | 啟動警示引擎 |
| `--telegram-token` / `--telegram-chat` | 無 | 不填則**只記錄到 DB、不推播** |
| `--scan-sec` | 60 | 掃描間隔秒數 |
| `--min-grade` | C | 只推 ≥ 此等級（建議盤中先用 B，少一點雜訊） |
| `--batch` | 25 | 每輪掃幾檔自選（round-robin，尊重牛牛行情速率上限） |
| `--phases` | regular | 掃描時段：`pre,regular,post` 可複選 |

## 重要：你的真實持股要「手動輸入」
牛牛/moomoo 對你只是**即時數據來源**，不是你下單或持股的地方（你在**國泰／永豐**買美股現股、在**加密交易所**打永續）。
所以賣點要盯的是你**真正的部位**——在網頁右側 **💼 持股** 分頁，打代號／股數／成本按 ＋ 加進去即可：
- 引擎會用即時報價盯這些部位，到賣點（跌破 VWAP/EMA、MACD 死叉、RSI 回落、跌破 EMA50…）就通知你。
- 自動同步給常駐引擎（`/setholdings`→`wo_holdings.json`），網頁關著也照盯。
- 單日虧損熔斷要算百分比，請設帳戶總額：啟動加 `--account-size 100000`（或寫進 `.alert_config.json` 的 `account_size`）。

## 三、在網頁看訊號
網頁右側分頁多了 **🚨 警示**：顯示引擎狀態、最近訊號（買/賣、等級、現價/停損/目標/R:R、理由）與**追蹤結果**（命中/停損/追蹤中、MFE/MAE）。
自選清單會由網頁自動同步給引擎（`/setwatch`），所以你在網頁加減股票，引擎下一輪就跟著掃。

---

## 訊號邏輯（Phase 2 起手版，會隨 Phase 5 自我優化）
- **買點（掃自選）**：VWAP 站回、均線多頭回測 EMA9、開盤區間突破 ORB（帶量）、RSI 超賣翻揚、MACD 金叉且在 VWAP 上。
- **賣點（掃持股）**：跌破 VWAP、EMA9 死叉 EMA20、MACD 死叉、RSI 超買回落、跌破 EMA50。
- **等級** = 匯流條件數 × RVOL × 與日線同向；A=多條件高量同向、C=單一觸發。
- **計畫**：用 ATR 反推停損與 2R 目標、算 R:R。
- **去重**：同檔同型態同方向，一個交易日只發一次。
- **結果追蹤**：每輪更新 MFE/MAE、先碰目標(命中)或停損(未命中) → 之後做勝率/期望值歸因與權重再校準。

## 加密永續監測（幣安 / Bitget，24 小時）
一鍵啟動已自動開（`start.py` 帶 `--crypto`）。手動跑可加：
```bash
python futu_bridge.py --alerts --crypto --crypto-source binance \
  --crypto-symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT --telegram-token "<t>" --telegram-chat "<id>"
```
- 24h 不分盤，重用同一套訊號引擎（VWAP/ORB/RSI/MACD/EMA＋多週期），market regime 用 **BTC** 當大盤。
- 每則加上**資金費率**解讀（多頭過熱付費＝留意反轉）。
- 換 Bitget：`--crypto-source bitget`。加密訊號跟美股一起進 🚨 警示分頁與 Telegram。
- 公開行情免 API 金鑰、只讀不下單。⚠️ 永續槓桿可能爆倉，務必設停損。
- **加密持股**：網頁 💼 持股分頁切到「加密永續」，輸入幣種／倉位／進場／槓桿／多空。引擎會盯這些部位的賣點，並算**距估算爆倉價**，<15% 自動推 ⚠️ 爆倉預警（減倉/補保證金/降槓桿）。爆倉價為 isolated 近似，各所階梯保證金不同僅供參考。

## 績效、自我優化與每日報告
- **🚨 警示分頁頂部**會顯示績效：已結算筆數、勝率、期望值(R)、獲利因子，分 A/B/C 等級，以及目前市場 regime。
- **每個交易日收盤後（美東 16:00–20:00）自動跑一次**：
  - **走動式權重學習**：依各流派的實際期望值，調 `wo_weights.json` 的權重（表現好的升、差的降，主群重新歸一）。
  - **每日 AI 報告**：把績效＋最近訊號丟給 Claude 做教練式檢討（哪種等級/方向好或差、失敗共因、下一步調什麼），推到 Telegram。
- 端點：`/stats`（績效）、`/weights`（目前權重）、`/report?push=1`（立即產報告並推播）。

## 安全
- 全程只讀、不下單；只綁 `127.0.0.1`；資料端點需 token。
- `wo_alerts.db`、`wo_watch.json` 都在本機、已列入 `.gitignore`，不會上傳。
- Telegram token 建議用環境變數，別寫進會進版控的檔案。
