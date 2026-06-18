#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
誠品線上「戰鬥陀螺」上架監控
================================
定時呼叫誠品線上的搜尋 API,偵測「新上架」的商品。第一次執行會把目前
所有結果記錄成基準(不通知);之後每次執行,只要出現新的商品 ID,就會
跳 Windows 桌面通知 + 響鈴,並寫進 log。

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

# 商品名稱必須包含這個字串才算數(留空 = 不過濾)。例如想盯特定型號可填 "CX-18"
NAME_MUST_INCLUDE = ""

ENABLE_TOAST = True   # 桌面通知(電腦上)
ENABLE_SOUND = True   # 響鈴
# LINE / Telegram 的金鑰放在同資料夾的 config.json(見 README)。
# 有填 token 才會推播到手機,沒填就自動略過。

API = "https://athena.eslite.com/api/v2/search"
SIZE = 40             # 該 API 單次最多回 40 筆
TIMEOUT = 30
RETRIES = 3
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

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
def fetch(keyword):
    """呼叫誠品搜尋 API,回傳 (found 總數, 已正規化的商品 list)。"""
    url = f"{API}?q={urllib.parse.quote(keyword)}&size={SIZE}&start=0"
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.load(r)
            hits = data.get("hits", {})
            items = [normalize(h) for h in hits.get("hit", [])]
            return hits.get("found", len(items)), items
        except Exception as e:
            last_err = e
            if attempt < RETRIES:
                time.sleep(3 * attempt)
    log(f"(查詢「{keyword}」失敗,已重試 {RETRIES} 次:{last_err})")
    return None, []


def normalize(hit):
    f = hit.get("fields", {}) or {}
    mfr = f.get("manufacturer") or []
    if isinstance(mfr, list):
        mfr = "/".join(x for x in mfr if x)
    return {
        "id": hit.get("id", ""),
        "name": f.get("name", ""),
        "price": str(f.get("final_price", "")),
        "list_price": str(f.get("mprice", "")),
        "url": f.get("url", ""),
        "stock": str(f.get("stock", "")),
        "create_date": f.get("create_date", ""),
        "is_book": f.get("is_book", ""),
        "manufacturer": mfr,
    }


def keep(item):
    """套用使用者設定的過濾條件。"""
    if ONLY_NON_BOOK and str(item.get("is_book")).lower() != "no":
        return False
    if NAME_MUST_INCLUDE and NAME_MUST_INCLUDE not in item.get("name", ""):
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


def label(item):
    tag = "🧸玩具/周邊" if str(item.get("is_book")).lower() == "no" else "📖書籍"
    return tag


# ── 主要邏輯:檢查一次 ─────────────────────────────────────────────
def check():
    ok, found, current = gather()
    if not ok:
        log("⚠ 這次查詢全部失敗,略過(不更動基準)。")
        return

    state = load_state()

    # 第一次執行:建立基準,不通知
    if state is None or not state.get("items"):
        items = {}
        for cid, it in current.items():
            it = dict(it)
            it["first_seen"] = now_str()
            it["last_seen"] = now_str()
            items[cid] = it
        save_state({"created": now_str(), "last_check": now_str(), "items": items})
        log(f"✅ 已建立基準:目前共 {len(items)} 項商品(誠品回報關鍵字總數約 {found} 筆)。")
        log("   之後再執行,只要有『新上架』就會通知你。")
        return

    known = state["items"]
    new_ids = [cid for cid in current if cid not in known]

    # 沒有新品就不重寫 state(雲端時可避免每 5 分鐘都產生一筆 commit)
    if not new_ids:
        log(f"沒有新上架(目前監控 {len(known)} 項,本次查到 {len(current)} 項)。")
        return

    log(f"🎉 發現 {len(new_ids)} 項新上架商品!")
    names = []
    push_lines = [f"🎉 誠品新上架戰鬥陀螺 {len(new_ids)} 項!", ""]
    for cid in new_ids:
        it = current[cid]
        names.append(it["name"])
        log(f"   ★ {label(it)} NT${it['price']}  {it['name']}")
        log(f"     {it['url']}")
        push_lines.append(f"🧸 {it['name']}")
        push_lines.append(f"NT${it['price']}  {it['url']}")
        push_lines.append("")
        # 記進基準
        it = dict(it)
        it["first_seen"] = now_str()
        it["last_seen"] = now_str()
        known[cid] = it

    # 桌面通知(電腦端,雲端會自動略過)
    head = names[0][:40]
    if len(names) > 1:
        head += f" 等 {len(names)} 項"
    toast("誠品新上架戰鬥陀螺!", head)
    beep()
    # 手機通知(LINE / Telegram)
    push_phone("\n".join(push_lines).strip())

    state["items"] = known
    state["last_check"] = now_str()
    save_state(state)


# ── 其他指令 ───────────────────────────────────────────────────────
def cmd_list():
    state = load_state()
    if not state or not state.get("items"):
        print("目前沒有任何基準資料,請先執行一次 python eslite_watch.py")
        return
    items = sorted(state["items"].values(), key=lambda x: x.get("first_seen", ""), reverse=True)
    print(f"目前監控 {len(items)} 項(依首次發現時間排序):\n")
    for it in items:
        print(f"  {label(it)} NT${it['price']:>5}  {it['name']}")
        print(f"     首次發現 {it.get('first_seen','?')} | {it['url']}")
    print(f"\n上次檢查:{state.get('last_check','?')}")


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
