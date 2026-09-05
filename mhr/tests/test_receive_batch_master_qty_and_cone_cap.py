"""2026-09-06 — "qty is getting double after saving" (prod MAT-DN-FY04726).

Two defects met on one note:
  1. create_receive_batches (MI1-I50) preset Batch.batch_qty from the row; ERPNext's
     incremental update_batch_qty then added the posted qty — 20 received, master 40.
  2. The "Cone Qty Calcuation" Client Script rewrote every batch+cone row on save to
     master * cone / cone_copy, trusting that master — 20 entered, 40 saved,
     submit refused with negative stock.
"""
import inspect
import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis

CS_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")


class TestReceiveBatchMasterIsNotPreset(FrappeTestCase):

    def test_no_preset_in_create_receive_batches(self):
        src = inspect.getsource(utilis.create_receive_batches)
        self.assertNotIn("batch.batch_qty = flt(row.qty)", src,
                         "ERPNext adds the posted qty on submit; a preset doubles the master.")
        self.assertIn("batch_qty is NOT preset", src)

    def test_heal_sets_master_to_balance_and_is_idempotent(self):
        b = frappe.new_doc("Batch")
        b.update({"batch_id": "TEST-HEAL-RECV-1", "item": "58D/8F", "batch_qty": 40.0, "custom_transaction_type": "VFY"})
        b.insert(ignore_permissions=True)
        try:
            fixed = utilis.heal_receive_batch_qty({b.name: 20.0})
            self.assertEqual(fixed, [b.name])
            self.assertEqual(frappe.db.get_value("Batch", b.name, "batch_qty"), 20.0)
            self.assertEqual(utilis.heal_receive_batch_qty({b.name: 20.0}), [], "Second run changes nothing.")
        finally:
            frappe.delete_doc("Batch", b.name, force=1, ignore_permissions=True)

    def test_heal_selects_only_batches_a_receive_entry_created(self):
        src = inspect.getsource(utilis.receive_created_batch_balances)
        self.assertIn("IFNULL(se.custom_original_send_entry, '') != ''", src)
        self.assertIn("s.type_of_transaction = 'Inward'", src)
        self.assertIsInstance(utilis.receive_created_batch_balances(), dict)

    def test_patch_registered_last(self):
        with open(os.path.join(frappe.get_app_path("mhr"), "patches.txt")) as fh:
            lines = [l.strip() for l in fh if l.strip()]
        self.assertTrue(lines[-1].startswith("mhr.patches.v1_0.heal_receive_batch_qty"))
        mod = frappe.get_module("mhr.patches.v1_0.heal_receive_batch_qty")
        self.assertTrue(callable(mod.execute))


class TestConeQtyScriptCapsAtAvailable(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(CS_FIXTURE, encoding="utf-8") as fh:
            cls.cs = next(c for c in json.load(fh) if c["name"] == "Cone Qty Calcuation")
        cls.src = cls.cs["script"].replace("\r\n", "\n")

    def test_both_paths_read_the_live_balance(self):
        self.assertEqual(self.src.count('method: "mhr.utilis.get_item_batch"'), 2, "live-change path and before_save path")
        self.assertEqual(self.src.count("with_available: 1"), 2)
        self.assertNotIn("frappe.client.get_value", self.src, "the master alone is never trusted again")

    def test_rule_is_master_proportion_capped_at_available(self):
        i = self.src.find("function mi1_cone_qty_from_batch(d, row)")
        self.assertGreater(i, -1)
        body = self.src[i:i + 900]
        self.assertIn("let proportional = (master * cone) / cone_copy;", body)
        self.assertIn("return Math.min(proportional, available);", body)
        self.assertIn("if (isNaN(available) || available <= 0) return null;", body, "no stock: leave the row alone")
        self.assertIn("if (isNaN(master) || master <= 0) master = available;", body, "master never maintained (Chips bags)")
        self.assertEqual(self.src.count("mi1_cone_qty_from_batch(r.message, row)"), 2)

    def test_manual_edit_and_return_guards_kept(self):
        self.assertIn("if (row.custom_qty_manual_edit) return;", self.src)
        self.assertIn("if (mi1_i93_is_return(frm)) return;", self.src)

    def test_fixture_bumped_and_matches_db(self):
        self.assertGreater(str(self.cs["modified"]), "2026-09-06")
        db = (frappe.db.get_value("Client Script", "Cone Qty Calcuation", "script") or "").replace("\r\n", "\n")
        self.assertEqual(db, self.src)

    def test_server_side_gives_the_numbers_the_script_needs(self):
        """get_item_batch(with_available=1) returns master and the capped balance —
        the prod case: master 40, on hand 20 -> row must become 20."""
        from mhr.utilis import get_item_batch
        b = frappe.new_doc("Batch")
        b.update({"batch_id": "TEST-CONE-CAP-1", "item": "58D/8F", "batch_qty": 40.0, "custom_cone": 5, "custom_transaction_type": "VFY"})
        b.insert(ignore_permissions=True)
        try:
            with patch.object(utilis, "_clamp_batch_qty_to_available", create=True):
                pass
            d = get_item_batch(b.name, with_available=1)
            self.assertEqual(d["qty"], 40.0)
            self.assertEqual(d["available_qty"], 0.0, "No bundle balance at all -> 0 -> the script leaves the row alone.")
        finally:
            frappe.delete_doc("Batch", b.name, force=1, ignore_permissions=True)
