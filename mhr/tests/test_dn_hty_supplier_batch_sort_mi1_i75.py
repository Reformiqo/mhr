"""MI1-I75 (Raj 2026-06-29) — after an HTY popup fetches items into
the Delivery Note Item table, they must be sorted ascending by
custom_supplier_batch_no.

Two HTY popups add rows:
  * MI1-I39 'Pick Containers by Lot' (mi1_i39_proceed) — HTY-only
  * 'HTY & VFY' batch-picker dialog (HTY branch) — must gate on
    transaction_type='HTY'.

Both must sort with numeric-aware localeCompare so 4486/4487/.../7004
orders numerically, not lexicographically (which would place '10' before
'9').
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _fixture_script(name):
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == name:
            return cs.get("script", "")
    raise AssertionError(f"Client Script {name!r} not in fixtures.")


class TestMi1I39PickerSortsItems(FrappeTestCase):
    """The Pick-by-Lot flow (mi1_i39_proceed) must sort AFTER the
    forEach add_child block, using numeric localeCompare."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _fixture_script("MI1-I39 — Delivery Note HTY Mode")

    def test_marker_present(self):
        self.assertIn("MI1-I75", self.src,
            "MI1-I39 script must carry the MI1-I75 sort block.")

    def test_sort_uses_supplier_batch_no(self):
        self.assertIn(
            "String(a.custom_supplier_batch_no || '').localeCompare(",
            self.src,
            "Sort key must be a.custom_supplier_batch_no vs "
            "b.custom_supplier_batch_no.",
        )
        self.assertIn(
            "String(b.custom_supplier_batch_no || ''),",
            self.src,
        )

    def test_sort_is_numeric_aware(self):
        """{numeric:true} matters — without it '10' < '9' lexicographically."""
        self.assertIn("numeric: true", self.src,
            "localeCompare must pass { numeric: true } — 4486/4487/4488/"
            "4489/7004 requires numeric collation.")

    def test_reindexes_rows(self):
        """After sort, idx must be reassigned so the row-order shown to
        the user matches the sorted array."""
        self.assertIn("row.idx = i + 1", self.src,
            "After sort, rows must be re-numbered by their new position.")

    def test_sort_precedes_refresh(self):
        """The refresh_field call must happen AFTER the sort, otherwise
        the user briefly sees the unsorted state."""
        i_sort = self.src.find("MI1-I75")
        i_refresh = self.src.find("frm.refresh_field('items');", i_sort)
        self.assertGreater(i_refresh, i_sort,
            "refresh_field('items') must sit after the MI1-I75 sort "
            "block in mi1_i39_proceed.")

    def test_hide_dialog_still_wired(self):
        """The dialog's d.hide() must survive the patch — otherwise the
        popup lingers after Proceed."""
        self.assertIn("d.hide();", self.src,
            "Picker dialog must still hide after Proceed.")


class TestHtyVfyPickerSortsItems(FrappeTestCase):
    """The batch-picker dialog in 'HTY & VFY' must sort for HTY only —
    VFY workflows continue to insert-order the items."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _fixture_script("HTY & VFY")

    def test_marker_present(self):
        self.assertIn("MI1-I75", self.src,
            "HTY & VFY script must carry the MI1-I75 sort block.")

    def test_gated_on_hty_transaction_type(self):
        """VFY-mode users must NOT get the reorder — pin the HTY gate."""
        self.assertIn(
            "if (String(frm.doc.transaction_type || '').toUpperCase() === 'HTY')",
            self.src,
            "HTY & VFY sort must be gated on transaction_type = 'HTY'.",
        )

    def test_sort_is_numeric_aware(self):
        self.assertIn("numeric: true", self.src,
            "HTY & VFY sort must be numeric-aware.")

    def test_sort_uses_supplier_batch_no(self):
        self.assertIn(
            "String(a.custom_supplier_batch_no || '').localeCompare(",
            self.src,
        )

    def test_reindexes_after_sort(self):
        self.assertIn("row.idx = k + 1", self.src,
            "HTY & VFY sort must renumber idx (loop-var k in this "
            "script — different from MI1-I39's `i` to avoid shadowing "
            "the outer batch iterator).")

    def test_prior_add_child_survived(self):
        """Regression: the add_child('items', {...}) that puts rows into
        the table must still be there — pin its shape."""
        self.assertIn('frm.add_child("items", {', self.src)
        self.assertIn("added_count++;", self.src,
            "The added_count counter must survive.")

    def test_prior_refresh_field_survived(self):
        self.assertIn('frm.refresh_field("items");', self.src)


class TestMi1I72PriorFixesStillPresent(FrappeTestCase):
    """MI1-I75 layers on top of MI1-I72 + MI1-I72 P2. Pin that the two
    prior fixes still hold in the same fixtures file."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mi1_i39 = _fixture_script("MI1-I39 — Delivery Note HTY Mode")
        cls.hty_vfy = _fixture_script("HTY & VFY")

    def test_mi1_i72_lusture_glue_pulp_still_hidden_in_hty(self):
        """MI1-I72 — the three VFY-only fields must still be in
        hide_in_hty."""
        for fn in ("custom_lusture", "custom_glue", "custom_pulp"):
            self.assertIn(f"'{fn}'", self.mi1_i39,
                f"MI1-I72 regression — {fn} lost from hide_in_hty.")

    def test_mi1_i72_p2_no_product_from_glue_copy(self):
        """MI1-I72 P2 — the erroneous copies must still be gone."""
        self.assertNotIn(
            "frm.set_value('custom_product', last_batch.custom_glue",
            self.hty_vfy,
            "MI1-I72 P2 regression — Product copy from Glue is back.",
        )

    def test_hty_vfy_still_in_mhr_module(self):
        path = os.path.join(
            frappe.get_app_path("mhr"), "fixtures", "client_script.json"
        )
        with open(path) as fh:
            data = json.load(fh)
        entry = next((cs for cs in data if cs.get("name") == "HTY & VFY"), None)
        self.assertIsNotNone(entry,
            "HTY & VFY must remain in fixtures (module=Mhr).")
        self.assertEqual(entry.get("module"), "Mhr")
