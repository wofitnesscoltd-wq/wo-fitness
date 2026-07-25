# 進度快照：wofitnesscoltd-wq/wo-fitness

> 產生時間：2026-06-13（對賬員自動產生）
> 對賬依據：git 提交時間、磁碟檔案狀態、設定檔存在性（本 repo 無雲端部署可查）

---

## 1.【系統真相】

**最新提交**
- `ad40141` — 2026-04-27 01:15:39 +0800 — "Rename wo_admin (2).html to wo_admin.html"
- 上一筆：`bafa629` — 2026-04-27 01:03:00 +0800 — "Add files via upload"
- **整個 repo 只有 2 筆 commit。**

**各關鍵檔案最後改動時間**
| 檔案 | 最後改動 | commit |
|---|---|---|
| `wo_admin.html` | 2026-04-27 01:15:39 | ad40141 |
| `wo_staff_query.html` | 2026-04-27 01:03:00 | bafa629 |
| `wo_teacher_schedule.html` | 2026-04-27 01:03:00 | bafa629 |

**檔名版號**：無任何 `_v` 版號檔。此專案不採版號檔命名。

**雲端部署**：無 wrangler、無 wrangler.toml/jsonc、無 package.json。**此 repo 不是 Cloudflare Workers 專案，沒有雲端部署可對賬。**

**使用的 AI 模型**：`wo_staff_query.html:627` 與 `wo_teacher_schedule.html:828` 皆寫死 `claude-sonnet-4-20250514`（Claude Sonnet 4）。與「Fable 5」無關。

---

## 2.【本對話貢獻】

本對話（session 主題：fable5 模型版本查詢）做了：
- 查證員工查詢／老師時間系統實際使用的模型 → `claude-sonnet-4-20250514`。
- 釐清「Fable 5 被關掉」的傳聞對本 repo 無影響（本來就沒用 Fable 5）。
- 尋找使用者所指的「數位客服」→ 確認本 repo 內不存在該頁面（過去歷史亦無）。
- 建立分支 `claude/fable5-model-version-check-nb1jo0`。

**對應提交**：無。本對話至今**未做任何 commit、未 deploy**，屬「只在對話裡調查」階段。

---

## 3.【⚠️ 孤兒工作警示】

本 repo 內：**無孤兒工作**。
- `git status --short`：空
- `git stash list`：空
- `git diff --stat`：無未提交差異
- 未推送 commit（branches not remotes）：無
- 未追蹤檔案：無

→ 本 repo 可安全 `git clone`，不會遺失任何東西。

**但真正的風險在別處（見下）：**
- 使用者要找的「數位客服 / 數位改革」專案**不在本 repo**。它預期具備的 wrangler 部署、`wo_linechat_v*.js` 版號檔、多輪官網改動歷史，本 repo 全部沒有。
- 該專案若存在，是在**另一個 repo 或另一個（已 Disconnected 的）遠端 session** 裡。那些 session 的容器是用完即丟，未 push 的成果才是真正可能遺失的孤兒工作——但那要在「數位改革」那個 session／repo 裡跑本提示詞才驗得到，本 repo 驗不到。

---

## 4.【判斷依據】

| 結論 | 來自哪個指令輸出 |
|---|---|
| 最新狀態＝2026-04-27 的 3 個 HTML | `git log -30 --format` |
| 無孤兒工作 | `git status --short` / `git stash list` / `git diff --stat` / `git log --branches --not --remotes` 全空 |
| 非 Cloudflare 專案、無部署可對賬 | 檔案系統檢查：無 wrangler.toml/jsonc、無 wrangler CLI |
| 無版號檔 | `git ls-files \| grep -E '_v[0-9]'` 無輸出 |
| 使用模型 Sonnet 4 | 讀取 `wo_staff_query.html:627`、`wo_teacher_schedule.html:828` |
| 數位客服不在本 repo | 上述所有輸出 + 全 repo 僅 3 個 HTML 檔 |

---

## 一句話結論

> 本 `wo-fitness` repo 的真實最新狀態＝2026-04-27 上傳的 3 個靜態 HTML（員工查詢／老師時間／規則後台），乾淨無孤兒工作，clone 即得全部。**但你要找的「數位客服」不在這個 repo**——它在另一個 repo 或另一個已斷線的 session，需要到那邊去對賬，本 repo 幫不上那件事。
