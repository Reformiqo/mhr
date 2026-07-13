"""MI1-I78 P3 (Raj 2026-07-13): the SE / DN 'fetch batch by supplier
batch no' and 'scan batch no' handlers each guard against adding the
same batch twice by scanning items[] for a matching batch_no:

    let exists = frm.doc.items.some(row => row.batch_no === data.batch_no);

A brand-new Stock Entry (or DN) has one blank items row with
row.batch_no undefined. If the server helper responds with
batch_no=undefined (miss / no match) then `undefined === undefined`
is TRUE and the code falsely reports 'Batch already exists in the
list.' — Raj's screenshot: items empty, error fires.

Fix: guard both sides — require BOTH data.batch_no AND row.batch_no
to be truthy before the equality check.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _script(name):
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == name:
            return cs.get("script", "")
    raise AssertionError(f"Client Script {name!r} missing from fixtures.")


class TestStockEntryContainerInfoGuards(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script("Stock Entry Container Info")

    def test_supplier_batch_check_guards_both_sides(self):
        self.assertIn(
            "let exists = data.batch_no && (frm.doc.items || []).some(row => row.batch_no && row.batch_no === data.batch_no);",
            self.src,
            "The `exists` check in fetch_and_append_batch_se must guard "
            "on BOTH data.batch_no AND row.batch_no being truthy — the "
            "blank items row's undefined batch_no was falsely matching "
            "the server helper's undefined batch_no.",
        )

    def test_supplier_batch_check_bare_form_removed(self):
        self.assertNotIn(
            "let exists = frm.doc.items.some(row => row.batch_no === data.batch_no);",
            self.src,
            "The un-guarded exists check must be gone — it triggered "
            "false-positive 'Batch already exists in the list.' errors.",
        )

    def test_scan_batch_check_guards_both_sides(self):
        self.assertIn(
            "const existingRow = frm.doc.custom_scan_batch_no && (frm.doc.items || []).find(d => d.batch_no && d.batch_no === frm.doc.custom_scan_batch_no);",
            self.src,
            "custom_scan_batch_no handler must also guard on both sides "
            "being truthy — same false-positive risk.",
        )


class TestDeliveryNoteV2Guards(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script("Delivery Note V2")

    def test_scan_batch_check_guards_both_sides(self):
        self.assertIn(
            "const existingRow = frm.doc.custom_scan_batch_no && (frm.doc.items || []).find(d => d.batch_no && d.batch_no === frm.doc.custom_scan_batch_no);",
            self.src,
            "DN V2's custom_scan_batch_no handler must guard both sides.",
        )

    def test_supplier_batch_check_guards_data_side(self):
        self.assertIn(
            "var exists = data.batch_no && (frm.doc.items || []).some(function(row) {",
            self.src,
            "DN V2's fetch_and_append_batch must guard the exists check "
            "on data.batch_no being truthy at minimum.",
        )
