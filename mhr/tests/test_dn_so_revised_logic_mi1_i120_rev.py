"""MI1-I120 revision (Raj 2026-09-05) — VFY Sales Order to Delivery Note.

Booking and delivery meet only at the Sales Order number:
  * a VFY Delivery Note MAY carry a Sales Order (optional since 2026-09-06 —
    the 2026-09-05 revision had made it mandatory); when it does, the order
    must be a submitted open VFY order of the same customer;
  * the note may carry ANY stocked batch — booked batches are reference only;
  * rows are kept exactly as entered — a VFY order carries one row per
    BOOKED batch, so linking / splitting rows against those would chop every
    shipped batch into "booked weight + remainder" (the TEST-CHALLAN-DN00006
    report of 2026-09-05). The note links to the order on the header only;
    the item-level cap is the "row level" rule;
  * delivered / pending are sums over the submitted notes linked to the order;
  * cumulative delivery never exceeds the order — item level and total level,
    within the standard Over Delivery Allowance;
  * booking is released Sales-Order-wise (effective booking = ordered −
    delivered, floored at zero, applied down the booked rows in order);
  * the Stock Sheet (Balance Report) shows Delivered / Pending qty and weight;
  * the Sales Order dropdown follows the Customer;
  * HTY is unchanged throughout.

Fixtures are built on real bench masters: customer "Shree Ram Sevak Silk
Mills", item 58D/8F, VFY batches of container MCJC-1680 with stock in
Finished Goods - MC. Every submitted document is cancelled and deleted again.
"""
import inspect
import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, today

from mhr import utilis

CUSTOMER = "Shree Ram Sevak Silk Mills"
OTHER_CUSTOMER = "G.M.Weaves"
ITEM = "58D/8F"
WH = "Finished Goods - MC"
COMPANY = "Meher Creations"
CS_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")

# Chosen at setUp from the bench: five VFY batches of ITEM with >= 20 in stock
# in WH and no open booking — three to book on the order, two to ship
# (Raj: any stocked batch may ship, booked or not). Picked dynamically so a
# Sales Order left on the bench (e.g. the browser walkthrough's) cannot
# collide with the fixture.
BOOKED = []
SHIPPED = None
SHIPPED_2 = None


def _pick_batches():
    global BOOKED, SHIPPED, SHIPPED_2
    if BOOKED and SHIPPED and SHIPPED_2:
        return True
    rows = frappe.db.sql("""
        SELECT b.name FROM `tabBatch` b
        JOIN (SELECT sbe.batch_no, SUM(sbe.qty) bal FROM `tabSerial and Batch Entry` sbe
              JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
              WHERE sbb.warehouse = %s AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
                AND sbb.type_of_transaction IN ('Inward', 'Outward')
              GROUP BY sbe.batch_no HAVING bal >= 20) s ON s.batch_no = b.name
        WHERE b.item = %s AND b.custom_transaction_type = 'VFY' AND b.disabled = 0
          AND IFNULL(b.custom_cone, 0) > 0 AND b.batch_qty >= 20
        ORDER BY b.name LIMIT 40""", (WH, ITEM))
    names = [r[0] for r in rows]
    booked = utilis.effective_booking_by_batch(names)
    free = [n for n in names if flt(booked.get(n, {}).get("qty", 0)) == 0]
    if len(free) < 5:
        return False
    BOOKED = free[:3]
    SHIPPED, SHIPPED_2 = free[3], free[4]
    return True


def _masters_present():
    return (frappe.db.exists("Customer", CUSTOMER) and frappe.db.exists("Item", ITEM) and _pick_batches())


def _make_so(qtys=(20, 20, 20)):
    so = frappe.new_doc("Sales Order")
    so.update({"customer": CUSTOMER, "company": COMPANY, "transaction_type": "VFY",
               "transaction_date": today(), "delivery_date": add_days(today(), 7),
               "set_warehouse": WH, "selling_price_list": "Standard Selling", "currency": "INR"})
    for qty, batch in zip(qtys, BOOKED):
        so.append("items", {"item_code": ITEM, "qty": qty, "rate": 100, "delivery_date": add_days(today(), 7),
                            "warehouse": WH, "custom_batch_no": batch})
    so.insert(ignore_permissions=True)
    so.submit()
    return so


def _make_dn(so_name, qtys, **header):
    dn = frappe.new_doc("Delivery Note")
    dn.update({"customer": CUSTOMER, "company": COMPANY, "transaction_type": "VFY",
               "posting_date": today(), "set_posting_time": 1, "set_warehouse": WH,
               "custom_sales_person": "Jayendrabhai", "custom_sales_order": so_name,
               "selling_price_list": "Standard Selling", "currency": "INR"})
    dn.update(header)
    for spec in qtys:
        qty, batch = spec if isinstance(spec, tuple) else (spec, SHIPPED)
        dn.append("items", {"item_code": ITEM, "qty": qty, "rate": 100, "warehouse": WH,
                            "batch_no": batch, "use_serial_batch_fields": 1})
    return dn


def _cleanup(*docs):
    for doc in docs:
        if not doc or not doc.name or not frappe.db.exists(doc.doctype, doc.name):
            continue
        try:
            d = frappe.get_doc(doc.doctype, doc.name)
            if d.docstatus == 1:
                d.cancel()
            frappe.delete_doc(doc.doctype, doc.name, force=1, ignore_permissions=True)
        except Exception:
            pass


class _WithOrder(FrappeTestCase):
    def setUp(self):
        if not _masters_present():
            self.skipTest("Bench lacks the VFY masters this suite is built on.")
        self.so = _make_so()
        self.docs = []

    def tearDown(self):
        _cleanup(*self.docs, self.so)


class TestVfyNoteNeedsItsOrder(_WithOrder):

    def test_without_sales_order_the_note_saves_and_submits_as_before(self):
        """2026-09-06: the Sales Order is optional. No order -> no allocation,
        no caps, no booking sync; stock moves exactly as it always did."""
        dn = _make_dn(None, [(5, SHIPPED)]); self.docs.append(dn)
        dn.insert(ignore_permissions=True)
        self.assertFalse(dn.custom_so_total_qty, "No order -> nothing to show in Total Quantity.")
        self.assertEqual(len(dn.items), 1)
        dn.submit()
        try:
            self.assertEqual(dn.docstatus, 1)
            self.assertEqual(utilis._delivered_by_sales_order([self.so.name])[self.so.name]["qty"], 0.0,
                             "An unlinked note never counts against any order.")
            self.assertEqual([utilis.effective_booking_by_batch(BOOKED)[b]["qty"] for b in BOOKED], [20.0, 20.0, 20.0])
        finally:
            dn.reload(); dn.cancel()

    def test_no_order_means_no_order_checks_at_all(self):
        with patch.object(frappe.db, "get_value", wraps=frappe.db.get_value) as gv:
            utilis.require_vfy_sales_order(frappe._dict(transaction_type="VFY", customer=OTHER_CUSTOMER, custom_sales_order=None))
            self.assertFalse(any(c.args and c.args[0] == "Sales Order" for c in gv.call_args_list))

    def test_other_customers_order_is_refused(self):
        dn = _make_dn(self.so.name, [10], customer=OTHER_CUSTOMER)
        with self.assertRaises(frappe.ValidationError):
            dn.insert(ignore_permissions=True)

    def test_hty_and_returns_untouched(self):
        utilis.require_vfy_sales_order(frappe._dict(transaction_type="HTY", custom_sales_order=None))
        utilis.require_vfy_sales_order(frappe._dict(transaction_type="VFY", is_return=1, custom_sales_order=None))

    def test_hooks_registered_in_order(self):
        import mhr.hooks as hooks
        dn = hooks.doc_events["Delivery Note"]
        self.assertIn("mhr.utilis.allocate_delivery_note_to_sales_order", dn["before_validate"])
        self.assertIn("mhr.utilis.sync_sales_order_delivery", dn["on_submit"])
        self.assertIn("mhr.utilis.sync_sales_order_delivery", dn["on_cancel"])
        v = dn["validate"]
        self.assertLess(v.index("mhr.utilis.require_vfy_sales_order"), v.index("mhr.utilis.validate_so_delivery_qty"))


class TestRowsAllocateAgainstTheOrder(_WithOrder):

    def test_rows_are_never_split_and_carry_no_per_row_link(self):
        """A 30 kg row against three 20 kg booked rows stays ONE row — the
        same batch must not show up two or three times (prod report,
        TEST-CHALLAN-DN00006)."""
        dn = _make_dn(self.so.name, [30]); self.docs.append(dn)
        dn.insert(ignore_permissions=True)
        self.assertEqual([(r.idx, r.qty, r.batch_no) for r in dn.items], [(1, 30.0, SHIPPED)])
        self.assertIsNone(dn.items[0].so_detail)
        self.assertIsNone(dn.items[0].against_sales_order)
        self.assertEqual(dn.custom_sales_order, self.so.name)
        self.assertEqual(dn.total_qty, 30.0)
        dn.save(ignore_permissions=True)
        self.assertEqual(len(dn.items), 1, "Re-saving adds nothing.")

    def test_mapped_row_links_are_cleared_on_a_vfy_note(self):
        """ERPNext's mapper stamps so_detail; on a VFY note only the header
        rule applies, so its per-row over-delivery check never fires when the
        shipped weight differs from the booked one."""
        dn = _make_dn(self.so.name, [(25.9, SHIPPED)])
        dn.items[0].so_detail = self.so.items[0].name
        dn.items[0].against_sales_order = self.so.name
        utilis.allocate_delivery_note_to_sales_order(dn)
        self.assertIsNone(dn.items[0].so_detail)
        self.assertIsNone(dn.items[0].against_sales_order)
        self.assertEqual(dn.items[0].qty, 25.9)

    def test_item_not_on_the_order_is_refused(self):
        dn = _make_dn(self.so.name, [5])
        dn.items[0].item_code = "58D/24F"
        with self.assertRaises(frappe.ValidationError) as ctx:
            dn.insert(ignore_permissions=True)
        self.assertIn("not on Sales Order", str(ctx.exception))

    def test_hty_note_rows_are_left_alone(self):
        doc = frappe._dict(transaction_type="HTY", custom_sales_order=self.so.name,
                           items=[frappe._dict(item_code=ITEM, qty=30, idx=1)])
        utilis.allocate_delivery_note_to_sales_order(doc)
        self.assertEqual(len(doc.get("items")), 1)
        self.assertIsNone(doc.get("items")[0].get("so_detail"))


class TestCumulativeCapWithTolerance(_WithOrder):

    def test_total_over_the_order_is_blocked(self):
        dn = _make_dn(self.so.name, [61])
        with self.assertRaises(frappe.ValidationError) as ctx:
            dn.insert(ignore_permissions=True)
        self.assertIn("reduce it by", str(ctx.exception))

    def test_allowance_from_stock_settings_widens_the_cap(self):
        dn = _make_dn(self.so.name, [66])
        with patch.object(utilis, "_over_delivery_allowance", return_value=10.0):
            utilis.validate_so_delivery_qty(dn)                       # 66 <= 60 * 1.10
        dn = _make_dn(self.so.name, [67])
        with patch.object(utilis, "_over_delivery_allowance", return_value=10.0):
            with self.assertRaises(frappe.ValidationError):
                utilis.validate_so_delivery_qty(dn)

    def test_item_level_cap_cannot_hide_behind_another_item(self):
        """Order: 60 of ITEM. A note of 61 of ITEM is over even if the order
        also carried another item — the per-item branch speaks."""
        with patch.object(utilis, "_delivered_by_sales_order_item", return_value={ITEM: 50.0}), \
             patch.object(utilis, "_delivered_against_sales_order", return_value=0.0):
            dn = _make_dn(self.so.name, [11])
            with self.assertRaises(frappe.ValidationError) as ctx:
                utilis.validate_so_delivery_qty(dn)
        self.assertIn(ITEM, str(ctx.exception))


class TestBookingIsReleasedSalesOrderWise(_WithOrder):

    def test_undelivered_order_books_every_row(self):
        booked = utilis.effective_booking_by_batch(BOOKED)
        self.assertEqual([booked[b]["qty"] for b in BOOKED], [20.0, 20.0, 20.0])

    def test_delivery_with_other_batches_releases_the_booking_down_the_rows(self):
        with patch.object(utilis, "_delivered_by_sales_order", return_value={self.so.name: {"qty": 30.0, "weight": 0.0}}):
            booked = utilis.effective_booking_by_batch(BOOKED)
            from mhr import sales_order
            self.assertEqual([booked[b]["qty"] for b in BOOKED], [0.0, 10.0, 20.0],
                             "30 delivered (any batch) frees row 1 wholly and half of row 2.")
            self.assertEqual(sales_order._get_available_qty(BOOKED[0], 22.0), 22.0)   # nothing held on it any more (master passed in)
            self.assertEqual(sales_order._booked_qty_by_batch(BOOKED), {BOOKED[1]: 10.0, BOOKED[2]: 20.0})
            st = utilis.sales_order_booking_state(sales_orders=[self.so.name])[self.so.name]
            self.assertEqual((st["ordered_qty"], st["delivered_qty"], st["pending_qty"], st["effective_booking"]), (60.0, 30.0, 30.0, 30.0))

    def test_fully_delivered_or_closed_order_books_nothing(self):
        with patch.object(utilis, "_delivered_by_sales_order", return_value={self.so.name: {"qty": 60.0, "weight": 0.0}}):
            self.assertEqual([utilis.effective_booking_by_batch(BOOKED)[b]["qty"] for b in BOOKED], [0.0, 0.0, 0.0])
        frappe.db.set_value("Sales Order", self.so.name, "status", "Closed", update_modified=False)
        try:
            self.assertEqual(utilis.effective_booking_by_batch(BOOKED)[BOOKED[0]]["qty"], 0.0)
        finally:
            frappe.db.set_value("Sales Order", self.so.name, "status", "To Deliver and Bill", update_modified=False)

    def test_sales_order_validate_uses_the_released_booking(self):
        """A second order may book the batch the first order no longer holds."""
        on_hand = flt(frappe.db.get_value("Batch", BOOKED[0], "batch_qty"))
        with patch.object(utilis, "_delivered_by_sales_order", return_value={self.so.name: {"qty": 60.0, "weight": 0.0}}):
            other = frappe._dict(name="SO-OTHER", items=[frappe._dict(idx=1, custom_batch_no=BOOKED[0], qty=on_hand)])
            utilis.validate_so_available_qty(other)                    # the whole batch: nothing booked on it
        with self.assertRaises(frappe.ValidationError):
            utilis.validate_so_available_qty(other)                    # unpatched: 20 still booked -> refused


class TestEndToEnd(_WithOrder):

    def test_submit_updates_delivered_and_cancel_restores_booking(self):
        """Two unbooked, stocked batches (20 + 10). On submit the order's rows
        are credited top-down per item by sync_sales_order_delivery, its
        per_delivered and status follow, and cancel reverses all of it."""
        dn = _make_dn(self.so.name, [(20, SHIPPED), (10, SHIPPED_2)]); self.docs.append(dn)
        dn.insert(ignore_permissions=True)
        self.assertEqual([(r.qty, r.so_detail) for r in dn.items], [(20.0, None), (10.0, None)])
        self.assertEqual(dn.custom_so_total_qty, 60.0, "Nothing delivered yet: remaining = ordered.")
        dn.submit()
        try:
            self.assertEqual(utilis._delivered_by_sales_order([self.so.name])[self.so.name]["qty"], 30.0)
            self.assertEqual(utilis.get_so_remaining_qty(self.so.name), 30.0, "A new note now sees 60 − 30.")
            self.assertEqual(utilis.get_so_remaining_qty(self.so.name, dn.name), 60.0, "The note never counts against itself.")
            self.assertEqual(frappe.db.get_value("Delivery Note", dn.name, "custom_so_total_qty"), 60.0, "Frozen at submission.")
            self.so.reload()
            self.assertEqual([r.delivered_qty for r in self.so.items], [20.0, 10.0, 0.0], "Credited down the rows per item.")
            self.assertEqual(self.so.per_delivered, 50.0)
            self.assertEqual(self.so.status, "To Deliver and Bill")
            booked = utilis.effective_booking_by_batch(BOOKED)
            self.assertEqual([booked[b]["qty"] for b in BOOKED], [0.0, 10.0, 20.0])
            self.assertEqual(utilis._delivered_by_sales_order_row(self.so.name)[self.so.items[0].name], 20.0)
        finally:
            dn.reload(); dn.cancel()
        self.assertEqual(utilis._delivered_by_sales_order([self.so.name])[self.so.name]["qty"], 0.0)
        self.assertEqual([utilis.effective_booking_by_batch(BOOKED)[b]["qty"] for b in BOOKED], [20.0, 20.0, 20.0])
        self.so.reload()
        self.assertEqual(([r.delivered_qty for r in self.so.items], self.so.per_delivered), ([0.0, 0.0, 0.0], 0.0))

    def test_full_delivery_moves_the_order_to_to_bill(self):
        dn = _make_dn(self.so.name, [(20, SHIPPED), (20, SHIPPED_2), (20, BOOKED[0])]); self.docs.append(dn)
        dn.insert(ignore_permissions=True)
        dn.submit()
        try:
            self.so.reload()
            self.assertEqual((self.so.per_delivered, self.so.status), (100.0, "To Bill"))
            self.assertEqual([utilis.effective_booking_by_batch(BOOKED).get(b, {}).get("qty", 0.0) for b in BOOKED], [0.0, 0.0, 0.0])
        finally:
            dn.reload(); dn.cancel()
        self.so.reload()
        self.assertEqual(self.so.status, "To Deliver and Bill")


class TestStockSheet(_WithOrder):

    def test_report_module(self):
        mod = frappe.get_module("mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)")
        names = [c["fieldname"] for c in mod.get_columns({})]
        for col in ("Delivered Qty", "Delivered Weight", "Pending Qty", "Pending Weight"):
            self.assertIn(col, names)
        self.assertEqual(names.index("Delivered Qty"), names.index("Lifting Terms") + 1)
        booked = mod.get_booked_quantities(BOOKED)
        self.assertEqual(sorted(booked), sorted(BOOKED))
        bk = booked[BOOKED[0]][0]
        self.assertEqual(bk["sales_order"], self.so.name)
        self.assertEqual((bk["booked_qty"], bk["delivered_qty"], bk["pending_qty"]), (20.0, 0.0, 60.0))
        with patch.object(utilis, "_delivered_by_sales_order", return_value={self.so.name: {"qty": 30.0, "weight": 0.0}}):
            booked = mod.get_booked_quantities(BOOKED)
            self.assertNotIn(BOOKED[0], booked, "Released booking rows are not listed.")
            self.assertEqual(booked[BOOKED[1]][0]["booked_qty"], 10.0)
            self.assertEqual(booked[BOOKED[1]][0]["pending_qty"], 30.0)


class TestMapperAndClientScript(FrappeTestCase):

    def test_mapper_stamps_the_header_order_for_vfy(self):
        if not _masters_present():
            self.skipTest("Bench lacks the VFY masters this suite is built on.")
        so = _make_so()
        try:
            from mhr.sales_order_to_delivery_note import carry_sales_order_details
            target = frappe.new_doc("Delivery Note")
            target.append("items", {"item_code": ITEM, "qty": 5, "so_detail": so.items[0].name})
            carry_sales_order_details(so.name, target)
            self.assertEqual(target.custom_sales_order, so.name)
        finally:
            _cleanup(so)

    def test_client_script_filters_orders_by_customer(self):
        with open(CS_FIXTURE, encoding="utf-8") as fh:
            cs = next(c for c in json.load(fh) if c["name"] == "MI1-I120 — Delivery Note Sales Order by Customer")
        self.assertEqual((cs["dt"], cs["enabled"], cs["module"]), ("Delivery Note", 1, "Mhr"))
        src = cs["script"]
        self.assertIn("frm.set_query('custom_sales_order'", src)
        self.assertIn("customer: customer || '__no_customer__'", src)
        self.assertIn("frm.set_df_property('custom_sales_order', 'read_only', customer ? 0 : 1)", src)
        self.assertIn("customer(frm)", src)
        self.assertIn("frm.set_value('custom_sales_order', '')", src)
        self.assertIn("!== 'HTY'", src, "VFY only — HTY unchanged.")
        db = frappe.db.get_value("Client Script", cs["name"], "script")
        self.assertEqual(db, src)
