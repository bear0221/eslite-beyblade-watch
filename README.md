# 誠品線上「戰鬥陀螺」上架監控

定時呼叫誠品線上的搜尋 API,偵測**新上架**的戰鬥陀螺玩具。一發現新品就:
- 📱 推 **LINE**(或 Telegram)通知到你手機
- 🔔 電腦跳桌面通知 + 響鈴
- 📝 寫進 `watch.log`

只用 Python 標準函式庫,**不需安裝任何套件**。

## 目前狀態:☁️ 已部署到雲端(GitHub Actions)
- Repo:https://github.com/bear0221/eslite-beyblade-watch(公開,Actions 免費不限時數)
- **每 5 分鐘**由 GitHub 雲端自動檢查,**電腦關機也會跑**。
- 只看**玩具/周邊**;有新上架就推 **LINE** 到手機。
- LINE 金鑰存在 repo 的 **Secrets**(`ESLITE_LINE_TOKEN`,加密),不在程式碼裡。
- 已看過的商品記在 repo 的 `state.json`,雲端每次自己讀寫(只有出現新品才 commit)。
- 本機的 Windows 排程已移除,避免和雲端重複通知(本機指令仍可手動用)。

### 雲端管理(用 GitHub CLI;gh 已安裝)
```powershell
$gh = "C:\Program Files\GitHub CLI\gh.exe"
$repo = "bear0221/eslite-beyblade-watch"
& $gh run list  --repo $repo --limit 5          # 看最近幾次執行
& $gh workflow run watch.yml --repo $repo        # 立刻手動跑一次
& $gh secret set ESLITE_LINE_TOKEN --repo $repo  # 換 LINE 金鑰(會問你貼新值)
```
- **改頻率**:編輯 `.github/workflows/watch.yml` 裡的 `cron: '*/5 * * * *'`(例如 `*/15` 改成 15 分鐘),commit 後 `git push`。
- **暫停**:到 repo 網頁 → Actions 分頁 → 左側選 workflow → 右上 `•••` → Disable workflow。
- ⚠️ GitHub 排程實際可能略有延遲;且若 repo 連續 60 天沒有任何 commit,排程會被自動停用(到 Actions 頁面按 Enable 即可恢復)。

## 運作方式
- 資料來源:誠品官方搜尋 API `https://athena.eslite.com/api/v2/search`(誠品網站自己也是打這支)。
- 第一次執行把目前所有結果存成「基準」(`state.json`),**不通知**。
- 之後每次只要出現基準裡沒有的商品 ID,就判定「新上架」並通知。

## 設定手機 LINE 通知(一次性)
> LINE 舊的「LINE Notify」已於 2025/3/31 停止服務,所以改用官方 **Messaging API**(免費)。

1. 用電腦到 **LINE Developers**:https://developers.line.biz/console/ ,用你的 LINE 帳號登入。
2. 建一個 **Provider**(隨便取名,例如自己的暱稱)。
3. 在該 Provider 下 **Create a Messaging API channel**(建立 Messaging API 頻道),填基本資料送出。
   - 過程中若被導到「LINE Official Account Manager」,照畫面建立官方帳號即可。
4. 進入頻道的 **Messaging API** 分頁:
   - 用手機 LINE **掃描該頁的 QR code**,把這個 bot **加為好友**(這樣它才能推播給你)。
   - 找到 **Channel access token (long-lived / 長期)**,按 **Issue** 產生並複製。
5. 打開本資料夾的 `config.json`,把 token 貼進 `line_token`:
   ```json
   { "line_token": "你剛剛複製的長字串" }
   ```
6. 測試:`python eslite_watch.py --test` → 手機應該收到一則 LINE 測試訊息。

完成後就不用再管它,有新上架自動推播。

### (替代方案)覺得 LINE 申請太麻煩?用 Telegram 更快
建 bot:手機開 Telegram 找 `@BotFather` → `/newbot` → 拿到 token;再找 `@userinfobot` 拿你的 chat id。
兩個值填進 `config.json` 的 `telegram_bot_token` / `telegram_chat_id` 即可,LINE 可留空。

## 常用指令
| 指令 | 說明 |
| --- | --- |
| `python eslite_watch.py` | 檢查一次(排程器就是呼叫這個) |
| `python eslite_watch.py --test` | 測試桌面通知 + 手機推播 |
| `python eslite_watch.py --list` | 列出目前已記錄的商品 |
| `python eslite_watch.py --loop 5` | 改用「開著視窗常駐」每 5 分鐘檢查 |
| `python eslite_watch.py --reset` | 清掉基準,下次重新建立 |
| `python eslite_watch.py --keywords "戰鬥陀螺,BEYBLADE X"` | 臨時換關鍵字 |

## 工作排程(已幫你裝好)
```powershell
python eslite_watch.py --install-task 5     # 每 5 分鐘(已執行過)
python eslite_watch.py --uninstall-task     # 不想用了就移除
```
> 排程只在你**登入 Windows 時**於背景執行(這樣通知才看得到)。電腦關機/登出期間不會檢查。
> 想要 24 小時不關機也能跑,可改放雲端(例如 GitHub Actions 定時觸發)——需要再跟我說。

## 自訂(改 `eslite_watch.py` 最上面「設定」區)
- `KEYWORDS`:監控關鍵字。想更精準可改 `["BEYBLADE X", "CX-", "UX-"]`。
- `ONLY_NON_BOOK`:目前 `True`(只看玩具)。想連書籍雜誌也通知改 `False`。
- `NAME_MUST_INCLUDE`:只盯名稱含特定字串(例如 `"CX-18"`)。
- `ENABLE_TOAST` / `ENABLE_SOUND`:電腦端通知與響鈴開關。

## 檔案
- `eslite_watch.py` — 主程式
- `config.json` — LINE / Telegram 金鑰
- `toast.ps1` — 顯示 Windows 桌面通知
- `state.json` — 已記錄商品基準(刪掉=重設)
- `watch.log` — 每次檢查紀錄

## 備註
- 該 API 單次最多回 40 筆,程式監控搜尋結果前 40 名(依相關度排序,新品通常排前面;目前約 39 項玩具)。
- 「上架」= 商品出現在誠品搜尋結果。庫存(`stock`)欄位誠品給的值不可靠,故以「是否被列出」為準。
