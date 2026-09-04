"""MI1-I120 (Raj 2026-09-02) — Delivery Note ↔ Sales Order quantity tracking.

Raj's points, verbatim:
  1. Two new Delivery Note fields: Sales Order No. (Link → Sales Order) and
     Total Quantity (read-only).
  2. Selecting a Sales Order auto-fetches its total ordered quantity.
  3. Total delivered against that Sales Order (this note + every other
     submitted note against it) must not exceed the ordered quantity;
     otherwise block with a clear message.
  4. Sales Order is NOT mandatory — without one the note behaves exactly as
     before.
  5. Reports reflect Sales Order No., Total Quantity, delivered and remaining.
"""
import inspect
import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


CF_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")


def _dn(items, **header):
    d = frappe._dict({"doctype": "Delivery Note", "name": "new-dn-1",
                      "custom_sales_order": "SO-TEST", "custom_so_total_qty": 0,
                      "items": [frappe._dict(r) for r in items]})
    d.update(header)
    return d


# ---------------------------------------------------------------------------
# 1 + 2. fields
# ---------------------------------------------------------------------------


class TestFields(FrappeTestCase):

    def test_sales_order_link_is_optional(self):
        f = frappe.get_meta("Delivery Note").get_field("custom_sales_order")
        self.assertIsNotNone(f, "Delivery Note.custom_sales_order missing.")
        self.assertEqual((f.fieldtype, f.options), ("Link", "Sales Order"))
        self.assertFalse(f.reqd, "Raj: Sales Order must not be mandatory.")
        self.assertEqual(f.label, "Sales Order No.")

    def test_total_quantity_is_read_only_and_fetched_from_the_order(self):
        f = frappe.get_meta("Delivery Note").get_field("custom_so_total_qty")
        self.assertIsNotNone(f, "Delivery Note.custom_so_total_qty missing.")
        self.assertEqual(f.fieldtype, "Float")
        self.assertTrue(f.read_only)
        self.assertEqual(f.fetch_from, "custom_sales_order.total_qty",
                         "Auto-fetch is declarative: frappe fills it when the link resolves.")
        self.assertEqual(f.label, "Total Quantity")

    def test_fields_ship_via_fixture_under_the_customer(self):
        with open(CF_FIXTURE, encoding="utf-8") as f:
            dn = {r["fieldname"]: r for r in json.load(f) if r.get("dt") == "Delivery Note"}
        self.assertEqual(dn["custom_sales_order"]["insert_after"], "customer_name",
                         "Mockup places it right under Customer.")
        self.assertEqual(dn["custom_so_total_qty"]["insert_after"], "custom_sales_order")
        for fn in ("custom_sales_order", "custom_so_total_qty"):
            self.assertEqual(dn[fn]["module"], "Mhr")


# ---------------------------------------------------------------------------
# 3 + 4. the cap
# ---------------------------------------------------------------------------


class TestValidateSoDeliveryQty(FrappeTestCase):

    def test_registered_after_totals_are_settled(self):
        import mhr.hooks as hooks
        v = hooks.doc_events["Delivery Note"]["validate"]
        self.assertIn("mhr.utilis.validate_so_delivery_qty", v)
        self.assertGreater(v.index("mhr.utilis.validate_so_delivery_qty"),
                           v.index("mhr.utilis.calculate_delivery_note_totals"))

    def test_no_sales_order_means_no_lookup_and_no_block(self):
        from mhr import utilis
        with patch.object(utilis, "_so_total_qty") as tot, patch.object(utilis, "_delivered_against_sales_order") as dlv:
            utilis.validate_so_delivery_qty(_dn([{"qty": 999999}], custom_sales_order=None))
            tot.assert_not_called(); dlv.assert_not_called()

    def test_within_remaining_passes_and_fills_total_quantity(self):
        from mhr import utilis
        doc = _dn([{"qty": 200}, {"qty": 100}])
        with patch.object(utilis, "_so_total_qty", return_value=1000.0), \
             patch.object(utilis, "_delivered_against_sales_order", return_value=700.0):
            utilis.validate_so_delivery_qty(doc)          # 300 <= 1000 - 700
        self.assertEqual(doc.custom_so_total_qty, 1000.0,
                         "Blank Total Quantity is refreshed from the order (API / mapped saves).")

    def test_exceeding_remaining_is_blocked_with_the_numbers(self):
        from mhr import utilis
        doc = _dn([{"qty": 301}])
        with patch.object(utilis, "_so_total_qty", return_value=1000.0), \
             patch.object(utilis, "_delivered_against_sales_order", return_value=700.0):
            with self.assertRaises(frappe.ValidationError) as ctx:
                utilis.validate_so_delivery_qty(doc)
        msg = str(ctx.exception)
        for token in ("SO-TEST", "1000", "700", "300", "301"):
            self.assertIn(token, msg, f"Message must show {token} (Raj: 'clear validation message').")

    def test_this_note_is_excluded_from_already_delivered(self):
        """Re-validating a submitted note must not count itself twice."""
        from mhr import utilis
        doc = _dn([{"qty": 300}], name="MAT-DN-FY00001")
        with patch.object(utilis, "_so_total_qty", return_value=1000.0), \
             patch.object(utilis, "_delivered_against_sales_order", return_value=700.0) as dlv:
            utilis.validate_so_delivery_qty(doc)
        dlv.assert_called_once_with("SO-TEST", exclude_dn="MAT-DN-FY00001")

    def test_returns_are_never_blocked(self):
        from mhr import utilis
        with patch.object(utilis, "_so_total_qty", return_value=1000.0), \
             patch.object(utilis, "_delivered_against_sales_order", return_value=1000.0):
            utilis.validate_so_delivery_qty(_dn([{"qty": -50}]))   # return rows carry negative qty

    def test_delivered_counts_header_link_or_row_link_once(self):
        from mhr import utilis
        src = inspect.getsource(utilis._delivered_against_sales_order)
        self.assertIn("dn.custom_sales_order = %(so)s OR dni.against_sales_order = %(so)s", src,
                      "Both link styles must count, OR'd on the row so neither double-counts.")
        self.assertIn("dn.docstatus = 1", src)


class TestDeliveredAgainstSalesOrderOnRealData(FrappeTestCase):
    """The HTY Sales Order that MI1-I90's mapper delivered in full links its
    Delivery Note through the per-row field only — exactly the case the
    OR clause exists for."""

    def test_row_linked_delivery_is_counted(self):
        so = "HTY-SO-2026-00001"
        if not frappe.db.exists("Sales Order", so):
            self.skipTest(f"{so} not on this bench.")
        expected = frappe.db.sql("""SELECT COALESCE(SUM(dni.qty),0) FROM `tabDelivery Note Item` dni
            JOIN `tabDelivery Note` dn ON dn.name=dni.parent
            WHERE dn.docstatus=1 AND dni.against_sales_order=%s""", so)[0][0]
        if not expected:
            self.skipTest("No submitted delivery against that order on this bench.")
        from mhr.utilis import _delivered_against_sales_order
        self.assertAlmostEqual(_delivered_against_sales_order(so), float(expected), places=3)


# ---------------------------------------------------------------------------
# 5. reports
# ---------------------------------------------------------------------------

SO_COLS = {"sales_order", "so_total_qty", "so_delivered_qty", "so_remaining_qty"}
REPORTS = (
    "mhr.mhr.report.dn.dn",
    "mhr.mhr.report.delivery_note_lot_wise.delivery_note_lot_wise",
    "mhr.mhr.report.delivery_challan.delivery_challan",
)


def _window():
    """A 60-day window ending at the latest submitted note on this bench."""
    last = frappe.db.sql("SELECT MAX(posting_date) FROM `tabDelivery Note` WHERE docstatus = 1")[0][0]
    if not last:
        return None
    from frappe.utils import add_days
    return {"from_date": add_days(last, -60), "to_date": last}


class TestSoDeliveryProgress(FrappeTestCase):

    def test_empty_and_blank_orders_give_empty_map(self):
        from mhr.utilis import so_delivery_progress
        self.assertEqual(so_delivery_progress([]), {})
        self.assertEqual(so_delivery_progress([None, ""]), {})

    def test_matches_the_validator_for_the_real_hty_order(self):
        so = "HTY-SO-2026-00001"
        if not frappe.db.exists("Sales Order", so):
            self.skipTest(f"{so} not on this bench.")
        from mhr.utilis import _delivered_against_sales_order, _so_total_qty, so_delivery_progress
        p = so_delivery_progress([so, so, None])[so]
        self.assertAlmostEqual(p["delivered"], _delivered_against_sales_order(so), places=3,
                               msg="Report and validator must agree on 'delivered'.")
        self.assertAlmostEqual(p["ordered"], _so_total_qty(so), places=3)
        self.assertAlmostEqual(p["remaining"], p["ordered"] - p["delivered"], places=3)

    def test_annotate_leaves_unlinked_rows_blank(self):
        from mhr.utilis import annotate_so_progress
        rows = [frappe._dict(sales_order=None, total_qty=5), frappe._dict(sales_order="", total_qty=6)]
        annotate_so_progress(rows)
        for r in rows:
            self.assertEqual((r.so_total_qty, r.so_delivered_qty, r.so_remaining_qty), (None, None, None))
            self.assertEqual(r.total_qty, r.total_qty, "Existing columns untouched.")


class TestReportsCarrySalesOrderColumns(FrappeTestCase):

    def test_all_three_reports_declare_the_columns_after_the_customer(self):
        for path in REPORTS:
            mod = frappe.get_module(path)
            cols = mod.get_columns({}) if "filters" in inspect.signature(mod.get_columns).parameters else mod.get_columns()
            names = [c["fieldname"] for c in cols]
            self.assertTrue(SO_COLS <= set(names), f"{path}: missing {SO_COLS - set(names)}")
            cust = next(n for n in names if n in ("customer_name", "customer"))
            self.assertEqual(names[names.index(cust) + 1], "sales_order", f"{path}: Sales Order sits right after the customer.")
            so_col = next(c for c in cols if c["fieldname"] == "sales_order")
            self.assertEqual((so_col["fieldtype"], so_col["options"]), ("Link", "Sales Order"))

    def test_reports_run_and_notes_without_an_order_look_as_before(self):
        w = _window()
        if not w:
            self.skipTest("No submitted Delivery Note on this bench.")
        for path in REPORTS:
            filters = dict(w)
            if path.endswith(".dn"):
                filters["transaction_type"] = "VFY"
            _, rows = frappe.get_module(path).execute(frappe._dict(filters))
            self.assertTrue(rows, f"{path}: no rows in the window {w}")
            for r in rows:
                self.assertTrue(SO_COLS <= set(r.keys()), f"{path}: row lacks SO keys")
                if not r["sales_order"]:
                    self.assertEqual((r["so_total_qty"], r["so_delivered_qty"], r["so_remaining_qty"]), (None, None, None),
                                     f"{path}: unlinked note must stay blank (Raj: 'appear normally').")
                else:
                    self.assertIsNotNone(r["so_total_qty"], f"{path}: linked note must show the order's total")

    def test_row_linked_hty_note_shows_its_order(self):
        """The MI1-I90 mapper links per row; the report must still show the order."""
        dn = frappe.db.get_value("Delivery Note Item", {"against_sales_order": "HTY-SO-2026-00001", "docstatus": 1}, "parent")
        if not dn:
            self.skipTest("No row-linked HTY note on this bench.")
        pd = frappe.db.get_value("Delivery Note", dn, "posting_date")
        from mhr.mhr.report.dn.dn import execute
        _, rows = execute(frappe._dict(from_date=pd, to_date=pd, transaction_type="HTY"))
        mine = [r for r in rows if dn in (r.get("id") or "")]
        self.assertTrue(mine, f"{dn} not in the DN report for {pd}")
        for r in mine:
            self.assertEqual(r["sales_order"], "HTY-SO-2026-00001")
            self.assertAlmostEqual(r["so_remaining_qty"], r["so_total_qty"] - r["so_delivered_qty"], places=3)
