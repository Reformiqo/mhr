"""MI1-I35 — Delivery Trip Simplified report.

A stripped-down Delivery Trip report per Raj's spec — 7 columns, no
totals, no Refrens fields. The existing Delivery Trip Report is for
Refrens and stays untouched.
"""
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.report.delivery_trip_simplified import delivery_trip_simplified as report


EXPECTED_COLUMNS = [
    ("departure_time", "Datetime"),
    ("delivery_note",  "Link"),
    ("total_qty",      "Float"),
    ("customer",       "Link"),
    ("vehicle",        "Link"),
    ("item_length",    "Data"),
    ("driver_name",    "Data"),
]


class TestDeliveryTripSimplifiedColumns(FrappeTestCase):
    """The FRD pins the column order — regression here would change what
    Raj sees."""

    def test_column_count(self):
        cols = report.get_columns()
        self.assertEqual(len(cols), 7,
            f"Expected exactly 7 columns (per ticket spec), got {len(cols)}.")

    def test_column_order_and_types(self):
        cols = report.get_columns()
        for i, (fname, ftype) in enumerate(EXPECTED_COLUMNS):
            with self.subTest(position=i, fieldname=fname):
                self.assertEqual(cols[i]["fieldname"], fname,
                    f"Column {i} fieldname expected {fname!r}, got {cols[i]['fieldname']!r}")
                self.assertEqual(cols[i]["fieldtype"], ftype,
                    f"Column {i} ({fname}) type expected {ftype!r}, got {cols[i]['fieldtype']!r}")

    def test_link_columns_point_to_right_doctypes(self):
        cols = {c["fieldname"]: c for c in report.get_columns()}
        self.assertEqual(cols["delivery_note"]["options"], "Delivery Note")
        self.assertEqual(cols["customer"]["options"], "Customer")
        self.assertEqual(cols["vehicle"]["options"], "Vehicle")


class TestDeliveryTripSimplifiedExecute(FrappeTestCase):
    """Smoke tests — empty-range execute returns (cols, [])."""

    def test_empty_date_range_returns_no_rows(self):
        cols, rows = report.execute({"from_date": "1900-01-01", "to_date": "1900-01-02"})
        self.assertEqual(len(cols), 7)
        self.assertEqual(rows, [])

    def test_execute_accepts_optional_filters(self):
        # All optional filters absent — must not crash, must return tuple.
        out = report.execute({})
        self.assertEqual(len(out), 2)
        cols, rows = out
        self.assertIsInstance(cols, list)
        self.assertIsInstance(rows, list)

    def test_filter_by_vehicle_does_not_crash(self):
        cols, rows = report.execute({"vehicle": "__no_such_vehicle__"})
        self.assertEqual(rows, [])

    def test_filter_by_customer_does_not_crash(self):
        cols, rows = report.execute({"customer": "__no_such_customer__"})
        self.assertEqual(rows, [])


class TestDeliveryTripSimplifiedRegistered(FrappeTestCase):
    """The Report doc must exist in the DB so it appears in the report list."""

    def test_report_doc_registered(self):
        self.assertTrue(
            frappe.db.exists("Report", "Delivery Trip Simplified"),
            "Report 'Delivery Trip Simplified' must be registered.",
        )

    def test_report_meta(self):
        d = frappe.db.get_value(
            "Report", "Delivery Trip Simplified",
            ["ref_doctype", "module", "is_standard", "report_type", "disabled"],
            as_dict=True,
        )
        self.assertEqual(d.ref_doctype, "Delivery Trip")
        self.assertEqual(d.module, "Mhr")
        self.assertEqual(d.is_standard, "Yes")
        self.assertEqual(d.report_type, "Script Report")
        self.assertEqual(d.disabled, 0)
