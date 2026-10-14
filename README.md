# codex-weekly-worklog

給研究群使用的 Codex 工作週報技能。讀取各電腦本機 Codex 紀錄，由程式計算用量、Codex 閱讀對話整理要求與交付，再產生可離線查看的單檔 HTML。預設截至昨天的七個完整日；也支援指定截止日及單日日報。

包含任務總覽、逐日統計、使用者要求原文與交付摘要、主控／子代理／內部審核用量，以及可取得的帳號額度快照。沒有另外呼叫付費 API 或要求 API key；Codex 讀取及摘要仍使用帳號用量。

## 安裝

需要可使用技能的 Codex，以及 Python 3.9 以上（腳本只用標準函式庫）。Windows 使用 PowerShell；手動複製也適用 macOS／Linux。實際對話來源格式可能隨 Codex 版本改變。

### 方法一：在 Codex 要求安裝

把這句貼入 Codex：

```text
請用 skill-installer 安裝 https://github.com/ctshih/codex-weekly-worklog/tree/main/codex-weekly-worklog
```

### 方法二：Windows 本機安裝

```powershell
git clone https://github.com/ctshih/codex-weekly-worklog.git
cd codex-weekly-worklog
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

安裝器預設使用 `~/.codex/skills/codex-weekly-worklog`（本專案驗證環境）；有設定 `CODEX_HOME` 時使用其 `skills` 子目錄。最新版官方文件列出的使用者技能目錄是 `~/.agents/skills`，可明確指定：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Destination "$HOME\.agents\skills\codex-weekly-worklog"
```

若既有同名技能，安裝器會停止，讓你先檢查舊版。macOS／Linux 可將儲存庫內 `codex-weekly-worklog` 資料夾複製至 `~/.agents/skills/`，或使用方法一。避免在兩個技能目錄重複安裝同名副本。若介面未顯示，重新開啟聊天或 Codex。

更新時先 `git pull`，再請 Codex「將此儲存庫的 codex-weekly-worklog 更新到我的技能目錄，保留備份並驗證」，避免未檢查就覆蓋個人修改。

## 第一次使用

```text
使用 $codex-weekly-worklog 產生工作週報，放在我的工作日誌目錄。
```

指定日期及目錄範例（請換成自己的路徑）：

```text
使用 $codex-weekly-worklog 統計截至 2026-10-13 的七天，報告放在 C:\Worklogs\Codex。
```

這個例子涵蓋台灣時間 10/07 00:00 至 10/14 00:00（終點不含）。明確要求「今天的日報」或某一天時改產生日報。

檔名為 `Codex工作週報-起日YYYYMMDD-迄日YYYYMMDD-電腦名稱.html`；名稱取來源 `stats.json.host`。同期間跨機同步時，各機器必須有不同電腦名稱，統計資料放 `data/起日-迄日/電腦名稱/`。這是各機器各自的週報，不會自動彙整多機用量。

## 設定每週自動排程

在每台要產報告的電腦上先安裝、手動產一次，確認能讀取來源並寫入輸出目錄。接著在 Codex app 貼上：

```text
請設定每週五 17:00（Asia/Taipei，台灣時間）自動使用 $codex-weekly-worklog 產生週報。
統計截至前一天的七個完整日（上週五至本週四），放在 C:\Worklogs\Codex。
HTML 檔名必須包含 stats.json 的來源電腦名稱：Codex工作週報-起日YYYYMMDD-迄日YYYYMMDD-電腦名稱.html。
stats、summaries、usage 與驗證紀錄存於 data\起日-迄日\電腦名稱。
依技能完成收集、閱讀實際交付補摘要、核對逐日加總、render 與 selfcheck；摘要缺漏只保留草稿，不覆蓋完整報告。
完成後提供報告連結；失敗時說明原因。帳號額度標明實際取得時間，不冒充週末歷史餘額。
```

將 `C:\Worklogs\Codex` 換成自己的輸出或 Synology Drive 同步目錄，然後確認 Codex 顯示的排程時間、時區與啟用狀態。可用「查看／修改／暫停我的 Codex 每週工作週報排程」管理。安裝技能不會自動建立排程；排程設定需在各執行機器上完成。

讀取本機資料的排程須保持電腦開機、桌面 app 執行及專案目錄可存取；請檢查執行歷史及首次排程結果。帳號、管理政策及版本可能影響功能可用性。只有排純 Python renderer 無法更新對話摘要，需由 Codex 執行完整技能流程。參考 [OpenAI 技能說明](https://developers.openai.com/codex/skills) 與 [排程說明](https://developers.openai.com/codex/app/automations)。

## 統計口徑與限制

- 只計指定半開時間區間。跨週要求保留作背景，不算本週的新輸入。
- 新格式依 response ID 去重，舊格式使用累積差分；缺 response ID 的舊 fork 無法保證跨檔完全去重。
- input 已包含 cached input，output 已包含 reasoning output；快取與推理不再加一次。token 數不等於扣款或帳號額度百分比。
- 含本機封存及可取得的子代理紀錄。未下載的遠端／雲端紀錄不在範圍，來源警告會保留。
- 帳號額度是取得當下的快照，不是統計截止時的歷史額度；取不到時顯示未取得。
- 成果摘要依原對話交付證據整理，沒有重新驗收原專案。正式成果、候選與待驗證數分開。

## 隱私與同步

HTML 包含對話原文、工作目錄與摘要，可能有研究內容；請按研究群規則選擇同步與分享範圍。技能不自動上傳報告或發布對話。本儲存庫僅含程式、技能、模板及合成測試，不含實際工作紀錄、額度快照或私人報告。Synology Drive 的安裝、同步權限與設定需自行完成。

## 開發與測試

```text
python -W error -m unittest discover -s tests -v
```

10 項測試涵蓋週區間、跨年、去重、跨日邊界、子代理歸屬、自動觸發、額度缺值與摘要缺漏保留完整報告。程式來源位於 `codex-weekly-worklog/scripts/`；使用流程與資料結構見 [SKILL.md](codex-weekly-worklog/SKILL.md)。

參考 [ctshih/claude-daily-worklog](https://github.com/ctshih/claude-daily-worklog) 的工作日誌設計，獨立改寫 Codex 資料來源與計量方式。
