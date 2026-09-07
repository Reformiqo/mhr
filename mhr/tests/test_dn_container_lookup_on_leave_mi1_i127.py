"""MI1-I127 (Rohit 2026-09-07) — HTY Delivery Note: no container lookup, and no
"No available stock" popup, while the Container No is still being typed.

Two defects behind the screenshot ("Container MCF has 506489 HTY batch(es)"):
  1. Container No is a Data field; frappe's Data control runs the field
     handler 500 ms after every pause in typing, so the lookup ran on "MCF".
  2. The MI1-I114 explainer called frappe.db.count('Batch', <filters>) — but
     frappe.db.count(doctype, args) reads args.filters, so it counted every
     Batch on the site and spoke up for any text that matched no container.
Both Delivery Note container handlers ("HTY & VFY" popup, "MI1-I101" notes)
now wait for the field to be left; the count is scoped to the container.
"""

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")


def _script(name):
    with open(FIXTURE) as f:
        for cs in json.load(f):
            if cs["name"] == name:
                return cs


class TestHtyVfyScript(FrappeTestCase):

    def setUp(self):
        self.cs = _script("HTY & VFY")
        self.src = self.cs["script"].replace("\r\n", "\n")

    def test_container_handler_waits_for_the_field_to_be_left(self):
        start = self.src.index("    async custom_container_no(frm) {")
        handler = self.src[start:self.src.index("    async custom_denier(frm) {", start)]
        self.assertIn("if (hty_still_typing(frm, 'custom_container_no')) return;", handler)
        # Order: return guard, empty-clears, then the typing guard, then the lookups.
        self.assertLess(handler.index("frm.doc.is_return"), handler.index("hty_still_typing"))
        self.assertLess(handler.index("clear_batch_fields(frm);"), handler.index("hty_still_typing"))
        self.assertLess(handler.index("hty_still_typing"), handler.index("get_all_batches_vfy("))
        self.assertLess(handler.index("hty_still_typing"), handler.index("get_all_batches(frm.doc.custom_container_no)"))

    def test_guard_semantics(self):
        fn = self.src[self.src.index("function hty_still_typing(frm, fieldname) {"):self.src.index("async function get_all_batches_by_item(item) {")]
        for needle in ("$input.is(':focus')", "return false;", "$input.one('blur'", "frm.trigger(fieldname);",
                       "if (e.key === 'Enter') $input.blur();", "removeData('mhr_lookup_on_leave')"):
            self.assertIn(needle, fn)

    def test_explainer_counts_this_container_only(self):
        fn = self.src[self.src.index("async function hty_explain_no_available_stock(container_no) {"):self.src.index("// ── Shared")]
        self.assertIn("frappe.db.count('Batch', {\n        filters: {\n            custom_container_no: container_no,", fn)
        self.assertIn("if (!known) return;", fn)
        # Every frappe.db.count CALL in the script passes filters under `filters`.
        calls = [i for i in range(len(self.src)) if self.src.startswith("frappe.db.count('", i)]
        self.assertTrue(calls)
        for i in calls:
            self.assertIn("filters:", self.src[i:i + 80], "frappe.db.count(doctype, args) reads args.filters")

    def test_line_endings_and_modified(self):
        self.assertIn("\r\n", self.cs["script"], "this script is stored with CRLF; keep it")
        self.assertGreater(self.cs["modified"], "2026-09-04 18:39:38.529335")
        self.assertEqual(self.cs["enabled"], 1)

    def test_server_count_semantics_match(self):
        """What the corrected client call asks the server: the same count
        frappe.db.count gives on the server with these filters — zero for text
        that is not a container."""
        self.assertEqual(frappe.db.count("Batch", {"custom_container_no": "MCF", "custom_transaction_type": "HTY"}), 0)
        self.assertGreater(frappe.db.count("Batch"), 0)

    def test_local_client_script_matches_fixture(self):
        if not frappe.db.exists("Client Script", "HTY & VFY"):
            self.skipTest("Client Script not installed on this bench.")
        self.assertIn("hty_still_typing", frappe.db.get_value("Client Script", "HTY & VFY", "script"))


class TestNotesScript(FrappeTestCase):

    def test_notes_fetch_waits_too(self):
        cs = _script("MI1-I101 — Delivery Note Container Notes")
        src = cs["script"]
        self.assertIn("if (mi1_i101_still_typing(frm, 'custom_container_no')) return;", src)
        self.assertLess(src.index("mi1_i101_still_typing(frm"), src.index("method: 'mhr.note.get_container_notes'"))
        fn = src[src.index("function mi1_i101_still_typing(frm, fieldname) {"):]
        for needle in ("$input.is(':focus')", "$input.one('blur'", "frm.trigger(fieldname);", "if (e.key === 'Enter') $input.blur();"):
            self.assertIn(needle, fn)
        self.assertGreater(cs["modified"], "2026-08-17 11:20:00.000000")
        self.assertNotIn("\r\n", src)


class TestSharedGuardKeys(FrappeTestCase):
    """frm.trigger(fieldname) runs every script's handler, so every script
    guarding the same field must share one blur listener — separate keys
    opened the Select Batch dialog twice on MCFW-02 (walkthrough 2026-09-07)."""

    def test_every_guard_uses_the_shared_keys(self):
        with open(FIXTURE) as f:
            scripts = {cs["name"]: cs["script"] for cs in json.load(f)}
        hty_js = os.path.join(frappe.get_app_path("mhr"), "public", "js", "sales_order_hty.js")
        with open(hty_js) as f:
            scripts["sales_order_hty.js"] = f.read()
        guarded = {n: s for n, s in scripts.items() if "_still_typing(frm, fieldname)" in s}
        self.assertEqual(set(guarded), {"HTY & VFY", "MI1-I101 — Delivery Note Container Notes",
                                        "Sales Order Booking", "sales_order_hty.js"})
        for name, src in guarded.items():
            self.assertIn("$input.data('mhr_lookup_on_leave')", src, name)
            self.assertIn("$input.data('mhr_enter_blurs')", src, name)
            self.assertNotRegex(src, r"data\('(?!mhr_)[a-z0-9_]*(lookup_on_leave|enter_blurs)'\)", name)
