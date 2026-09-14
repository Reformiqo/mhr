"""MI1-I141 (prod TEST-3333, 2026-09-14, on a v16 site) —
MySQLdb.OperationalError: (4078, "Illegal parameter data types varchar and
row for operation '='"), raised from mhr.note.fetch_batches / _scan while
fetching with grade="Grade-AA UNEVEN".

Root cause: MI1-I140's fix moved _scan()'s query from frappe.get_all to a
hand-rolled frappe.db.sql WHERE clause, but its first version did a blind
`` `field` = %(field)s `` for every entry in `filters` — correct for a
plain scalar, but wrong for `mhr.utilis.grade_filter_value` (MI1-I107,
"matches both storage forms"), which returns frappe's own
`["in", [variant1, variant2]]` filter-value convention whenever a Batch's
grade could be stored either as the plain HTY form or the prefixed VFY
Item Specification docname -- exactly frappe.get_all's own filters=
understood, and exactly what the hand-rolled loop did not. Comparing a
varchar column to that whole two-element list with a plain `=` sent MySQL
a value shaped like a row constructor: "Illegal parameter data types
varchar and row for operation '='". Any Delivery Note / Stock Entry header
whose Grade needed the two-form IN clause hit this on every Fetch Batches
/ Count -- confirmed live: grade_filter_value("Grade-AA UNEVEN") returns
["in", ["Grade-AA UNEVEN", "AA UNEVEN"]] on this exact bench.

Fixed by mhr.note._filter_condition, which handles frappe's own
[operator, value] filter-value convention generally (in / not in / like /
comparison operators), not just this one caller -- so a future filters
entry using the same convention doesn't repeat the bug.
"""
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import note
from mhr.utilis import grade_filter_value

ITEM = "MI1I141-TEST-ITEM"
CONTAINER_NO = "MI1I141-TEST"
LOT_NO = "L1"


class TestFilterConditionHandlesFrappeFilterConventions(FrappeTestCase):
    """Fast, isolated checks of the WHERE-clause builder itself."""

    def test_plain_scalar_is_a_plain_equality(self):
        cond, params = note._filter_condition("custom_glue", "Glue-LOW")
        self.assertEqual(cond, "`custom_glue` = %(custom_glue)s")
        self.assertEqual(params, {"custom_glue": "Glue-LOW"})

    def test_in_list_becomes_an_in_clause_not_a_row_comparison(self):
        cond, params = note._filter_condition(
            "custom_grade", ["in", ["Grade-AA UNEVEN", "AA UNEVEN"]]
        )
        self.assertIn("IN (", cond)
        self.assertNotIn("=", cond)
        self.assertEqual(len(params), 2)
        self.assertEqual(set(params.values()), {"Grade-AA UNEVEN", "AA UNEVEN"})

    def test_in_clause_placeholders_match_the_params_dict(self):
        import re
        cond, params = note._filter_condition("custom_grade", ["in", ["A", "B", "C"]])
        placeholders = re.findall(r"%\((\w+)\)s", cond)
        self.assertEqual(set(placeholders), set(params.keys()))
        self.assertEqual(len(placeholders), 3)

    def test_not_in_uses_not_in(self):
        cond, params = note._filter_condition("custom_grade", ["not in", ["X", "Y"]])
        self.assertIn("NOT IN (", cond)

    def test_a_single_value_in_list_still_produces_a_valid_in_clause(self):
        """grade_filter_value collapses to a plain string when both forms
        are identical -- but if some OTHER caller ever passed a one-item
        in-list, it must not degrade into the broken `= [x]` shape either."""
        cond, params = note._filter_condition("custom_grade", ["in", ["Solo"]])
        self.assertIn("IN (", cond)
        self.assertEqual(list(params.values()), ["Solo"])

    def test_comparison_operator_convention_still_works(self):
        cond, params = note._filter_condition("custom_cone", [">", 0])
        self.assertEqual(cond, "`custom_cone` > %(custom_cone)s")
        self.assertEqual(params, {"custom_cone": 0})


class TestFetchBatchesWithAnInFormGradeOnRealData(FrappeTestCase):
    """End to end: the exact request shape from the bug report (grade
    needing the two-form IN clause) must not raise."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.db.sql("DELETE FROM `tabBatch` WHERE custom_container_no=%s", (CONTAINER_NO,))
        frappe.db.commit()
        if not frappe.db.exists("Item Specification", "Grade-AA UNEVEN"):
            frappe.get_doc({
                "doctype": "Item Specification",
                "specification_type": "Grade",
                "specification_value": "AA UNEVEN",
            }).insert(ignore_permissions=True)
        if not frappe.db.exists("Item", ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Products",
                "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 0,
            }).insert(ignore_permissions=True)
        b = frappe.new_doc("Batch")
        b.batch_id = "MI1I141-A"
        b.item = ITEM
        b.custom_container_no = CONTAINER_NO
        b.custom_lot_no = LOT_NO
        b.custom_cone = 6
        b.custom_grade = "Grade-AA UNEVEN"  # the VFY (prefixed) storage form
        b.custom_supplier_batch_no = "1"
        b.custom_transaction_type = "VFY"
        b.batch_qty = 25.0
        b.flags.ignore_mandatory = True
        b.insert(ignore_permissions=True)
        cls.batch = b.name

        # A second batch stored in the OTHER form (bare HTY value, no
        # "Grade-" docname prefix) -- proves the IN clause built from a
        # single request grade actually covers both storage forms at once,
        # not just an accidental exact-match on whichever form was typed.
        b2 = frappe.new_doc("Batch")
        b2.batch_id = "MI1I141-B"
        b2.item = ITEM
        b2.custom_container_no = CONTAINER_NO
        b2.custom_lot_no = LOT_NO
        b2.custom_cone = 6
        b2.custom_grade = "AA UNEVEN"  # the bare HTY storage form
        b2.custom_supplier_batch_no = "2"
        b2.custom_transaction_type = "HTY"
        b2.batch_qty = 25.0
        b2.flags.ignore_mandatory = True
        b2.insert(ignore_permissions=True)
        cls.batch_hty_form = b2.name
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.db.sql("DELETE FROM `tabBatch` WHERE custom_container_no=%s", (CONTAINER_NO,))
        frappe.db.commit()
        super().tearDownClass()

    def test_grade_filter_value_returns_the_in_shape_for_this_grade(self):
        """Pins the precondition the whole bug rests on."""
        self.assertEqual(
            grade_filter_value("Grade-AA UNEVEN"), ["in", ["Grade-AA UNEVEN", "AA UNEVEN"]]
        )

    def test_fetch_batches_does_not_raise_with_the_reported_request_shape(self):
        """The exact request payload from the bug report."""
        from unittest.mock import patch
        with patch.object(note, "_clamp_batch_qty_to_available", lambda *a, **k: None):
            out = note.fetch_batches(
                limit=5,
                lot_no=LOT_NO,
                container_no=CONTAINER_NO,
                glue=None,
                pulp=None,
                fsc=None,
                lusture=None,
                grade="Grade-AA UNEVEN",
                cone=6,
                denier=None,
            )  # must not raise MySQLdb.OperationalError 4078
        names = {row["name"] for row in out}
        self.assertIn(self.batch, names)

    def test_grade_filter_in_clause_matches_both_stored_forms_in_one_request(self):
        """A single request grade ("Grade-AA UNEVEN", exactly the bug
        report's payload) must find batches stored EITHER way -- the VFY
        docname form and the bare HTY form -- proving the IN clause, not
        just an accidental exact-match on whichever form was typed."""
        from unittest.mock import patch
        with patch.object(note, "_clamp_batch_qty_to_available", lambda *a, **k: None):
            out = note.fetch_batches(
                limit=5, lot_no=LOT_NO, container_no=CONTAINER_NO,
                grade="Grade-AA UNEVEN", cone=6,
            )
        names = {row["name"] for row in out}
        self.assertEqual(names, {self.batch, self.batch_hty_form})
