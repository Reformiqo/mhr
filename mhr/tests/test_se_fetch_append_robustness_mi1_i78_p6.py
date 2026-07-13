"""MI1-I78 P6 (Raj 2026-07-13): the SE 'Select lot → enter SBN → row
appends' flow now:
  * Surfaces a clear msgprint when the SBN doesn't resolve on the
    server (was: silent early-return, user reads it as 'nothing happens').
  * Filters out the blank auto-row before add_child, so the SBN row
    appears at position 1 instead of position 2.
  * Drops the fragile pop/unshift dance in favor of plain add_child +
    refresh_field.
  * Carries custom_supplier_batch_no over onto the item row so the
    Supplier Batch No column in the items table matches what the
    header showed.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _se_container_info_script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "Stock Entry Container Info":
            return cs.get("script", "")
    raise AssertionError("SE script missing from fixtures.")


class TestP6Fix(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _se_container_info_script()

    def test_marker_present(self):
        self.assertIn("MI1-I78 P6", self.src)

    def test_no_batch_message_surfaced(self):
        """User must see a message when the SBN doesn't resolve — the
        pre-P6 code silently early-returned and the user read it as
        'nothing happens'."""
        self.assertIn(
            "No batch found for Supplier Batch No",
            self.src,
            "Must surface a user-visible message when the SBN doesn't "
            "match any batch.",
        )

    def test_data_batch_no_guard(self):
        """Must skip the whole append flow when data.batch_no is falsy."""
        self.assertIn(
            "if (!data || !data.batch_no)",
            self.src,
            "Must guard against a truthy data object with a falsy batch_no.",
        )

    def test_blank_rows_filtered_before_add_child(self):
        """The initial blank auto-row must be filtered out so the new
        batch row appears at position 1."""
        self.assertIn(
            "frm.doc.items = (frm.doc.items || []).filter(row => row.item_code);",
            self.src,
            "Must filter out item_code-less rows before add_child so the "
            "SBN-driven row lands at position 1.",
        )

    def test_pop_unshift_dance_removed_from_fetch_and_append(self):
        """The fragile pop/unshift move-to-front pattern must be gone
        from fetch_and_append_batch_se — the blank-row filter replaces
        it and it broke if add_child didn't append synchronously.
        (The scan-batch handler has its own separate pop/unshift that
        this test does NOT touch.)"""
        # Isolate the fetch_and_append_batch_se function body.
        start = self.src.find("function fetch_and_append_batch_se(frm)")
        self.assertGreater(start, -1, "fetch_and_append_batch_se must exist.")
        # Find the matching closing brace of that function.
        depth = 0
        started = False
        i = start
        end = start
        while i < len(self.src):
            ch = self.src[i]
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    end = i + 1
                    break
            i += 1
        body = self.src[start:end]
        self.assertNotIn(
            "frm.doc.items.unshift(frm.doc.items.pop());",
            body,
            "fetch_and_append_batch_se must not use the pop/unshift dance — "
            "the blank-row filter replaces it and it was fragile against "
            "timing.",
        )

    def test_supplier_batch_no_carried_onto_item_row(self):
        self.assertIn(
            "custom_supplier_batch_no: data.supplier_batch_no,",
            self.src,
            "The item row must carry data.supplier_batch_no so the SE's "
            "'Supplier Batch No' items column matches what the header "
            "showed.",
        )

    def test_clears_supplier_batch_no_on_success(self):
        """After successful append, the header's Supplier Batch No is
        cleared so the user can enter the next one."""
        self.assertIn(
            "frm.set_value('custom_supplier_batch_no', '');",
            self.src,
        )

    def test_calculate_totals_still_wired(self):
        self.assertIn("calculate_totals(frm);", self.src,
            "Post-append totals must still recompute.")
