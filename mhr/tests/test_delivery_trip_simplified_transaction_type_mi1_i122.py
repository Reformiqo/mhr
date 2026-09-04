"""MI1-I122 (Raj 2026-09-03) — Delivery Trip Simplified: Transaction Type filter + column.

  * HTY selected  -> only HTY rows;  VFY selected -> only VFY rows;
  * nothing selected -> both;
  * the type is visible as its own column;
  * everything else about the report is unchanged.
"""
import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.report.delivery_trip_simplified import delivery_trip_simplified as report

JS = os.path.join(os.path.dirname(report.__file__), "delivery_trip_simplified.js")
WINDOW = {"from_date": "2000-01-01", "to_date": "2099-12-31"}


class TestColumn(FrappeTestCase):

    def test_transaction_type_is_the_last_column(self):
        cols = report.get_columns()
        self.assertEqual(cols[-1]["fieldname"], "transaction_type")
        self.assertEqual(cols[-1]["label"], "Transaction Type")
        self.assertEqual([c["fieldname"] for c in cols[:7]],
                         ["departure_time", "delivery_note", "total_qty", "customer", "vehicle", "item_length", "driver_name"],
                         "The MI1-I35 seven keep their order.")

    def test_every_row_carries_a_type(self):
        _, rows = report.execute(dict(WINDOW))
        if not rows:
            self.skipTest("No submitted Delivery Trips on this bench.")
        self.assertTrue(all(r["transaction_type"] in ("VFY", "HTY") for r in rows),
                        "Legacy notes without the field must read VFY, never blank.")


class TestFilter(FrappeTestCase):

    def test_blank_shows_both_and_each_mode_only_itself(self):
        _, both = report.execute(dict(WINDOW))
        if not both:
            self.skipTest("No submitted Delivery Trips on this bench.")
        _, vfy = report.execute(dict(WINDOW, transaction_type="VFY"))
        _, hty = report.execute(dict(WINDOW, transaction_type="HTY"))
        self.assertTrue(all(r["transaction_type"] == "VFY" for r in vfy))
        self.assertTrue(all(r["transaction_type"] == "HTY" for r in hty))
        self.assertEqual(len(vfy) + len(hty), len(both), "Blank filter = union of the two modes.")
        self.assertEqual(len(vfy), sum(1 for r in both if r["transaction_type"] == "VFY"))

    def test_unknown_value_is_ignored_not_applied(self):
        _, both = report.execute(dict(WINDOW))
        _, odd = report.execute(dict(WINDOW, transaction_type="All"))
        self.assertEqual(len(odd), len(both))

    def test_type_comes_from_the_note_then_the_trip_then_vfy(self):
        self.assertEqual(report.TRANSACTION_TYPE_SQL,
                         "COALESCE(NULLIF(dn.transaction_type, ''), NULLIF(dt.transaction_type, ''), 'VFY')")
        src = inspect.getsource(report.get_data)
        self.assertIn("{TRANSACTION_TYPE_SQL}     AS transaction_type", src)
        self.assertIn('conditions.append(f"{TRANSACTION_TYPE_SQL} = %(transaction_type)s")', src,
                      "Column and filter must use the same expression.")

    def test_existing_filters_untouched(self):
        _, rows = report.execute(dict(WINDOW, vehicle="__no_such_vehicle__"))
        self.assertEqual(rows, [])
        _, rows = report.execute(dict(WINDOW, customer="__no_such_customer__", transaction_type="VFY"))
        self.assertEqual(rows, [])

    def test_single_mode_users_are_forced_like_every_other_report(self):
        self.assertIn("enforce_role_scoped_transaction_type", inspect.getsource(report.execute))


class TestClientFilter(FrappeTestCase):

    def test_select_with_blank_vfy_hty(self):
        with open(JS, encoding="utf-8") as fh:
            js = fh.read()
        i = js.find('fieldname: "transaction_type"')
        self.assertGreater(i, -1, "Transaction Type filter missing from the report JS.")
        block = js[i:i + 400]
        self.assertIn('fieldtype: "Select"', block)
        self.assertIn('options: "\\nVFY\\nHTY"', block, "Blank first option = both modes (Raj).")
        self.assertIn('default: ""', block)
        for kept in ('fieldname: "vehicle"', 'fieldname: "driver"', 'fieldname: "customer"', 'fieldname: "from_date"', 'fieldname: "to_date"'):
            self.assertIn(kept, js)
