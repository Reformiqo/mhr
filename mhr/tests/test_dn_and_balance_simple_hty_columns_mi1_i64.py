"""MI1-I64 reopen (Raj 2026-06-29) — extend the Merge No / Cross Section
HTY hide rule to the two remaining reports that carry the VFY-only
columns:
  * DN (mhr.mhr.report.dn.dn) — has Merge No (no Cross Section)
  * Stock Sheet (Balance Report Simple) — has both

Stock Sheet (Balance Report) is covered by
test_stock_sheet_balance_hty_columns_mi1_i64.py.
"""
import frappe
from frappe.tests.utils import FrappeTestCase


class TestDnHtyDropsMergeNo(FrappeTestCase):
    """DN report never had a Cross Section column — only Merge No is
    at stake here."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from mhr.mhr.report.dn import dn as mod
        cls.mod = mod

    def _labels(self, transaction_type):
        cols = self.mod.get_columns({"transaction_type": transaction_type})
        return [c["label"] for c in cols]

    def test_hty_drops_merge_no(self):
        self.assertNotIn("Merge No", self._labels("HTY"),
            "DN HTY view must NOT include Merge No — Raj's 2026-06-29 ask.")

    def test_vfy_keeps_merge_no(self):
        self.assertIn("Merge No", self._labels("VFY"),
            "DN VFY view must KEEP Merge No — HTY-only removal.")

    def test_blank_filter_matches_vfy(self):
        self.assertIn("Merge No", self._labels(""),
            "DN with blank transaction_type defaults to VFY-style layout.")

    def test_hty_still_swaps_pulp_glue_lusture(self):
        """Regression pin: the earlier MI1-I64 rework must still swap
        Pulp/Glue/Lusture -> Type/Product/Colour on DN."""
        labels = self._labels("HTY")
        self.assertIn("Type", labels)
        self.assertIn("Product", labels)
        self.assertIn("Colour", labels)
        self.assertNotIn("Pulp", labels)
        self.assertNotIn("Glue", labels)
        self.assertNotIn("Lusture", labels)

    def test_merge_no_fieldname_stable(self):
        """The rendered HTY grid won't show Merge No, but get_data
        still writes a merge_no key — don't rename it."""
        cols = self.mod.get_columns({"transaction_type": "VFY"})
        fields = {c["fieldname"] for c in cols}
        self.assertIn("merge_no", fields)


class TestBalanceSimpleHtyDropsMergeAndCross(FrappeTestCase):
    """Balance Report Simple has both Merge No + Cross Section — mirror
    the Balance Report behaviour."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mod = frappe.get_module(
            "mhr.mhr.report.stock_sheet_(balance_report_simple).stock_sheet_(balance_report_simple)"
        )

    def _labels(self, transaction_type):
        cols = self.mod.get_columns({"transaction_type": transaction_type})
        return [c["label"] for c in cols]

    def test_hty_drops_merge_no(self):
        self.assertNotIn("Merge No", self._labels("HTY"))

    def test_hty_drops_cross_section(self):
        self.assertNotIn("Cross Section", self._labels("HTY"))

    def test_vfy_keeps_both(self):
        labels = self._labels("VFY")
        self.assertIn("Merge No", labels)
        self.assertIn("Cross Section", labels)

    def test_blank_filter_matches_vfy(self):
        labels = self._labels("")
        self.assertIn("Merge No", labels)
        self.assertIn("Cross Section", labels)

    def test_hty_swaps_pulp_glue_labels(self):
        """Balance Simple picks up the same label swap the other
        reports already have — Pulp -> Type, Glue -> Product."""
        labels = self._labels("HTY")
        self.assertIn("Type", labels)
        self.assertIn("Product", labels)
        self.assertNotIn("Pulp", labels)
        self.assertNotIn("Glue", labels)

    def test_pulp_glue_fieldnames_unchanged(self):
        """Underlying fieldnames must stay 'Pulp' + 'Glue' — get_data
        writes to those keys."""
        cols = self.mod.get_columns({"transaction_type": "HTY"})
        fields = {c["fieldname"] for c in cols}
        self.assertIn("Pulp", fields)
        self.assertIn("Glue", fields)

    def test_execute_still_accepts_no_filters(self):
        """execute(None) must not crash — matches how the desk fires
        the report before the user picks a Transaction Type."""
        try:
            self.mod.execute(None)
        except frappe.PermissionError:
            pass


class TestBalanceSimpleColumnOrderStable(FrappeTestCase):
    """When Merge No + Cross Section vanish in HTY, the surrounding
    columns must line up correctly — pins against future off-by-one
    slip-ups in get_columns."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mod = frappe.get_module(
            "mhr.mhr.report.stock_sheet_(balance_report_simple).stock_sheet_(balance_report_simple)"
        )

    def test_hty_cone_precedes_type(self):
        labels = [c["label"] for c in self.mod.get_columns({"transaction_type": "HTY"})]
        i_cone = labels.index("Cone")
        i_type = labels.index("Type")
        self.assertEqual(i_type, i_cone + 1,
            "HTY: Type (Pulp column) must come right after Cone "
            "(Merge No was between them in VFY).")

    def test_hty_balance_box_precedes_production_date(self):
        labels = [c["label"] for c in self.mod.get_columns({"transaction_type": "HTY"})]
        i_box = labels.index("Balance Box")
        i_prod = labels.index("Production Date")
        self.assertEqual(i_prod, i_box + 1,
            "HTY: Production Date must come right after Balance Box "
            "(Cross Section was between them in VFY).")

    def test_vfy_cone_merge_pulp_order(self):
        labels = [c["label"] for c in self.mod.get_columns({"transaction_type": "VFY"})]
        i_cone = labels.index("Cone")
        i_merge = labels.index("Merge No")
        i_pulp = labels.index("Pulp")
        self.assertEqual(i_merge, i_cone + 1)
        self.assertEqual(i_pulp, i_merge + 1)
