# 窩 Trading 系統實作現況
> 每次完成一批工作後更新此檔。**這是唯一現況真相來源**——查這裡，不用翻聊天記錄。
> 狀態圖例：✅已做 / 🟡部分(附缺口) / ❌未做 / ⚠️做錯或未驗證。

## 最後更新：2026-07-01 / commit（P1-1 這輪，見 git log 最新）

## 現況總表
| 項目 | 狀態 | 證據（檔:函式/行） | 缺口 |
|---|---|---|---|
| **P0-1** 危急聲音/紅閃＋頻率分級 | ✅ | 前端 `wo_trade.html:bufferAlarm()`（紅閃 `.bufflash` + WebAudio 嗶，危急<7% 每20s、警告<15% 每60s）；後端 `crypto.py` 爆倉預警 `bucket_for={7:300,12:900,20:3600}`（危急每5分≈近連續、警告15分、留意每小時，繞去重） | 分級門檻用 7/12/20%（非 20/12/7 字面，但語意一致） |
| **P1-1** 腿角色 `leg_role` 單一欄位＋全系統統一讀 | ✅ | 權威計算 `bitget_private.py:_tag_leg_roles()`（`|L−S|/(L+S)<0.2`→lock；小邊 insurance、大邊 directional；單腿 directional）。前端 `wo_trade.html:legRole()/isProtectiveLeg()/setLegRoleOverride()`（覆蓋>Bitget欄位>前端退路）；`posAdvice` 讀 `isProtectiveLeg`、`adviceLine` 分 🛡保險/⚖️對鎖、`maybeNotifyAdvice` 保險/對鎖腿早退。後端 `crypto.py` sync 帶 `leg_role`、`futu_bridge._perp_underlyings` 傳遞、`alert_engine.py` 減碼/回補路徑改讀 `leg_role`（退路：無欄位時同 perp 有多空腿即視對鎖）。手動覆蓋 UI 在持倉卡。**實測**：override 改 directional→lock、insurance 腿 near=true 推播數=0 | 後端 `_perp_underlyings` 僅在牛牛/FutuOpenD 開時觸發（美股本體背離路徑），使用者美股常關；主要保護在前端＋crypto.py |
| **P2-1** build_report 改按需、不自動推 AI | ✅ | `alert_engine.py:build_report()` AI 教練 `if key and use_ai`（`use_ai` 預設關）；自動 EOD 只推純績效數字 | — |
| **P2-2** 每日報告尾附「涵蓋/未涵蓋」行 | ❌ | 尚未加 | 下一批做 |
| **P2-3** 移除逐則 ai_vet | ✅ | `#130` 移除 `crypto._emit` 與 `alert_engine` 推播迴圈的 `ai_vet()` 呼叫；grep 僅剩 `def ai_vet`（dead，未被呼叫） | 可再刪 dead def（無害） |
| **P3-1** Bitget retry/backoff＋低緩衝加密輪詢 | 🟡 | retry ✅ `bitget_private.py:_get()`（0.4/0.8/1.6s，4xx 不重試）| 「低緩衝輪詢越密」尚未做（前端 `bitgetAutoSync` 仍固定 60s） |
| **P4-1** 結構化快照匯出＋每腿 leg_role | 🟡 | `copyToMax()` 已匯出快照＋曝險 | 尚未逐腿附 `leg_role`、未做 `/snapshot` 端點 |
| **P4-2** 數字新鮮度時戳/過期變灰 | 🟡 | Bitget 同步有 stale 判斷（`bufferAlarm` gate `!stale`） | 未逐欄位 `fetched_at`（報價/K線/資金費各自年齡） |
| **P5-1** 平保險腿兩意圖文案 | ✅ | `renderSimClose()`：① 主動換方向試算 ② 看錯回補損害回報 | — |
| **P5-2** 情境卡「距爆倉線」欄 | ✅ | `renderScenarioCard()` 表頭含「距爆倉」欄，`scenarioRow().liqPct` 顯示 | — |
| **P5-3** 單腿 marginRatio 交叉檢核 | ✅ | `bitget_private` 回 per-position `marginRatio`；儀表顯示「最高倉保證金率」 | 帳戶層級用 `crossedRiskRate`，per-leg marginRatio 顯示最大值 |
| **P6-1** pattern_signals.py（低波動蓄積、資金費異常） | ❌ | 尚未建模組 | 下一批做（P6） |
| **P7-1** 突破加倉引擎（B5） | ✅ | `breakoutAdd()/addHeadroom()/brkLine()`＋🚀雷達開關＋保守/積極。實測突破/RVOL/回踩/風控 headroom 正確 | — |
| **P8-1** 安全硬化（SHA/header token/金鑰後端） | ✅ | `futu_bridge`：`WO_HTML_REF` 可釘 SHA；token 預設只認 X-Token 標頭；`/claude` 代理讓金鑰留後端 | 預設仍 `/main`（自動更新），釘 SHA 為 opt-in |
| **P8-2** 美股市場日曆（假日/半日盤） | 🟡 | 時區 `America/New_York`（DST 由 Intl 處理）＋假日清單 | 半日盤（提早收盤）尚未特別處理 |
| **P8-3** 橋接單點故障降級提示 | ✅ | `/health` 回 `futu` 狀態；前端 `futu.futuLive` 降級；Bitget/加密路徑獨立於 FutuOpenD | — |

## 回歸檢查清單（累積增長，見任務書第3節 Step 3）
- [x] 全倉不對單腿算爆倉價、不對保險腿喊降槓桿（grep「止損比爆倉」「降槓桿或補保證金」= 0 筆，最後確認 2026-07-01）
- [x] 代幣化美股永續一律用 Bitget 標記價，不可用美股本體/幣安價（`cMark()`→`h.mark`；`crypto.py` 用 `p.get("mark")`）
- [x] 指標 None 不推等級A（`alert_engine._data_complete()` fail-closed，套 `eval_buy`/`eval_sell`）
- [x] 新出場推播去重冷卻仍生效（`maybeNotifyAdvice` lastT/lastReason/lastSl 30分冷卻，#129）
- [x] `leg_role` 由前端卡片/後端推播/快照三處一致讀取，無任一處自行重推（P1-1，2026-07-01）— 前端 `legRole()`、後端 `alert_engine` 讀 `p.get("leg_role")`；快照(P4-1)待補 leg_role 欄

## 歷史紀錄
- 2026-07-01 — 完成 P1-1（leg_role 單一欄位＋手動覆蓋＋前後端統一讀取），實機驗證覆蓋切換與保險腿零推播。同步更新：確認 P0-1/P2-1/P2-3/P5-x/P7-1/P8-1/P8-3 已於 #125–#133 完成（先前 A6 狀態表釘在 f796371 已過時）。未修新問題：無。
- 先前（#122–#133）— 曝險儀表 F1-F6、雙紅線上圖、出場建議重做、成交量副圖、爆倉/降槓桿錯誤推播刪除、指標None把關、去重冷卻、Bitget retry、安全硬化、Anthropic 後端代理、B5 突破加倉。

## 下一批建議（依任務書優先序）
1. **P2-2** 報告尾行「涵蓋/未涵蓋」（小、快）
2. **P3-1** 低緩衝加密輪詢加密（retry 已完成，補輪詢頻率）
3. **P4-1** `/snapshot` 結構化匯出＋每腿 leg_role（依賴 P1-1，已可做）
4. **P4-2** 逐欄位 fetched_at 新鮮度
5. **P6-1** pattern_signals.py（低波動蓄積＋資金費異常）
6. **P8-2** 半日盤日曆
</content>
