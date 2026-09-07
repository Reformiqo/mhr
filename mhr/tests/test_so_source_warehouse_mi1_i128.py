"""MI1-I128 (Rohit 2026-09-07) — Sales Order Set Source Warehouse must be the
Container's inward warehouse (or where its stock now is).

Real documents: a VFY Sales Order booking a stocked, unbooked batch of a
container whose Container master names Finished Goods - MC as the inward
warehouse. Submitting with Vadod - MC is refused, with a blank warehouse is
refused, with Finished Goods - MC goes through. A container moved by a
Material Transfer accepts the warehouse holding its stock (MI1-I125).
"""

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, today

from mhr import sales_order as so_mod
from mhr import utilis

ITEM = "58D/8F"
COMPANY = "Meher Creations"
FG = "Finished Goods - MC"
OTHER = "Vadod - MC"
CUSTOMER = "Shree Ram Sevak Silk Mills"
FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
HTY_JS = os.path.join(frappe.get_app_path("mhr"), "public", "js", "sales_order_hty.js")


def _pick_batch():
    """A stocked, unbooked VFY batch whose Container master says FG."""
    rows = frappe.db.sql("""
        SELECT b.name, b.custom_container_no, b.custom_lot_no, b.batch_qty FROM `tabBatch` b
        JOIN `tabContainer` c ON c.container_no = b.custom_container_no AND c.lot_no = b.custom_lot_no AND c.docstatus = 1
        JOIN (SELECT sbe.batch_no, SUM(sbe.qty) bal FROM `tabSerial and Batch Entry` sbe
              JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
              WHERE sbb.warehouse = %s AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
              GROUP BY sbe.batch_no HAVING bal >= 5) s ON s.batch_no = b.name
        WHERE b.item = %s AND b.disabled = 0 AND IFNULL(b.custom_cone, 0) > 0 AND b.batch_qty >= 5
          AND c.set_warehouse = %s AND b.custom_transaction_type = 'VFY'
        ORDER BY b.name LIMIT 40""", (FG, ITEM, FG), as_dict=True)
    booked = utilis.effective_booking_by_batch([r.name for r in rows])
    for r in rows:
        if flt(booked.get(r.name, {}).get("qty", 0)) == 0:
            return r
    return None


def _make_so(batch, warehouse, row_warehouse=None, qty=5):
    so = frappe.new_doc("Sales Order")
    so.update({"customer": CUSTOMER, "company": COMPANY, "transaction_type": "VFY",
               "transaction_date": today(), "delivery_date": add_days(today(), 7),
               "set_warehouse": warehouse, "selling_price_list": "Standard Selling", "currency": "INR",
               "custom_container_no": batch.custom_container_no, "custom_lot_no": batch.custom_lot_no})
    so.append("items", {"item_code": ITEM, "qty": qty, "rate": 100, "delivery_date": add_days(today(), 7),
                        "warehouse": row_warehouse if row_warehouse is not None else warehouse,
                        "custom_batch_no": batch.name})
    so.insert(ignore_permissions=True)
    return so


_PICKED = {}


def _picked_batch():
    """The stock-wide scan is slow; pick once per test run."""
    if "batch" not in _PICKED:
        _PICKED["batch"] = _pick_batch()
    return _PICKED["batch"]


class TestSourceWarehouseOnSubmit(FrappeTestCase):

    def setUp(self):
        if not frappe.db.exists("Customer", CUSTOMER):
            self.skipTest("Customer missing.")
        self.batch = _picked_batch()
        if not self.batch:
            self.skipTest("No free stocked VFY batch of a Container inwarded to Finished Goods - MC.")

    def test_hook_registered_on_before_submit(self):
        hooks = frappe.get_hooks("doc_events")["Sales Order"]
        self.assertIn("mhr.sales_order.validate_so_source_warehouse", hooks.get("before_submit", []))

    def test_other_warehouse_is_refused(self):
        so = _make_so(self.batch, OTHER)
        with self.assertRaises(frappe.ValidationError) as ctx:
            so.submit()
        msg = str(ctx.exception)
        self.assertIn(OTHER, msg)
        self.assertIn(self.batch.custom_container_no, msg)
        self.assertIn(FG, msg, "the message names the inward warehouse")
        self.assertEqual(frappe.db.get_value("Sales Order", so.name, "docstatus"), 0)

    def test_blank_warehouse_is_refused(self):
        so = _make_so(self.batch, None, row_warehouse=FG)
        so.set_warehouse = None
        so.db_set("set_warehouse", None)
        so.reload()
        with self.assertRaises(frappe.ValidationError) as ctx:
            so.submit()
        self.assertIn("mandatory", str(ctx.exception))

    def test_row_warehouse_is_checked_too(self):
        so = _make_so(self.batch, FG, row_warehouse=OTHER)
        with self.assertRaises(frappe.ValidationError) as ctx:
            so.submit()
        self.assertIn("Row 1", str(ctx.exception))

    def test_inward_warehouse_submits(self):
        so = _make_so(self.batch, FG)
        so.submit()
        self.assertEqual(so.docstatus, 1)

    def test_order_without_container_untouched(self):
        so = _make_so(self.batch, OTHER)
        so.custom_container_no = None
        so.custom_lot_no = None
        so.save()
        so.submit()
        self.assertEqual(so.docstatus, 1)


class TestResolution(FrappeTestCase):

    def test_endpoint_prefers_the_inward_warehouse(self):
        b = _picked_batch()
        if not b:
            self.skipTest("No suitable batch.")
        r = so_mod.get_container_source_warehouse(b.custom_container_no, b.custom_lot_no)
        self.assertEqual(r["inward"], [FG])
        self.assertEqual(r["suggested"], FG)
        self.assertIn(FG, r["live"])

    def test_moved_stock_is_accepted(self):
        """MI1-I125 reality: after a Material Transfer the stock is elsewhere;
        the holding warehouse passes, the inward one still names itself."""
        rows = frappe.db.sql("""
            SELECT b.name, b.custom_container_no, b.custom_lot_no FROM `tabBatch` b
            JOIN (SELECT sbe.batch_no, SUM(sbe.qty) bal FROM `tabSerial and Batch Entry` sbe
                  JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
                  WHERE sbb.warehouse = %s AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
                  GROUP BY sbe.batch_no HAVING bal >= 5) s ON s.batch_no = b.name
            WHERE b.item = %s AND b.disabled = 0 AND IFNULL(b.custom_cone, 0) > 0 AND b.batch_qty >= 5
            ORDER BY b.name LIMIT 1""", (OTHER, ITEM), as_dict=True)
        if not rows:
            self.skipTest(f"No stocked batch of {ITEM} in {OTHER}.")
        b = rows[0]
        live = so_mod._container_live_warehouses(b.custom_container_no, b.custom_lot_no)
        self.assertIn(OTHER, live)
        doc = frappe._dict(custom_container_no=b.custom_container_no, custom_lot_no=b.custom_lot_no,
                           set_warehouse=OTHER, items=[frappe._dict(idx=1, warehouse=OTHER)])
        so_mod.validate_so_source_warehouse(doc)  # no raise
        doc.set_warehouse = "Stores - MC"
        with self.assertRaises(frappe.ValidationError):
            so_mod.validate_so_source_warehouse(doc)

    def test_unknown_container_skips_the_comparison(self):
        doc = frappe._dict(custom_container_no="MI1I128-NO-SUCH", custom_lot_no="L1", set_warehouse=FG,
                           items=[frappe._dict(idx=1, warehouse=FG)])
        so_mod.validate_so_source_warehouse(doc)
        doc.set_warehouse = None
        with self.assertRaises(frappe.ValidationError):
            so_mod.validate_so_source_warehouse(doc)
        self.assertEqual(so_mod.get_container_source_warehouse("MI1I128-NO-SUCH"), {"inward": [], "live": [], "suggested": None})

    def test_no_container_no_op(self):
        so_mod.validate_so_source_warehouse(frappe._dict(custom_container_no=None, set_warehouse=None, items=[]))


class TestPickersFillTheWarehouse(FrappeTestCase):

    def test_vfy_script(self):
        with open(FIXTURE) as f:
            cs = [c for c in json.load(f) if c["name"] == "Sales Order Booking"][0]
        self.assertIn("mi1_so_fill_source_warehouse(frm, row.lot_no);", cs["script"])
        self.assertIn("method: 'mhr.sales_order.get_container_source_warehouse'", cs["script"])
        self.assertIn("if (frm.doc.set_warehouse || !frm.doc.custom_container_no) return;", cs["script"])
        self.assertGreater(cs["modified"], "2026-09-07 09:00:00.000000")

    def test_hty_script(self):
        with open(HTY_JS) as f:
            src = f.read()
        self.assertIn("so_hty_fill_source_warehouse(frm, row.lot_no);", src)
        self.assertIn("method: 'mhr.sales_order.get_container_source_warehouse'", src)
