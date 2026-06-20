#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eslite_watch 的單元測試(只用標準庫 unittest,不需安裝套件)。

執行:
    python -m unittest -v test_eslite_watch
    或
    python test_eslite_watch.py

測試不會碰網路、不會真的發通知:把 gather() 換成假資料,
通知函式(toast/beep/push_phone)換成記錄器,狀態檔導到暫存目錄。
重點覆蓋:新上架偵測、舊品補貨偵測,以及幾個邊界情況。
"""
import json
import os
import tempfile
import unittest

import eslite_watch as ew


def make_item(pid, name=None, price="350", buyable=False, is_book="no"):
    """產生一筆 normalize() 格式的商品。buyable 決定可否加購物車。"""
    return {
        "id": pid,
        "name": name or f"BEYBLADE X戰鬥陀螺/ 測試-{pid}",
        "price": price,
        "url": f"https://www.eslite.com/product/{pid}",
        "availability": "IN_STOCK" if buyable else "OUT_OF_STOCK",
        "button_status": "add_to_shopping_cart" if buyable else "not_add_to_notice",
        "buyable": buyable,
        "is_book": is_book,
        "manufacturer": "TOMY",
    }


class CheckLogicTest(unittest.TestCase):
    def setUp(self):
        # 暫存狀態檔,避免動到真的 state.json
        self.tmpdir = tempfile.mkdtemp()
        self._saved = {name: getattr(ew, name)
                       for name in ("STATE_FILE", "gather", "toast", "beep",
                                    "push_phone", "log")}
        ew.STATE_FILE = os.path.join(self.tmpdir, "state.json")
        # 攔截通知:把推播內容收集起來檢查
        self.pushes = []
        ew.push_phone = lambda text: self.pushes.append(text)
        ew.toast = lambda *a, **k: None
        ew.beep = lambda *a, **k: None
        ew.log = lambda *a, **k: None  # 測試時保持安靜

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(ew, name, val)
        try:
            if os.path.exists(os.path.join(self.tmpdir, "state.json")):
                os.remove(os.path.join(self.tmpdir, "state.json"))
            os.rmdir(self.tmpdir)
        except OSError:
            pass

    # ── 輔助 ──────────────────────────────────────────────
    def set_catalog(self, items):
        """設定誠品『目前』有哪些商品(已過濾後的結果)。"""
        merged = {it["id"]: it for it in items}
        ew.gather = lambda: (True, len(merged), merged)

    def build_baseline(self, items):
        """用一次 check() 建立基準,並清掉這次的通知記錄。"""
        self.set_catalog(items)
        ew.check()
        self.pushes.clear()

    def read_state(self):
        with open(ew.STATE_FILE, encoding="utf-8") as f:
            return json.load(f)

    # ── 基準建立 ──────────────────────────────────────────
    def test_first_run_builds_baseline_without_notifying(self):
        self.set_catalog([make_item("A"), make_item("B", buyable=True)])
        ew.check()
        state = self.read_state()
        self.assertEqual(state.get("schema"), ew.SCHEMA)
        self.assertEqual(len(state["items"]), 2)
        self.assertEqual(self.pushes, [], "第一次建立基準不應發通知")

    # ── 新上架 ────────────────────────────────────────────
    def test_new_arrival_is_detected_and_notified(self):
        self.build_baseline([make_item("A")])
        self.set_catalog([make_item("A"), make_item("NEW", name="新品天馬爆擊")])
        ew.check()
        self.assertEqual(len(self.pushes), 1)
        msg = self.pushes[0]
        self.assertIn("新上架", msg)
        self.assertIn("新品天馬爆擊", msg)
        # 新品要被記進基準
        self.assertIn("NEW", self.read_state()["items"])

    def test_new_arrival_shows_buyable_tag(self):
        self.build_baseline([make_item("A")])
        self.set_catalog([make_item("A"), make_item("NEW", buyable=True)])
        ew.check()
        self.assertIn("✅可購買", self.pushes[0])

    # ── 舊品補貨 ──────────────────────────────────────────
    def test_restock_not_buyable_to_buyable_notifies(self):
        # 基準:A 一開始不可買
        self.build_baseline([make_item("A", name="補貨測試蒼龍", buyable=False)])
        # 之後 A 變成可加購物車
        self.set_catalog([make_item("A", name="補貨測試蒼龍", buyable=True)])
        ew.check()
        self.assertEqual(len(self.pushes), 1)
        msg = self.pushes[0]
        self.assertIn("補貨", msg)
        self.assertIn("補貨測試蒼龍", msg)

    def test_already_buyable_does_not_notify_restock(self):
        # 基準裡 A 本來就可買 → 不該被當成補貨
        self.build_baseline([make_item("A", buyable=True)])
        self.set_catalog([make_item("A", buyable=True)])
        ew.check()
        self.assertEqual(self.pushes, [], "本來就可買的商品不應觸發補貨通知")

    def test_sold_out_then_restock_fires_only_on_restock(self):
        # A 一開始可買
        self.build_baseline([make_item("A", buyable=True)])
        # 賣完(可買→不可買):不通知,但狀態要更新
        self.set_catalog([make_item("A", buyable=False)])
        ew.check()
        self.assertEqual(self.pushes, [], "賣完不應通知")
        self.assertFalse(self.read_state()["items"]["A"]["buyable"])
        # 再次補貨(不可買→可買):這次要通知
        self.set_catalog([make_item("A", buyable=True)])
        ew.check()
        self.assertEqual(len(self.pushes), 1)
        self.assertIn("補貨", self.pushes[0])

    # ── 同時新上架 + 補貨 ─────────────────────────────────
    def test_new_and_restock_in_same_run(self):
        self.build_baseline([make_item("A", buyable=False)])
        self.set_catalog([
            make_item("A", buyable=True),                 # 補貨
            make_item("NEW", name="同時出現的新品"),        # 新上架
        ])
        ew.check()
        self.assertEqual(len(self.pushes), 1)
        msg = self.pushes[0]
        self.assertIn("新上架", msg)
        self.assertIn("補貨", msg)
        self.assertIn("同時出現的新品", msg)

    # ── 無變化 ────────────────────────────────────────────
    def test_no_change_does_not_notify(self):
        self.build_baseline([make_item("A"), make_item("B", buyable=True)])
        self.set_catalog([make_item("A"), make_item("B", buyable=True)])
        ew.check()
        self.assertEqual(self.pushes, [])

    # ── schema 遷移:舊狀態自動重建,不誤報 ───────────────
    def test_schema_mismatch_rebuilds_baseline_silently(self):
        # 寫一個「舊版」狀態(沒有 schema、且結構不同)
        old = {"items": {"A": {"id": "A", "name": "舊", "buyable": False}}}
        with open(ew.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False)
        # 即使現在有一堆「可買」商品,也不該因為舊狀態缺欄位而誤報補貨
        self.set_catalog([make_item("A", buyable=True), make_item("B", buyable=True)])
        ew.check()
        self.assertEqual(self.pushes, [], "schema 不符時應靜默重建基準,不發通知")
        self.assertEqual(self.read_state().get("schema"), ew.SCHEMA)


class FilterTest(unittest.TestCase):
    """測試過濾條件 keep()(只看玩具 / 名稱含關鍵字 / 名稱白名單)。"""

    def setUp(self):
        self._saved = {n: getattr(ew, n)
                       for n in ("ONLY_NON_BOOK", "NAME_MUST_INCLUDE", "NAME_INCLUDE_ANY")}
        # 預設關掉各過濾,讓每個測試只驗自己那一項
        ew.ONLY_NON_BOOK = False
        ew.NAME_MUST_INCLUDE = ""
        ew.NAME_INCLUDE_ANY = []

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(ew, n, v)

    def test_only_non_book_filters_out_books(self):
        ew.ONLY_NON_BOOK = True
        self.assertTrue(ew.keep(make_item("A", is_book="no")))
        self.assertFalse(ew.keep(make_item("B", is_book="yes")))

    def test_name_must_include(self):
        ew.NAME_MUST_INCLUDE = "CX-18"
        self.assertTrue(ew.keep(make_item("A", name="BEYBLADE X CX-18 腕龍鞭打")))
        self.assertFalse(ew.keep(make_item("B", name="BEYBLADE X BX-49 蒼龍突擊")))

    def test_name_include_any_filters_unrelated(self):
        # 這就是「森林家族 / 假面」誤報的修正:名稱沒有 陀螺/BEYBLADE 就濾掉
        ew.NAME_INCLUDE_ANY = ["陀螺", "BEYBLADE"]
        self.assertTrue(ew.keep(make_item("A", name="BEYBLADE X戰鬥陀螺/ CX-18/ 腕龍鞭打")))
        self.assertTrue(ew.keep(make_item("B", name="戰鬥紙陀螺 入門款")))
        self.assertFalse(ew.keep(make_item("C", name="EPOCH森林家族嬰兒仙子變裝/ 抽抽包")))
        self.assertFalse(ew.keep(make_item("D", name="假面: 人格面具")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
