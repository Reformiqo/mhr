"""MI1-I133 (Rohit 2026-09-09) — Job Work Received: a new Received Item
field (Link, like Container's own Item field), and two read-only totals —
Received Total Qty / Received Total Cone — showing the live Qty and total
Cone of that item currently sitting in the document's Default Target
Warehouse. Applies to both HTY and VFY; nothing else on the document
changes.

Real documents throughout: `get_item_warehouse_totals` reads the same
Serial and Batch Bundle live-balance source every other figure in this app
reads (never Batch.batch_qty), summing Batch.custom_cone over exactly the
batches that hold that balance.
"""

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from mhr import utilis

FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
CUSTOM_FIELD_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")
SCRIPT_NAME = "Stock Entry Container Info"
ITEM = "MI1I133-TEST-ITEM"
WAREHOUSE_A = "Finished Goods - MC"
WAREHOUSE_B = "Vadod - MC"


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

    def test_modified_is_recent(self):
        for f in self.fields.values():
            self.assertGreater(f["modified"], "2026-09-08 00:00:00")

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

    def test_received_item_and_target_warehouse_both_trigger_refresh(self):
        for trigger in ("custom_received_item(frm) {", "to_warehouse(frm) {"):
            self.assertIn(trigger, self.src, trigger)
            start = self.src.index(trigger)
            end = self.src.index("},", start)
            body = self.src[start:end]
            self.assertIn("mhr_refresh_received_totals(frm);", body, trigger)

    def test_refresh_helper_calls_the_endpoint_and_writes_both_fields(self):
        fn = self.src[self.src.index("function mhr_refresh_received_totals(frm) {"):self.src.index("function mhr_on_received_container_or_lot_leave(frm) {")]
        self.assertIn("method: 'mhr.utilis.get_received_item_totals'", fn)
        self.assertIn("item_code: item_code", fn)
        self.assertIn("warehouse: warehouse", fn)
        self.assertIn("frm.set_value('custom_received_total_qty', flt(totals.qty));", fn)
        self.assertIn("frm.set_value('custom_received_total_cone', cint(totals.cone));", fn)

    def test_blank_item_or_warehouse_zeroes_the_totals(self):
        fn = self.src[self.src.index("function mhr_refresh_received_totals(frm) {"):self.src.index("function mhr_on_received_container_or_lot_leave(frm) {")]
        self.assertIn("if (!item_code || !warehouse) {", fn)
        self.assertIn("frm.set_value('custom_received_total_qty', 0);", fn)
        self.assertIn("frm.set_value('custom_received_total_cone', 0);", fn)

    def test_refresh_runs_on_load_only_for_a_draft(self):
        """MI1-I106: never write to a submitted doc from refresh()."""
        refresh = self.src[self.src.index("refresh(frm) {"):self.src.index("before_save: function(frm) {")]
        self.assertIn("if (frm.doc.docstatus === 0) {", refresh)
        self.assertIn("mhr_refresh_received_totals(frm);", refresh)

    def test_fixture_matches_local_client_script(self):
        db_script = frappe.db.get_value("Client Script", SCRIPT_NAME, "script") or ""
        self.assertEqual(db_script, self.src)


class TestGetItemWarehouseTotals(FrappeTestCase):
    """Real documents: two Stock Entries land ITEM in two different
    warehouses at two different cones; totals must be per-warehouse and
    must sum Cone only over the batches that actually hold stock there."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Item", ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Products",
                "stock_uom": "Nos", "has_batch_no": 1, "create_new_batch": 0, "is_stock_item": 1,
            }).insert(ignore_permissions=True)
        cls.batch_a1 = cls._make_batch("MI1I133-A1", cone=6)
        cls.batch_a2 = cls._make_batch("MI1I133-A2", cone=9)
        cls.batch_b1 = cls._make_batch("MI1I133-B1", cone=12)
        cls._make_stock_entry([(cls.batch_a1, 20), (cls.batch_a2, 15)], WAREHOUSE_A)
        cls._make_stock_entry([(cls.batch_b1, 7)], WAREHOUSE_B)

    @classmethod
    def _make_batch(cls, name, cone):
        if frappe.db.exists("Batch", name):
            return name
        b = frappe.new_doc("Batch")
        b.update({"batch_id": name, "item": ITEM, "custom_cone": cone, "custom_transaction_type": "VFY"})
        b.insert(ignore_permissions=True)
        return b.name

    @classmethod
    def _make_stock_entry(cls, batch_qtys, target_warehouse):
        se = frappe.new_doc("Stock Entry")
        se.update({"stock_entry_type": "Material Receipt", "purpose": "Material Receipt", "company": "Meher Creations",
                   "posting_date": frappe.utils.nowdate(), "set_posting_time": 1, "to_warehouse": target_warehouse})
        for batch_no, qty in batch_qtys:
            se.append("items", {"item_code": ITEM, "qty": qty, "t_warehouse": target_warehouse,
                                "batch_no": batch_no, "use_serial_batch_fields": 1, "basic_rate": 10})
        se.insert(ignore_permissions=True)
        se.submit()
        return se

    def test_totals_are_per_warehouse(self):
        a = utilis.get_item_warehouse_totals(ITEM, WAREHOUSE_A)
        b = utilis.get_item_warehouse_totals(ITEM, WAREHOUSE_B)
        self.assertAlmostEqual(a["qty"], 35, places=3)
        self.assertEqual(a["cone"], 15, "sum of the two batches' own cone (6 + 9), not their qty-weighted average")
        self.assertAlmostEqual(b["qty"], 7, places=3)
        self.assertEqual(b["cone"], 12)

    def test_a_warehouse_the_item_never_reached_is_zero(self):
        r = utilis.get_item_warehouse_totals(ITEM, "SURAMYA YARN - MC")
        self.assertEqual(r, {"qty": 0.0, "cone": 0})

    def test_blank_arguments_are_zero(self):
        self.assertEqual(utilis.get_item_warehouse_totals("", WAREHOUSE_A), {"qty": 0.0, "cone": 0})
        self.assertEqual(utilis.get_item_warehouse_totals(ITEM, ""), {"qty": 0.0, "cone": 0})
        self.assertEqual(utilis.get_item_warehouse_totals(None, None), {"qty": 0.0, "cone": 0})

    def test_unknown_item_is_zero(self):
        self.assertEqual(utilis.get_item_warehouse_totals("MI1I133-NO-SUCH-ITEM", WAREHOUSE_A), {"qty": 0.0, "cone": 0})

    def test_whitelisted_wrapper_delegates_and_checks_permission(self):
        self.assertEqual(utilis.get_received_item_totals(ITEM, WAREHOUSE_A), utilis.get_item_warehouse_totals(ITEM, WAREHOUSE_A))
        import inspect
        self.assertIn('frappe.has_permission("Stock Entry", "read", throw=True)', inspect.getsource(utilis.get_received_item_totals))

    def test_delivering_it_out_again_drops_it_from_the_total(self):
        """A batch fully consumed from the warehouse must stop counting —
        this is a live balance, not a running total of what was ever
        received."""
        before = utilis.get_item_warehouse_totals(ITEM, WAREHOUSE_A)
        dn = frappe.new_doc("Stock Entry")
        dn.update({"stock_entry_type": "Material Issue", "purpose": "Material Issue", "company": "Meher Creations",
                   "posting_date": frappe.utils.nowdate(), "set_posting_time": 1, "from_warehouse": WAREHOUSE_A})
        dn.append("items", {"item_code": ITEM, "qty": 20, "s_warehouse": WAREHOUSE_A, "batch_no": self.batch_a1,
                            "use_serial_batch_fields": 1})
        dn.insert(ignore_permissions=True)
        dn.submit()
        try:
            after = utilis.get_item_warehouse_totals(ITEM, WAREHOUSE_A)
            self.assertAlmostEqual(after["qty"], flt(before["qty"]) - 20, places=3)
            self.assertEqual(after["cone"], 9, "batch_a1 (cone 6) fully issued out; only batch_a2 (cone 9) remains")
        finally:
            dn.cancel()
