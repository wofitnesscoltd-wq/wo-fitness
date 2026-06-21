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

## 安全
- 全程只讀、不下單；只綁 `127.0.0.1`；資料端點需 token。
- `wo_alerts.db`、`wo_watch.json` 都在本機、已列入 `.gitignore`，不會上傳。
- Telegram token 建議用環境變數，別寫進會進版控的檔案。
