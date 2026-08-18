"""MI1-I94 (Raj 2026-08-13): Stock Sheet (Balance Report) must emit
one FULL row per Sales Order allocation and add an Aging column.

Pre-MI1-I94 behaviour was:
  * First booking on a container/lot → full row.
  * Subsequent bookings → sparse sub-rows (blank Balance / Item /
    Container / Lifting Terms), with a PROGRESSIVE Available Qty
    (running sum minus each booking).
  * `Total Booked` column combined every SO on the group.

Raj's spec (verbatim asks pinned below):
  * Each SO on the same container/lot must be a separate row.
  * Balance Qty repeats on every SO row (physical stock is shared).
  * `Booked Qty` (Buyer Qty field) = this SO's booking.
  * `Total Booked` (Booked Qty field) = this SO's booking too.
  * `Available Qty` = Balance − this SO's booking (NOT progressive
    across bookings).
  * Buyer / Sales Person / Lifting Terms from this SO.
  * New `Aging` column = today − batch_date, in days, on detail rows.
"""
import inspect

import frappe
from frappe.tests.utils import FrappeTestCase


REPORT_MODULE = "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"


def _mod():
    return frappe.get_module(REPORT_MODULE)


class TestAgingColumnAdded(FrappeTestCase):

    def test_aging_in_get_columns(self):
        cols = _mod().get_columns({})
        agings = [c for c in cols if c.get("fieldname") == "Aging"]
        self.assertEqual(
            len(agings), 1,
            "MI1-I94: exactly one Aging column must exist in get_columns.",
        )
        self.assertEqual(
            agings[0]["fieldtype"], "Int",
            "Aging must be an Int (days).",
        )

    def test_aging_present_regardless_of_transaction_type(self):
        """Aging isn't tx-type-scoped — must appear in HTY too."""
        cols_hty = _mod().get_columns({"transaction_type": "HTY"})
        cols_vfy = _mod().get_columns({"transaction_type": "VFY"})
        for cols, label in ((cols_hty, "HTY"), (cols_vfy, "VFY")):
            fields = [c.get("fieldname") for c in cols]
            self.assertIn(
                "Aging", fields,
                f"Aging column must be in {label}-scoped columns.",
            )


class TestPerSoRowExplosion(FrappeTestCase):
    """Source-level pin — the old sub-booking pattern is gone and
    the new per-SO expansion writes a full row per booking."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = inspect.getsource(_mod().get_data)

    def test_no_progressive_available_pattern(self):
        """The old code did `running_available -= flt(bk['booked_qty'])`
        for subsequent booking rows. That pattern must be gone —
        Available on each SO row is now independent."""
        self.assertNotIn(
            "running_available -= flt",
            self.src,
            "Progressive Available-Qty accumulator must be removed. "
            "Each SO row now shows Balance − its own booking, not the "
            "running remainder.",
        )

    def test_no_sub_booking_sort_order_neg_1(self):
        """The old code appended sparse rows with sort_order=-1. New
        code appends full rows keeping sort_order=0."""
        self.assertNotIn(
            '"sort_order": -1',
            self.src,
            "sort_order=-1 sub-booking rows must be gone — every SO "
            "row is a full detail row (sort_order=0).",
        )

    def test_per_so_row_writes_full_columns(self):
        """The new expansion must copy `base` into `so_row` so every
        SO row carries Balance / Item / Container / Aging etc."""
        self.assertIn(
            "so_row = dict(base)",
            self.src,
            "MI1-I94: each SO row must be `dict(base)` so it inherits "
            "Balance / Item / Container / Aging / all identity columns "
            "from the group's base dict.",
        )

    def test_total_booked_matches_this_so_only(self):
        """`Booked Qty` field (labelled 'Total Booked' in the UI) must
        be set to THIS SO's booking, not the group's summed booked."""
        self.assertIn(
            'so_row["Booked Qty"] = round(bk_qty, 2)',
            self.src,
            "MI1-I94: `Booked Qty` field (== UI 'Total Booked') must "
            "be this SO's booking only, not the group total.",
        )

    def test_available_qty_uses_balance_minus_this_so(self):
        self.assertIn(
            'so_row["Available Qty"] = round(balance - bk_qty, 2)',
            self.src,
            "MI1-I94: Available Qty per SO row must be Balance minus "
            "THIS SO's booking (not progressive across bookings).",
        )

    def test_lifting_terms_flows_from_booking(self):
        self.assertIn(
            'so_row["Lifting Terms"] = bk.get("lifting_terms", "")',
            self.src,
            "MI1-I94: Lifting Terms must be filled from the booking "
            "dict on every SO row (was blank on the first row + "
            "sparse on subsequent ones).",
        )


class TestReportExecutesAndAgingFires(FrappeTestCase):
    """End-to-end smoke: pick a real container from the live DB, run
    the report, and verify at least one detail row has an Aging value
    (>= 0) plus Sales Order rows carry the new column set."""

    def test_execute_returns_rows_with_aging(self):
        cn = frappe.db.get_value(
            "Batch",
            filters={"custom_container_no": ["!=", ""]},
            fieldname="custom_container_no",
        )
        if not cn:
            self.skipTest("No batches with a container_no on this bench.")

        cols, data = _mod().execute({"container": cn})
        # Aging must be in columns
        fieldnames = {c.get("fieldname") for c in cols}
        self.assertIn("Aging", fieldnames)

        # At least one detail row (sort_order=0) must have Aging set.
        detail_rows = [r for r in data if r.get("sort_order") == 0]
        self.assertTrue(
            detail_rows,
            f"Container {cn!r} produced no detail rows — cannot pin Aging.",
        )
        with_aging = [r for r in detail_rows if isinstance(r.get("Aging"), int)]
        self.assertTrue(
            with_aging,
            "At least one detail row must have Aging populated as an int "
            "(today − batch_date, in days).",
        )
        # Aging must be non-negative (today − a past date).
        for r in with_aging:
            self.assertGreaterEqual(
                r["Aging"], 0,
                f"Aging must be >= 0. Got {r['Aging']!r}.",
            )

    def test_total_rows_leave_aging_blank(self):
        cn = frappe.db.get_value(
            "Batch",
            filters={"custom_container_no": ["!=", ""]},
            fieldname="custom_container_no",
        )
        if not cn:
            self.skipTest("No batches on this bench.")

        _cols, data = _mod().execute({"container": cn})
        for r in data:
            if r.get("sort_order") in (1, 2, 3):
                # None, not "" — the column is an Int and frappe's Int
                # formatter only renders blank for null (cint("") is 0).
                self.assertIsNone(
                    r.get("Aging"),
                    f"Total rows (sort_order={r.get('sort_order')}) must "
                    f"leave Aging blank; got {r.get('Aging')!r}.",
                )
