"""MI1-I62 (reverted 2026-06-23) — Print Batch form must NOT auto-populate
List Batches when the user picks just Container + Lot. Raj's feedback: the
user expects to type a Supplier Batch No to drive each fetch.

Source-level pins on print_batch.js — behavioural cypress-style verification
is out of scope here (no JS test runner). The pins below would fail if any
future commit re-introduces the auto-fetch on lot_no change, or relaxes
the supplier_batch_no guard inside fetch_and_append_batch.
"""

import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_print_batch_js():
    path = os.path.join(
        frappe.get_app_path("mhr"),
        "mhr", "doctype", "print_batch", "print_batch.js",
    )
    return open(path).read()


class TestLotNoDoesNotFetch(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_print_batch_js()

    def test_lot_no_handler_exists(self):
        self.assertRegex(self.js, r"lot_no:\s*function\s*\(\s*frm\s*\)\s*\{",
            "Print Batch must still have a lot_no change handler "
            "(repopulates the Item select).")

    def test_lot_no_handler_does_not_call_fetch(self):
        """Extract the lot_no handler block and assert fetch_and_append_batch
        is NOT invoked from it."""
        m = re.search(
            r"lot_no:\s*function\s*\(\s*frm\s*\)\s*\{(.*?)\n\s*\},",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, "Could not extract the lot_no handler block.")
        body = m.group(1)
        self.assertNotIn(
            "fetch_and_append_batch(frm)", body,
            "lot_no handler must NOT call fetch_and_append_batch — Raj's "
            "feedback: selecting a Lot alone must not populate List Batches.",
        )


class TestFetchRequiresSupplierBatch(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_print_batch_js()

    def test_fetch_guards_empty_supplier_batch_no(self):
        """Extract fetch_and_append_batch body; assert it bails when
        supplier_batch_no is empty."""
        m = re.search(
            r"function\s+fetch_and_append_batch\s*\(\s*frm\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, "Could not locate fetch_and_append_batch.")
        body = m.group(1)
        self.assertRegex(
            body,
            r"if\s*\(\s*!\s*frm\.doc\.supplier_batch_no\s*\)\s*return",
            "fetch_and_append_batch must early-return when "
            "supplier_batch_no is empty/falsy.",
        )

    def test_fetch_still_requires_container_and_lot(self):
        """The existing Container + Lot guard must remain too."""
        m = re.search(
            r"function\s+fetch_and_append_batch\s*\(\s*frm\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertRegex(
            body,
            r"!\s*frm\.doc\.container_no\s*\|\|\s*!\s*frm\.doc\.lot_no",
            "fetch_and_append_batch must still require both container_no "
            "and lot_no before firing.",
        )


class TestSupplierBatchHandlerStillDrivesFetch(FrappeTestCase):
    """The user's only path to populating List Batches is now typing
    Supplier Batch No — so the change handler for that field must still
    call fetch_and_append_batch (after the existing debounce)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_print_batch_js()

    def test_supplier_handler_calls_fetch(self):
        m = re.search(
            r"supplier_batch_no:\s*function\s*\(\s*frm\s*\)\s*\{(.*?)\n\s*\},",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, "Could not locate supplier_batch_no handler.")
        body = m.group(1)
        self.assertIn(
            "fetch_and_append_batch(frm)", body,
            "supplier_batch_no handler must still call fetch_and_append_batch "
            "— it's now the ONLY entry point that populates List Batches.",
        )
