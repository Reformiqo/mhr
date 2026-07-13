"""MI1-I78 (Raj 2026-07-13): the VFY 'Select Batch' popup was rendering
one <tr> per Batch. For container MILA-01 that meant 111 rows across
5 lots, and 46 of those were duplicate-looking (lot 71006857, cone 12)
— the other lots got buried in the scroll and Raj read that as
'not all lots are being displayed'.

Fix: dedupe the batches array in the VFY branch of
show_hty_batch_dialog's sibling `async custom_container_no(frm)`
handler by (custom_lot_no, custom_cone) before rendering the rows.
Each unique lot+cone combo shows exactly once so every lot is
visible at a glance.

The primary_action still resolves via frappe.db.get_doc('Batch', name)
using the FIRST batch.name kept for each combo — any batch matching
the same lot+cone would populate the same header fields anyway.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _hty_vfy_script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "HTY & VFY":
            return cs.get("script", "")
    raise AssertionError("'HTY & VFY' Client Script missing from fixtures.")


class TestVfyPopupDedupes(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_marker_present(self):
        self.assertIn(
            "MI1-I78", self.src,
            "VFY popup must carry the MI1-I78 marker.",
        )

    def test_dedupe_uses_lot_and_cone_composite_key(self):
        """Pin the composite key. If someone reverts to lot-only, users
        would lose the cone distinction."""
        self.assertIn(
            "(b.custom_lot_no || '') + '|' + (b.custom_cone || '')",
            self.src,
            "Dedupe key must be (custom_lot_no, custom_cone) so a lot "
            "with two distinct cone counts shows two rows, not one.",
        )

    def test_dedupe_uses_a_set_for_o1_lookup(self):
        self.assertIn(
            "new Set()", self.src,
            "Dedupe must use a Set — a linear .includes() over 111 batches "
            "would slow the popup.",
        )

    def test_rows_html_renders_from_deduped_list(self):
        self.assertIn(
            "unique_batches.map(batch =>",
            self.src,
            "rows_html must map over the deduped `unique_batches` array, "
            "not the raw `batches` array.",
        )

    def test_original_batches_variable_untouched_by_dedupe(self):
        """Regression pin: the VFY primary_action's
        frappe.db.get_doc('Batch', selected_batch_name) still needs the
        batch names, so the dedupe must NOT mutate the source `batches`
        array — .filter returns a new array."""
        self.assertIn(
            "batches.filter(b =>",
            self.src,
            "Dedupe must use .filter (non-mutating) not .splice / in-place.",
        )


class TestHtyPathNotAffected(FrappeTestCase):
    """MI1-I78 only touches the VFY branch. The HTY show_hty_batch_dialog
    already shows one row per Batch by design (multi-select checkboxes
    per batch) — pin it isn't accidentally deduping too."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_hty_dialog_maps_batches_directly(self):
        """show_hty_batch_dialog should still map the full batches array
        (not a deduped variant)."""
        self.assertIn(
            "batches.map(batch => `",
            self.src,
            "show_hty_batch_dialog must still map raw batches — HTY needs "
            "each batch as a selectable row.",
        )

    def test_seen_vfy_variable_is_scoped_to_vfy_only(self):
        """The seen_vfy name is deliberate — pin that we did not
        introduce a similar `seen_hty` (which would break the HTY
        multi-select workflow)."""
        self.assertNotIn(
            "seen_hty", self.src,
            "MI1-I78 dedupe must not spread to the HTY branch.",
        )


class TestVfyPopupOriginalBehaviourPreserved(FrappeTestCase):
    """Everything else in the VFY branch must survive."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_radio_input_still_batch_select_vfy(self):
        self.assertIn(
            'name="batch_select_vfy"',
            self.src,
            "VFY radio group name must stay batch_select_vfy — the "
            "primary_action selector depends on it.",
        )

    def test_primary_action_still_reads_get_doc_batch(self):
        self.assertIn(
            "frappe.db.get_doc('Batch', selected_batch_name)",
            self.src,
            "VFY primary_action must still resolve the selected batch "
            "via frappe.db.get_doc('Batch', ...) — that's how header "
            "fields get populated.",
        )

    def test_header_fields_populated_on_select(self):
        for fn in ("custom_glue", "custom_pulp", "custom_lusture",
                   "custom_grade", "custom_lot_no", "custom_fsc",
                   "custom_cone", "custom_denier"):
            self.assertIn(
                f"frm.set_value('{fn}',",
                self.src,
                f"VFY primary_action must still set {fn} — Raj's popup "
                "populates the DN header from the selected batch.",
            )
