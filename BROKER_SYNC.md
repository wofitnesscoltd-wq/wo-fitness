# 券商持股自動同步（永豐 ＋ 國泰）— 設定說明

把你在**國泰／永豐（複委託美股現股）**的真實持股，自動寫進 `wo_holdings.json`，
警示引擎就用即時報價盯它們、到賣點通知你——免每天手動輸入。**只讀持股、不下單。**

> 三條路，挑能用的。手動輸入（網頁 💼 持股）跟這裡的同步**可以並存**（依來源合併、依代號去重）。

---

## 路線 1：CSV 匯入（最穩、今天就能用）
從券商網頁把持股匯出成 CSV，欄位有 `代號/股數/成本`（或英文 code/qty/cost、shares/price 都認）：
```bash
python broker_sync.py --csv 我的持股.csv
```
寫好就生效，引擎下一輪就盯。最沒風險、不用交出任何 API 金鑰。

## 路線 2：永豐 Shioaji 自動同步
```bash
pip install shioaji
```
把金鑰放 `.broker_config.json`（此檔已 gitignore，不會上傳）：
```json
{ "sinopac_api_key": "你的KEY", "sinopac_secret_key": "你的SECRET" }
```
**先 probe**（重要）——把你帳戶實際回傳的欄位印出來，確認複委託美股有沒有在裡面、欄位叫什麼：
```bash
python broker_sync.py --probe --broker sinopac
```
把印出來的內容貼回來，我幫你把映射對到 100% 準。確認後就能自動同步：
```bash
python broker_sync.py --sync --broker sinopac --watch 300   # 每 5 分鐘同步一次
```

## 路線 3：國泰 API
國泰的接法依你帳戶實際回傳而定，流程一樣：先 `--probe --broker cathay` 取得格式 →
貼回來我把 `cathay_positions()` 補完 → `--sync`。在那之前，國泰可先走**路線 1（CSV）**。

---

## 為什麼要先 probe？
各券商 API 對「**複委託美股**」的支援與欄位命名差很多，我沒有你的帳戶、不能瞎猜。
`--probe` 只印格式、不寫任何東西，把結果貼回來我就能把它對準，避免做出「看起來會、其實抓錯」的同步。

## 安全
- 全程**只讀持股、不下單**。
- API 金鑰只放本機 `.broker_config.json`（已 gitignore），不入庫、不外傳。
- 寫入的 `wo_holdings.json` 也在本機、已 gitignore。
