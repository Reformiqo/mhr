"""MI1-I64 reopen (Raj 2026-06-29) — Stock Sheet (Balance Report):
hide Merge No + Cross Section columns when transaction_type is HTY.

The Pulp/Glue → Type/Product label swap already landed. Reopen asks
that Merge No + Cross Section be dropped entirely from the HTY view
(grid + Excel export + print), since they're VFY-only fields.

Same pattern as the earlier MI1-I64 rework: get_columns(filters)
returns different column lists based on filters.transaction_type.
"""
import frappe
from frappe.tests.utils import FrappeTestCase


def _get_columns(filters):
    from mhr.mhr.report.stock_sheet_balance_report.stock_sheet_balance_report import get_columns
    return get_columns(filters)


class TestHtyDropsMergeNoAndCrossSection(FrappeTestCase):
    """Load the report's get_columns via the actual path — the folder
    name has parentheses so import via frappe.get_module."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mod = frappe.get_module(
            "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"
        )

    def _labels(self, transaction_type):
        cols = self.mod.get_columns({"transaction_type": transaction_type})
        return [c["label"] for c in cols]

    def test_hty_drops_merge_no(self):
        labels = self._labels("HTY")
        self.assertNotIn("Merge No", labels,
            "HTY view must NOT include Merge No — Raj's 2026-06-29 ask.")

    def test_hty_drops_cross_section(self):
        labels = self._labels("HTY")
        self.assertNotIn("Cross Section", labels,
            "HTY view must NOT include Cross Section — Raj's 2026-06-29 ask.")

    def test_hty_swaps_pulp_glue(self):
        """Regression pin: the label swap from the earlier MI1-I64
        rework must still fire."""
        labels = self._labels("HTY")
        self.assertIn("Type", labels)
        self.assertIn("Product", labels)
        self.assertNotIn("Pulp", labels)
        self.assertNotIn("Glue", labels)

    def test_vfy_keeps_merge_no_and_cross_section(self):
        labels = self._labels("VFY")
        self.assertIn("Merge No", labels,
            "VFY view must KEEP Merge No — HTY-only removal.")
        self.assertIn("Cross Section", labels,
            "VFY view must KEEP Cross Section — HTY-only removal.")

    def test_blank_filter_matches_vfy(self):
        """Blank / no filter defaults to VFY-style layout (Merge No +
        Cross Section shown)."""
        labels = self._labels("")
        self.assertIn("Merge No", labels)
        self.assertIn("Cross Section", labels)

    def test_hty_column_order_stable(self):
        """After the two removals, the surrounding order must not shift
        columns into wrong positions — Cone still precedes Type
        (Pulp fieldname), Lifting Terms still precedes Production Date."""
        cols = self.mod.get_columns({"transaction_type": "HTY"})
        by_label = [c["label"] for c in cols]
        # Cone right before Type (was Cone -> Merge No -> Pulp; now Cone -> Type)
        i_cone = by_label.index("Cone")
        i_type = by_label.index("Type")
        self.assertEqual(i_type, i_cone + 1,
            "In HTY, Type (Pulp column) must come immediately after Cone "
            "(Merge No was between them in VFY).")
        # Lifting Terms right before Production Date (Cross Section
        # was between them in VFY; now gone).
        i_lifting = by_label.index("Lifting Terms")
        i_prod = by_label.index("Production Date")
        self.assertEqual(i_prod, i_lifting + 1,
            "In HTY, Production Date must come immediately after "
            "Lifting Terms (Cross Section was between them in VFY).")


class TestFieldnamesStable(FrappeTestCase):
    """Even though Merge No + Cross Section vanish from the column
    list, the underlying data dicts still populate their fieldnames.
    That's fine — the rendered table just won't show those columns.
    Pin that we haven't renamed the fieldnames (which would break the
    row-population code in get_data)."""

    def test_pulp_glue_fieldnames_unchanged(self):
        mod = frappe.get_module(
            "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"
        )
        cols = mod.get_columns({"transaction_type": "HTY"})
        fieldnames = {c["fieldname"] for c in cols}
        self.assertIn("Pulp", fieldnames,
            "Pulp fieldname must remain — get_data writes to row['Pulp'].")
        self.assertIn("Glue", fieldnames,
            "Glue fieldname must remain — get_data writes to row['Glue'].")
