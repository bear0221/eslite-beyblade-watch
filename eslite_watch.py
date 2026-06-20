#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
誠品線上「戰鬥陀螺」上架/補貨監控
================================
定時呼叫誠品線上的 holmes 搜尋 API(會自動翻頁抓全部結果),偵測:
  (1) 新上架:出現基準裡沒看過的商品。
  (2) 補貨可購買:既有商品從不可買變成可加購物車。
第一次執行會把目前所有結果記錄成基準(不通知);之後有上述狀況就跳
Windows 桌面通知 + 響鈴 + 推 LINE/Telegram 到手機,並寫進 log。

用法:
    python eslite_watch.py                 # 跑一次(適合給 Windows 工作排程器定時呼叫)
    python eslite_watch.py --loop 30        # 常駐,每 30 分鐘檢查一次
    python eslite_watch.py --list           # 列出目前已記錄的商品
    python eslite_watch.py --reset          # 清掉基準,下次執行重新建立
    python eslite_watch.py --test           # 測試通知與響鈴
    python eslite_watch.py --install-task 30   # 註冊 Windows 工作排程,每 30 分鐘自動跑
    python eslite_watch.py --uninstall-task    # 移除上述排程

只用 Python 標準函式庫,不需 pip 安裝任何套件。
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

# ── 讓 Windows 主控台能正確輸出中文 ────────────────────────────────
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── 設定 ───────────────────────────────────────────────────────────
# 要監控的關鍵字(可多個)。想更精準可改成例如 ["BEYBLADE X", "UX-", "CX-"]
KEYWORDS = ["戰鬥陀螺"]

# 只關心玩具/周邊、不想被「書籍/雜誌」洗版時,設成 True
ONLY_NON_BOOK = True

# 商品名稱必須「至少包含其中一個」關鍵字才算數(避免 holmes 模糊比對撈進不相關商品,
# 例如森林家族、書籍)。大小寫不分。留空 list = 不過濾。
# 想只盯 TOMY 正版那條線,可改成 ["BEYBLADE X"]。
NAME_INCLUDE_ANY = ["陀螺", "BEYBLADE"]

# 商品名稱必須包含這個字串才算數(留空 = 不過濾)。例如想盯特定型號可填 "CX-18"
NAME_MUST_INCLUDE = ""

ENABLE_TOAST = True   # 桌面通知(電腦上)
ENABLE_SOUND = True   # 響鈴
# LINE / Telegram 的金鑰放在同資料夾的 config.json(見 README)。
# 有填 token 才會推播到手機,沒填就自動略過。

# 用誠品的 holmes 搜尋 endpoint:支援正常翻頁(page_no/page_size,一次最多 100),
# 而且回傳 availability / button_status 等真實庫存狀態(athena 那支沒有、且只給前 40 筆)。
HOLMES_API = "https://holmes.eslite.com/v1/search"
PAGE_SIZE = 100       # holmes 單頁上限,通常一兩頁就抓完
MAX_PAGES = 20        # 安全上限,避免極端情況無限翻頁
TIMEOUT = 30
RETRIES = 3
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 狀態結構版本。改變資料來源/欄位/過濾規則時 +1,程式偵測到版本不符會自動重建基準(不誤報)。
SCHEMA = 3

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
LOG_FILE = os.path.join(HERE, "watch.log")
TOAST_PS1 = os.path.join(HERE, "toast.ps1")
CONFIG_FILE = os.path.join(HERE, "config.json")
TASK_NAME = "EsliteBeybladeWatch"


def load_config():
    """讀取 config.json(LINE / Telegram 金鑰)。檔案不存在就回空字典。"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── 工具函式 ───────────────────────────────────────────────────────
def now_str():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    """同時印到畫面與寫進 log 檔。"""
    line = f"[{now_str()}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def beep():
    if not ENABLE_SOUND or os.name != "nt":
        return  # 雲端/非 Windows 無喇叭,直接略過
    try:
        import winsound
        for _ in range(2):
            winsound.Beep(880, 250)
            winsound.Beep(660, 250)
    except Exception:
        pass


def toast(title, message):
    if not ENABLE_TOAST or os.name != "nt":
        return  # 雲端/非 Windows 沒有桌面通知,直接略過
    try:
        env = dict(os.environ)
        env["ESLITE_TOAST_TITLE"] = title
        env["ESLITE_TOAST_MSG"] = message
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW  # 不要每次都閃一個黑視窗
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", TOAST_PS1],
            env=env, timeout=20, creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log(f"(通知顯示失敗,可忽略:{e})")


def send_line(text):
    """用 LINE Messaging API 的 broadcast 推播到手機(發給所有把這個 bot 加好友的人=你自己)。
    需要在 config.json 填 line_token,或設環境變數 ESLITE_LINE_TOKEN。"""
    token = (load_config().get("line_token") or os.environ.get("ESLITE_LINE_TOKEN", "")).strip()
    if not token:
        return False
    try:
        body = json.dumps({"messages": [{"type": "text", "text": text[:4900]}]}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/broadcast", data=body, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        log("📱 已發送 LINE 通知。")
        return True
    except Exception as e:
        log(f"(LINE 通知失敗:{e})")
        return False


def send_telegram(text):
    """選用:若在 config.json 填了 telegram_bot_token + telegram_chat_id 就一併推播。"""
    cfg = load_config()
    tok = (cfg.get("telegram_bot_token") or "").strip()
    chat = (cfg.get("telegram_chat_id") or "").strip()
    if not (tok and chat):
        return False
    try:
        body = urllib.parse.urlencode({
            "chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=body, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        log("📱 已發送 Telegram 通知。")
        return True
    except Exception as e:
        log(f"(Telegram 通知失敗:{e})")
        return False


def push_phone(text):
    """把訊息推到手機(LINE / Telegram 任一有設定就送)。"""
    sent = send_line(text)
    sent = send_telegram(text) or sent
    return sent


# ── 抓資料 ─────────────────────────────────────────────────────────
def _holmes_page(keyword, page_no):
    """抓 holmes 的某一頁,回傳 JSON dict(失敗回 None)。"""
    qs = urllib.parse.urlencode({"q": keyword, "page_size": PAGE_SIZE, "page_no": page_no})
    req = urllib.request.Request(f"{HOLMES_API}?{qs}", headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        # holmes 是 v3 endpoint,少了這個 header 會回 400
        "content-type": "application/x-www-form-urlencoded",
    })
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except Exception as e:
            last_err = e
            if attempt < RETRIES:
                time.sleep(3 * attempt)
    log(f"(查詢「{keyword}」第 {page_no} 頁失敗,已重試 {RETRIES} 次:{last_err})")
    return None


def fetch(keyword):
    """抓某關鍵字的『全部』結果(自動翻頁),回傳 (總數, 已正規化的商品 list)。"""
    items = {}
    total = None
    for page_no in range(1, MAX_PAGES + 1):
        data = _holmes_page(keyword, page_no)
        if data is None:
            # 第一頁就失敗 → 視為查詢失敗;後續頁失敗 → 用已抓到的部分
            return (None, []) if page_no == 1 else (total, list(items.values()))
        results = data.get("results") or []
        if total is None:
            try:
                total = int(data.get("total_size", 0) or 0)
            except (TypeError, ValueError):
                total = 0
        for r in results:
            if r.get("id"):
                items[r["id"]] = normalize(r)
        if not results or len(items) >= (total or 0):
            break
    return (total if total is not None else len(items)), list(items.values())


def normalize(r):
    """把 holmes 的一筆 result 轉成本程式用的格式。
    buyable = 能不能直接加購物車買(button_status == add_to_shopping_cart)。"""
    mfr = r.get("manufacturers") or r.get("brands") or []
    if isinstance(mfr, list):
        mfr = "/".join(x for x in mfr if x)
    pid = r.get("id", "")
    btn = r.get("button_status", "")
    return {
        "id": pid,
        "name": r.get("name", ""),
        "price": str(r.get("final_price", "")),
        "url": f"https://www.eslite.com/product/{pid}",
        "availability": r.get("availability", ""),      # IN_STOCK / OUT_OF_STOCK
        "button_status": btn,                            # add_to_shopping_cart 等
        "buyable": btn == "add_to_shopping_cart",        # 真的可以買
        "is_book": r.get("is_book", ""),
        "manufacturer": mfr,
    }


def keep(item):
    """套用使用者設定的過濾條件。"""
    name = item.get("name", "")
    if ONLY_NON_BOOK and str(item.get("is_book")).lower() != "no":
        return False
    if NAME_INCLUDE_ANY:
        upper = name.upper()
        if not any(kw.upper() in upper for kw in NAME_INCLUDE_ANY):
            return False
    if NAME_MUST_INCLUDE and NAME_MUST_INCLUDE not in name:
        return False
    return True


def gather():
    """合併所有關鍵字的結果,以商品 ID 去重。"""
    merged = {}
    total_found = 0
    ok = False
    for kw in KEYWORDS:
        found, items = fetch(kw)
        if found is not None:
            ok = True
            try:
                total_found += int(found)
            except (TypeError, ValueError):
                pass
        for it in items:
            if it["id"] and keep(it):
                merged[it["id"]] = it
    return ok, total_found, merged


# ── 狀態存取 ───────────────────────────────────────────────────────
def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def item_lines(it):
    """組一個商品在手機通知裡顯示的幾行。可購買 → 給明確的『立即購買』連結。"""
    if it.get("buyable"):
        return [f"🧸 {it['name']}",
                f"NT${it['price']} ✅可購買",
                f"👉 立即購買:{it['url']}", ""]
    return [f"🧸 {it['name']}",
            f"NT${it['price']} ⏳尚未開賣",
            f"🔗 看商品:{it['url']}", ""]


# ── 主要邏輯:檢查一次 ─────────────────────────────────────────────
def check():
    ok, found, current = gather()
    if not ok:
        log("⚠ 這次查詢全部失敗,略過(不更動基準)。")
        return

    state = load_state()

    # 第一次執行,或狀態結構版本不符(例如換了資料來源)→ 重建基準,不通知
    if state is None or not state.get("items") or state.get("schema") != SCHEMA:
        items = {}
        for cid, it in current.items():
            rec = dict(it)
            rec["first_seen"] = now_str()
            rec["last_seen"] = now_str()
            items[cid] = rec
        save_state({"schema": SCHEMA, "created": now_str(),
                    "last_check": now_str(), "items": items})
        buyable_n = sum(1 for it in current.values() if it.get("buyable"))
        log(f"✅ 已建立基準:共 {len(items)} 項(其中可購買 {buyable_n} 項,總數約 {found} 筆)。")
        log("   之後若有『新上架』或『舊品補貨可購買』都會通知你。")
        return

    known = state["items"]
    new_items, restock_items = [], []
    buyable_flips = 0

    for cid, it in current.items():
        if cid not in known:
            new_items.append(it)
            continue
        prev_buyable = bool(known[cid].get("buyable"))
        now_buyable = bool(it.get("buyable"))
        if prev_buyable != now_buyable:
            buyable_flips += 1
            if now_buyable and not prev_buyable:
                restock_items.append(it)        # 不可買 → 可買 = 補貨
        # 更新既有商品的即時狀態(供下次比對)
        known[cid].update(
            buyable=now_buyable,
            availability=it.get("availability", ""),
            button_status=it.get("button_status", ""),
            price=it.get("price", ""),
            last_seen=now_str(),
        )

    # 把新商品記進基準
    for it in new_items:
        rec = dict(it)
        rec["first_seen"] = now_str()
        rec["last_seen"] = now_str()
        known[rec["id"]] = rec

    changed = bool(new_items) or buyable_flips > 0
    notify = bool(new_items) or bool(restock_items)

    if not changed:
        buyable_n = sum(1 for v in known.values() if v.get("buyable"))
        log(f"沒有變化(監控 {len(known)} 項,可購買 {buyable_n} 項)。")
        return

    if notify:
        push_lines, head_parts = [], []
        if new_items:
            log(f"🎉 發現 {len(new_items)} 項新上架!")
            head_parts.append(f"新上架 {len(new_items)} 項")
            push_lines.append(f"🎉 新上架 {len(new_items)} 項")
            for it in new_items:
                tag = "✅可購買" if it.get("buyable") else "⏳尚未開賣"
                log(f"   ★ NT${it['price']} {tag}  {it['name']}")
                log(f"     {it['url']}")
                push_lines += item_lines(it)
        if restock_items:
            log(f"♻️ {len(restock_items)} 項舊品補貨、現在可購買!")
            head_parts.append(f"補貨 {len(restock_items)} 項")
            push_lines.append(f"♻️ 補貨可購買 {len(restock_items)} 項")
            for it in restock_items:
                log(f"   ★ NT${it['price']} ✅可購買  {it['name']}")
                log(f"     {it['url']}")
                push_lines += item_lines(it)
        first = (new_items + restock_items)[0]
        toast("誠品戰鬥陀螺:" + "、".join(head_parts), first["name"][:40])
        beep()
        push_phone("\n".join(push_lines).strip())
    else:
        # 只有「可買→不可買(售出)」的變化:記錄狀態但不打擾你
        log(f"狀態更新:{buyable_flips} 項庫存變動(無新上架/補貨,不通知)。")

    state["items"] = known
    state["last_check"] = now_str()
    save_state(state)


# ── 其他指令 ───────────────────────────────────────────────────────
def cmd_list():
    state = load_state()
    if not state or not state.get("items"):
        print("目前沒有任何基準資料,請先執行一次 python eslite_watch.py")
        return
    items = sorted(state["items"].values(),
                   key=lambda x: (not x.get("buyable"), x.get("first_seen", "")))
    buyable_n = sum(1 for it in items if it.get("buyable"))
    print(f"目前監控 {len(items)} 項(可購買 {buyable_n} 項,可購買者排前面):\n")
    for it in items:
        tag = "✅可購買" if it.get("buyable") else "⏳缺貨/未開賣"
        print(f"  {tag}  NT${it['price']:>5}  {it['name']}")
        print(f"     {it['url']}")
    print(f"\n上次更新:{state.get('last_check','?')}")


def cmd_test():
    log("發送測試通知…")
    toast("誠品戰鬥陀螺監控(測試)", "如果你看到這個通知與聽到嗶聲,代表桌面通知正常。")
    beep()
    log("若右下角有跳出通知就代表桌面通知 OK。")
    cfg = load_config()
    has_line = bool((cfg.get("line_token") or os.environ.get("ESLITE_LINE_TOKEN", "")).strip())
    has_tg = bool(cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"))
    if not (has_line or has_tg):
        log("尚未設定 LINE / Telegram 金鑰(config.json),略過手機推播測試。")
        return
    sent = push_phone("✅ 誠品戰鬥陀螺監控測試:你的手機推播設定正常,之後有新上架就會這樣通知你。")
    if sent:
        log("已嘗試推播到手機,請檢查你的 LINE / Telegram。")


def python_for_task():
    """排程時優先用 pythonw.exe,背景執行不會閃黑視窗。"""
    exe = sys.executable or "python"
    pyw = exe.replace("python.exe", "pythonw.exe")
    return pyw if os.path.exists(pyw) else exe


def cmd_install_task(minutes):
    script = os.path.abspath(__file__)
    tr = f'"{python_for_task()}" "{script}"'
    cmd = [
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/TR", tr,
        "/SC", "MINUTE", "/MO", str(minutes),
        "/IT",          # 只在使用者登入時執行(這樣通知才看得到)
        "/F",           # 覆蓋同名工作
    ]
    print("註冊工作排程指令:\n  " + " ".join(cmd) + "\n")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout or r.stderr)
    if r.returncode == 0:
        print(f"✅ 已建立排程「{TASK_NAME}」,每 {minutes} 分鐘自動檢查一次。")
        print("   先手動跑一次以建立基準:python eslite_watch.py")
    else:
        print("❌ 建立排程失敗(可能需要系統管理員權限,或改用下方手動指令)。")


def cmd_uninstall_task():
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)


# ── 進入點 ─────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="誠品線上戰鬥陀螺上架監控")
    p.add_argument("--loop", type=int, metavar="MIN",
                   help="常駐模式,每 MIN 分鐘檢查一次")
    p.add_argument("--list", action="store_true", help="列出已記錄的商品")
    p.add_argument("--reset", action="store_true", help="清除基準,下次重新建立")
    p.add_argument("--test", action="store_true", help="測試通知與響鈴")
    p.add_argument("--keywords", type=str,
                   help="覆蓋監控關鍵字,以逗號分隔,例如 --keywords \"戰鬥陀螺,BEYBLADE X\"")
    p.add_argument("--install-task", type=int, metavar="MIN", nargs="?", const=30,
                   help="註冊 Windows 工作排程,每 MIN 分鐘自動執行(預設 30)")
    p.add_argument("--uninstall-task", action="store_true", help="移除工作排程")
    args = p.parse_args()

    global KEYWORDS
    if args.keywords:
        KEYWORDS = [k.strip() for k in args.keywords.split(",") if k.strip()]

    if args.reset:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        print("已清除基準,下次執行會重新建立。")
        return
    if args.list:
        cmd_list(); return
    if args.test:
        cmd_test(); return
    if args.install_task is not None:
        cmd_install_task(args.install_task); return
    if args.uninstall_task:
        cmd_uninstall_task(); return

    log(f"監控關鍵字:{KEYWORDS}")
    if args.loop:
        log(f"常駐模式啟動,每 {args.loop} 分鐘檢查一次(Ctrl+C 結束)。")
        while True:
            try:
                check()
            except KeyboardInterrupt:
                log("已停止。"); break
            except Exception as e:
                log(f"本輪發生例外(已忽略,繼續):{e}")
            try:
                time.sleep(args.loop * 60)
            except KeyboardInterrupt:
                log("已停止。"); break
    else:
        check()


if __name__ == "__main__":
    main()
