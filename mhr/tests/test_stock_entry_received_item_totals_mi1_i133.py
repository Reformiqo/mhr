"""MI1-I133 (Rohit 2026-09-09, corrected by Raj's follow-up comment
2026-09-10) — Job Work Received: a Received Item field (Link, like
Container's own Item field), and two read-only totals — Received Total Qty /
Received Total Cone. Applies to both HTY and VFY; nothing else on the
document changes.

Round 1 (shipped 2026-09-09) read the LIVE Serial and Batch Bundle balance
of the separately-picked Received Item in the header's Default Target
Warehouse. Raj's follow-up (screenshot: MAT-GD-2026-00016, both totals
reading 0 despite Target Warehouse rows plainly carrying Qty/Cone) corrected
this: a still-draft receipt has not posted anything to the ledger yet, so a
first-time item's live balance is 0 regardless of what the draft's own rows
say. The real, intended rule — Received Total Qty = SUM(qty), Received
Total Cone = SUM(custom_cone), both over this document's OWN Item rows that
carry a Target Warehouse; a row's own Source Warehouse, if also set, does
not disqualify it, only a pure source-only row (Target Warehouse blank)
is excluded. custom_received_item stays as its own field but no longer
feeds this calculation at all.
"""

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis

FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
CUSTOM_FIELD_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")
SCRIPT_NAME = "Stock Entry Container Info"
ITEM = "MI1I133-TEST-ITEM"
WAREHOUSE_A = "Finished Goods - MC"
WAREHOUSE_B = "Vadod - MC"
COMPANY = "Meher Creations"


def _script():
    with open(FIXTURE) as f:
        for cs in json.load(f):
            if cs["name"] == SCRIPT_NAME:
                return cs


def _custom_fields():
    with open(CUSTOM_FIELD_FIXTURE) as f:
        return {f["fieldname"]: f for f in json.load(f) if f["dt"] == "Stock Entry"
                and f["fieldname"] in ("custom_received_item", "custom_received_total_qty", "custom_received_total_cone")}


class TestFixtureDefinesTheThreeFields(FrappeTestCase):

    def setUp(self):
        self.fields = _custom_fields()

    def test_received_item_is_a_plain_item_link(self):
        f = self.fields["custom_received_item"]
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Item")
        self.assertEqual(f["label"], "Received Item")
        self.assertFalse(f["read_only"], "user must be able to pick it, like Container's Item field")

    def test_totals_are_read_only_and_correctly_typed(self):
        qty = self.fields["custom_received_total_qty"]
        cone = self.fields["custom_received_total_cone"]
        self.assertEqual(qty["fieldtype"], "Float")
        self.assertTrue(qty["read_only"])
        self.assertEqual(cone["fieldtype"], "Int")
        self.assertTrue(cone["read_only"])

    def test_descriptions_no_longer_claim_a_live_warehouse_balance(self):
        for f in self.fields.values():
            desc = f.get("description") or ""
            self.assertNotIn("currently sitting", desc)

    def test_local_meta_matches(self):
        m = frappe.get_meta("Stock Entry")
        item_field = m.get_field("custom_received_item")
        qty_field = m.get_field("custom_received_total_qty")
        cone_field = m.get_field("custom_received_total_cone")
        self.assertEqual(item_field.fieldtype, "Link")
        self.assertEqual(item_field.options, "Item")
        self.assertEqual(qty_field.fieldtype, "Float")
        self.assertTrue(qty_field.read_only)
        self.assertEqual(cone_field.fieldtype, "Int")
        self.assertTrue(cone_field.read_only)


class TestClientScriptWiring(FrappeTestCase):

    def setUp(self):
        self.cs = _script()
        self.src = self.cs["script"]
        self.assertNotIn("\r\n", self.src)

    def _refresh_fn(self):
        start = self.src.index("function mhr_refresh_received_totals(frm) {")
        end = self.src.index("function mhr_on_received_container_or_lot_leave(frm) {")
        return self.src[start:end]

    def test_refresh_helper_no_longer_calls_the_server(self):
        fn = self._refresh_fn()
        self.assertNotIn("get_received_item_totals", fn)
        self.assertNotIn("frappe.call", fn)

    def test_refresh_helper_sums_target_warehouse_rows_locally(self):
        fn = self._refresh_fn()
        self.assertIn("if (!row.t_warehouse) return;", fn)
        self.assertIn("qty += flt(row.qty);", fn)
        self.assertIn("cone += cint(row.custom_cone);", fn)
        self.assertIn("frm.set_value('custom_received_total_qty', qty);", fn)
        self.assertIn("frm.set_value('custom_received_total_cone', cone);", fn)

    def test_received_item_and_target_warehouse_still_trigger_a_refresh(self):
        for trigger in ("custom_received_item(frm) {", "to_warehouse(frm) {"):
            self.assertIn(trigger, self.src, trigger)
            start = self.src.index(trigger)
            end = self.src.index("},", start)
            body = self.src[start:end]
            self.assertIn("mhr_refresh_received_totals(frm);", body, trigger)

    def test_refresh_runs_on_load_only_for_a_draft(self):
        """MI1-I106: never write to a submitted doc from refresh()."""
        refresh = self.src[self.src.index("refresh(frm) {"):self.src.index("before_save: function(frm) {")]
        self.assertIn("if (frm.doc.docstatus === 0) {", refresh)
        self.assertIn("mhr_refresh_received_totals(frm);", refresh)

    def _stock_entry_detail_block(self):
        start = self.src.index("frappe.ui.form.on('Stock Entry Detail', {")
        end = self.src.index("});", start)
        return self.src[start:end]

    def test_row_level_triggers_recompute_on_every_relevant_change(self):
        """Add / remove a row, or change its own Qty / Cone / Target
        Warehouse -- each must recompute (Raj: "Recalculate automatically
        whenever Item rows are added, removed, or Qty/Cone/Target Warehouse
        is changed")."""
        block = self._stock_entry_detail_block()
        for trigger in ("items_add:", "items_remove:", "qty:", "custom_cone:", "t_warehouse:"):
            idx = block.index(trigger)
            # the handler body ends at the next top-level trigger or the block's end
            self.assertIn(
                "mhr_refresh_received_totals(frm)",
                block[idx: idx + 400],
                f"{trigger} does not call mhr_refresh_received_totals",
            )

    def test_fixture_matches_local_client_script(self):
        db_script = frappe.db.get_value("Client Script", SCRIPT_NAME, "script") or ""
        self.assertEqual(db_script, self.src)


class TestCalculateReceivedTotals(FrappeTestCase):
    """mhr.utilis.calculate_received_totals — the server-side, authoritative
    recompute (Stock Entry.validate), unit-tested against frappe._dict fake
    docs the way the rest of this module's hooks already are."""

    def _doc(self, items):
        return frappe._dict({"items": [frappe._dict(r) for r in items]})

    def test_sums_only_rows_with_a_target_warehouse(self):
        doc = self._doc([
            {"qty": 10, "custom_cone": 5, "t_warehouse": WAREHOUSE_A, "s_warehouse": None},
            {"qty": 20, "custom_cone": 8, "t_warehouse": None, "s_warehouse": WAREHOUSE_B},
            {"qty": 15, "custom_cone": 3, "t_warehouse": WAREHOUSE_A, "s_warehouse": WAREHOUSE_B},
        ])
        utilis.calculate_received_totals(doc)
        self.assertEqual(doc.custom_received_total_qty, 25)
        self.assertEqual(doc.custom_received_total_cone, 8)

    def test_a_row_with_both_warehouses_still_counts(self):
        """A plain single-pair Material Transfer row (Source AND Target both
        set) is not a 'source warehouse row' — only Target Warehouse blank
        excludes a row."""
        doc = self._doc([{"qty": 3, "custom_cone": 1, "t_warehouse": WAREHOUSE_A, "s_warehouse": WAREHOUSE_B}])
        utilis.calculate_received_totals(doc)
        self.assertEqual(doc.custom_received_total_qty, 3)
        self.assertEqual(doc.custom_received_total_cone, 1)

    def test_no_target_warehouse_rows_at_all_gives_zero(self):
        doc = self._doc([{"qty": 10, "custom_cone": 5, "t_warehouse": None, "s_warehouse": WAREHOUSE_A}])
        utilis.calculate_received_totals(doc)
        self.assertEqual(doc.custom_received_total_qty, 0)
        self.assertEqual(doc.custom_received_total_cone, 0)

    def test_no_items_at_all_gives_zero(self):
        doc = self._doc([])
        utilis.calculate_received_totals(doc)
        self.assertEqual(doc.custom_received_total_qty, 0)
        self.assertEqual(doc.custom_received_total_cone, 0)

    def test_blank_custom_cone_counts_as_zero_not_an_error(self):
        doc = self._doc([{"qty": 10, "custom_cone": None, "t_warehouse": WAREHOUSE_A}])
        utilis.calculate_received_totals(doc)
        self.assertEqual(doc.custom_received_total_qty, 10)
        self.assertEqual(doc.custom_received_total_cone, 0)

    def test_dead_functions_are_actually_gone(self):
        self.assertFalse(hasattr(utilis, "get_item_warehouse_totals"))
        self.assertFalse(hasattr(utilis, "get_received_item_totals"))


class TestHookRegistration(FrappeTestCase):

    def test_registered_on_stock_entry_validate(self):
        import mhr.hooks as hooks
        v = hooks.doc_events["Stock Entry"]["validate"]
        self.assertIn("mhr.utilis.calculate_received_totals", v)


class TestRealDocumentEndToEnd(FrappeTestCase):
    """A real Stock Entry with a target-only finished row (must contribute)
    and a source-only consume row (must not) — the exact row shapes
    MAT-GD-2026-00016 (Raj's follow-up screenshot) mixes. A plain,
    non-batch-tracked item: `calculate_received_totals` reads only
    qty/custom_cone/t_warehouse off each row, never a batch, so batch-level
    stock bookkeeping is unrelated complexity this test does not need."""

    PLAIN_ITEM = "MI1I133-E2E-ITEM"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Item", cls.PLAIN_ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": cls.PLAIN_ITEM, "item_name": cls.PLAIN_ITEM,
                "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1,
            }).insert(ignore_permissions=True)

    def test_target_only_row_counts_source_only_row_does_not(self):
        # Seed stock in WAREHOUSE_B for the source-only consume row below.
        receipt = frappe.new_doc("Stock Entry")
        receipt.update({"stock_entry_type": "Material Receipt", "purpose": "Material Receipt", "company": COMPANY,
                        "posting_date": frappe.utils.nowdate(), "set_posting_time": 1, "to_warehouse": WAREHOUSE_B})
        receipt.append("items", {"item_code": self.PLAIN_ITEM, "qty": 50, "t_warehouse": WAREHOUSE_B, "basic_rate": 10})
        receipt.insert(ignore_permissions=True)
        receipt.submit()
        try:
            se = frappe.new_doc("Stock Entry")
            # Repack: the one purpose that can mix a source-only consume row
            # with a target-only finished row (CLAUDE.md, MI1-I50 1b) — a
            # plain Material Transfer requires a Target Warehouse on every
            # row, so it cannot even hold the source-only row this test
            # needs. calculate_received_totals has no stock_entry_type gate
            # (it runs on every Stock Entry), so this test only needs a
            # document that can actually hold both row shapes — the "Repack"
            # Stock Entry Type, not "Job Work Received", whose own stored
            # purpose ("Material Transfer") would win via `purpose`'s
            # fetch_from the moment stock_entry_type resolves.
            se.update({"stock_entry_type": "Repack", "purpose": "Repack", "company": COMPANY,
                       "posting_date": frappe.utils.nowdate(), "set_posting_time": 1})
            # Target-only finished row: must contribute.
            se.append("items", {"item_code": self.PLAIN_ITEM, "qty": 3, "custom_cone": 1,
                                "t_warehouse": WAREHOUSE_B, "is_finished_item": 1, "basic_rate": 10})
            # Source-only consume row: must not contribute.
            se.append("items", {"item_code": self.PLAIN_ITEM, "qty": 20, "custom_cone": 4,
                                "s_warehouse": WAREHOUSE_B})
            se.insert(ignore_permissions=True)
            self.assertEqual(se.custom_received_total_qty, 3)
            self.assertEqual(se.custom_received_total_cone, 1)
            se.submit()
            se.reload()
            self.assertEqual(se.custom_received_total_qty, 3)
            self.assertEqual(se.custom_received_total_cone, 1)
            se.cancel()
        finally:
            receipt.cancel()
