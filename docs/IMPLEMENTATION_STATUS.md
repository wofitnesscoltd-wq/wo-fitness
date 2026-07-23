# 窩 Trading 系統實作現況
> 每次完成一批工作後更新此檔。**這是唯一現況真相來源**——查這裡，不用翻聊天記錄。
> 狀態圖例：✅已做 / 🟡部分(附缺口) / ❌未做 / ⚠️做錯或未驗證。

## 最後更新：2026-07-01 / commit e86111b（P2-2/P4-1/P4-2/P6-1/P8-2 這輪）

## 現況總表
| 項目 | 狀態 | 證據（檔:函式/行） | 缺口 |
|---|---|---|---|
| **P0-1** 危急聲音/紅閃＋頻率分級 | ✅ | 前端 `wo_trade.html:bufferAlarm()`（紅閃 `.bufflash` + WebAudio 嗶，危急<7% 每20s、警告<15% 每60s）；後端 `crypto.py` 爆倉預警 `bucket_for={7:300,12:900,20:3600}`（危急每5分≈近連續、警告15分、留意每小時，繞去重） | 分級門檻用 7/12/20%（非 20/12/7 字面，但語意一致） |
| **P1-1** 腿角色 `leg_role` 單一欄位＋全系統統一讀 | ✅ | 權威計算 `bitget_private.py:_tag_leg_roles()`（`|L−S|/(L+S)<0.2`→lock；小邊 insurance、大邊 directional；單腿 directional）。前端 `wo_trade.html:legRole()/isProtectiveLeg()/setLegRoleOverride()`（覆蓋>Bitget欄位>前端退路）；`posAdvice` 讀 `isProtectiveLeg`、`adviceLine` 分 🛡保險/⚖️對鎖、`maybeNotifyAdvice` 保險/對鎖腿早退。後端 `crypto.py` sync 帶 `leg_role`、`futu_bridge._perp_underlyings` 傳遞、`alert_engine.py` 減碼/回補路徑改讀 `leg_role`（退路：無欄位時同 perp 有多空腿即視對鎖）。手動覆蓋 UI 在持倉卡。**實測**：override 改 directional→lock、insurance 腿 near=true 推播數=0 | 後端 `_perp_underlyings` 僅在牛牛/FutuOpenD 開時觸發（美股本體背離路徑），使用者美股常關；主要保護在前端＋crypto.py |
| **P2-1** build_report 改按需、不自動推 AI | ✅ | `alert_engine.py:build_report()` AI 教練 `if key and use_ai`（`use_ai` 預設關）；自動 EOD 只推純績效數字 | — |
| **P2-2** 每日報告尾附「涵蓋/未涵蓋」行 | ✅ | `alert_engine.py:build_report()` 尾行「✅涵蓋：美股警示引擎…／⏭未涵蓋：加密永續、未結算 N 筆」 | — |
| **P2-3** 移除逐則 ai_vet | ✅ | `#130` 移除 `crypto._emit` 與 `alert_engine` 推播迴圈的 `ai_vet()` 呼叫；grep 僅剩 `def ai_vet`（dead，未被呼叫） | 可再刪 dead def（無害） |
| **P3-1** Bitget retry/backoff＋低緩衝加密輪詢 | ✅ | retry `bitget_private.py:_get()`（0.4/0.8/1.6s，4xx 不重試）；低緩衝輪詢 `bitgetAutoSync` throttle（<7%→12s、<15%→25s、<30%→40s、否則60s） | — |
| **P4-1** 結構化快照匯出＋每腿 leg_role | ✅ | `copyToMax()`：帳戶/曝險＋「每腿角色」（`legRole(h)` 即時重讀）＋每標的訊號記分卡一行 | 走前端按鈕（未另做 `/snapshot` 端點，等價） |
| **P4-2** 數字新鮮度時戳/過期變灰 | ✅ | `bitgetSyncedAt`/`cryptoQuotesAt`＋`ageStr()`；摘要顯示「Bitget N秒前」、>45s 標「可能過期」變黃 | 目前顯示 Bitget 年齡；報價/K線年齡已記錄可再擴充顯示 |
| **P5-1** 平保險腿兩意圖文案 | ✅ | `renderSimClose()`：① 主動換方向試算 ② 看錯回補損害回報 | — |
| **P5-2** 情境卡「距爆倉線」欄 | ✅ | `renderScenarioCard()` 表頭含「距爆倉」欄，`scenarioRow().liqPct` 顯示 | — |
| **P5-3** 單腿 marginRatio 交叉檢核 | ✅ | `bitget_private` 回 per-position `marginRatio`；儀表顯示「最高倉保證金率」 | 帳戶層級用 `crossedRiskRate`，per-leg marginRatio 顯示最大值 |
| **P6-1** pattern_signals.py（低波動蓄積、資金費異常） | ✅ | 新模組 `pattern_signals.py`（`low_vol_signal`/`funding_signal`/`scan`，確定性不呼叫AI，輸出永遠 N/2＋「僅供比對，非預測」）；橋接 `/patternsignals`；`crypto.py:funding_history`；併入 `copyToMax`。review 修正 funding 定值誤觸（絕對容忍度 1e-9） | 基準＝近窗（非literal 90天，已標明）；未平倉量/監理層本輪不做 |
| **P7-1** 突破加倉引擎（B5） | ✅ | `breakoutAdd()/addHeadroom()/brkLine()`＋🚀雷達開關＋保守/積極。實測突破/RVOL/回踩/風控 headroom 正確 | — |
| **P8-1** 安全硬化（SHA/header token/金鑰後端） | ✅ | `futu_bridge`：`WO_HTML_REF` 可釘 SHA；token 預設只認 X-Token 標頭；`/claude` 代理讓金鑰留後端 | 預設仍 `/main`（自動更新），釘 SHA 為 opt-in |
| **P8-2** 美股市場日曆（假日/半日盤） | ✅ | 前端 `US_HOLIDAYS`/`US_HALFDAYS`（`usSession` 半日盤 13:00 收）；後端 `alert_engine.py:market_phase()` 同一份日曆（假日→closed、半日盤 13:00 收），前後端一致 | 假日清單需逐年維護（2026–2027 已列） |
| **P9-1** 交易日誌（唯讀）：歷史成交/委託記錄本機 | ✅（待你本機首跑驗證欄位） | 新模組 `trade_journal.py`（`fill_history()`/`orders_history()` 於 `bitget_private.py`；SQLite WAL 模式 `wo_trade_journal.db`；`backfill()`/`sync_incremental()` 共用游標分頁 `_paginate_window()`／`JournalSync` 背景15分同步）；橋接 `/journal/backfill`、`/journal/report`、`/journal/stats`（含背景同步健康狀態）；設定面板兩顆按鈕。對抗式 review 抓到 3 個真 bug 已修：①`hedge_pattern_report()`原本先篩日期再重建狀態，導致主倉開在窗口外時窗口內事件被漏判——改成全歷史重建、只篩事件時間②`sync_incremental()`原本按時間戳記+1前進，同毫秒多筆會漏——統一改用游標分頁③分頁迴圈原本沒有防呆上限，游標參數若真的猜錯會無窮迴圈——加 forward-progress 檢查+500頁硬上限。另加 WAL+busy_timeout 降低背景同步與使用者操作互撞鎖的機率。修復後逐項重測：Bug1回歸案例(主倉200天前/保險腿30天前開)正確偵測、分頁防呆三情境(正常/卡住/失控)皆如預期、大缺口(200天)增量同步正確終止、SMCI/CRDO原始案例數字不變 | ⚠️ Bitget V2 fill-history/orders-history 欄位名稱**這輪沙盒連不上官方文件**(proxy 擋 bitget.com)，靠 WebSearch 片段＋既有 `orders()`/`positions()` 已驗證慣例推回去的；已存 raw JSON 兜底＋分頁防呆上限兜底，**第一次在你本機真實跑 backfill 才是真正驗證**，若欄位對不上會是空值不是崩潰，回報我就能一行修 |
| **P9-2** AI 自動管保險腿（帶絕對上限） | ❌ 尚未開始 | 本輪刻意不寫下單/執行程式碼——範圍已定案：新倉手動、只自動化對鎖/保險腿、size與止損距離要有絕對上限。下一步：你在本機跑 P9-1 backfill＋看報告，我們用報告裡的真實比例/持有天數數字，一起定上限與執行架構，再動工 | 需要開一把**有交易權限**的新 API key(現有唯讀 key 做不到)，是完全不同風險等級，動工前需明確設計review |
| **P8-3** 橋接單點故障降級提示 | ✅ | `/health` 回 `futu` 狀態；前端 `futu.futuLive` 降級；Bitget/加密路徑獨立於 FutuOpenD | — |

## 回歸檢查清單（累積增長，見任務書第3節 Step 3）
- [x] 全倉不對單腿算爆倉價、不對保險腿喊降槓桿（grep「止損比爆倉」「降槓桿或補保證金」= 0 筆，最後確認 2026-07-01）
- [x] 代幣化美股永續一律用 Bitget 標記價，不可用美股本體/幣安價（`cMark()`→`h.mark`；`crypto.py` 用 `p.get("mark")`）
- [x] 指標 None 不推等級A（`alert_engine._data_complete()` fail-closed，套 `eval_buy`/`eval_sell`）
- [x] 新出場推播去重冷卻仍生效（`maybeNotifyAdvice` lastT/lastReason/lastSl 30分冷卻，#129）
- [x] `leg_role` 由前端卡片/後端推播/快照三處一致讀取，無任一處自行重推（P1-1，2026-07-01）— 前端 `legRole()`、後端 `alert_engine` 讀 `p.get("leg_role")`、快照 `copyToMax` 即時重讀
- [x] 訊號記分卡純確定性、不呼叫 AI；輸出永遠 N/2（未觸發 0/2）、固定含「僅供比對，非預測」（P6-1，2026-07-01）
- [x] 前後端市場日曆一致（假日/半日盤同一份，`market_phase` 與 `usSession`）（P8-2，2026-07-01）

## 歷史紀錄
- 2026-07-01（P9-1）— 使用者提出「蒐集操作學習→日後AI管保險腿」需求。範圍釐清：新倉手動、只自動化對鎖/保險腿、需絕對上限。本輪只做 P9-1(唯讀交易日誌+習慣分析)，P9-2(實際執行)刻意延後到看過真實習慣報告、一起定案上限後才動工。對抗式 review 抓到 3 個真 bug(報告日期篩選順序錯誤導致漏判、增量同步時間戳記前進法會漏同毫秒資料、分頁迴圈無防呆上限)，全部修復並逐項重測通過。
- 2026-07-01（#135）— 完成 P2-2/P4-1/P4-2/P6-1/P8-2；P3-1 稽核發現先前已完成。對抗式 review 抓到 1 bug（funding_signal 定值誤觸），已修（絕對容忍度 1e-9）。實機驗證：快照含每腿角色＋記分卡、市場日曆、模組單測皆正確。
- 2026-07-01（#134）— 完成 P1-1（leg_role 單一欄位＋手動覆蓋＋前後端統一讀取）。確認 A6 舊表（釘 f796371）已過時。
- 先前（#122–#133）— 曝險儀表 F1-F6、雙紅線上圖、出場建議重做、成交量副圖、爆倉/降槓桿錯誤推播刪除、指標None把關、去重冷卻、Bitget retry、安全硬化、Anthropic 後端代理、B5 突破加倉。

## 下一批建議（總表已全數 ✅；剩餘皆為可選增強）
- P6 之後：未平倉量成長率 vs 價格（需接 open interest 新資料源）——任務書列「本輪不做」。
- P4-2 擴充：把報價/K線/資金費各自年齡也顯示在對應數字旁（目前僅摘要顯示 Bitget 同步年齡）。
- 假日清單逐年維護（2028+）。
- 建議：每 2–3 層用**新對話/新 session** 外部重稽核一次（任務書 §3 誠實限制）。
</content>
