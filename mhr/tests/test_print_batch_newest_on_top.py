"""MI1-I62 (newest on top, 2026-06-23) — pin that fetched batches land
at the top of List Batches, not appended at the bottom.

Raj's feedback: after typing a Supplier Batch No (or a Cone), the
newly-fetched rows must appear above the existing ones in the table.
Persisted order = fetch order (most recent first).

Source-level pins on print_batch.js:
  - prepend_added_rows helper exists and renumbers idx
  - both fetch_and_append_batch and fetch_and_append_batch_by_cone
    call it before refreshing the field
  - the before_save alphabetical sort was removed (otherwise save
    would undo the top-pinning)
"""

import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_js():
    path = os.path.join(
        frappe.get_app_path("mhr"),
        "mhr", "doctype", "print_batch", "print_batch.js",
    )
    return open(path).read()


class TestPrependHelper(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_js()

    def test_helper_exists(self):
        self.assertRegex(
            self.js,
            r"function\s+prepend_added_rows\s*\(\s*frm\s*,\s*added_count\s*\)\s*\{",
            "prepend_added_rows helper must exist.",
        )

    def test_helper_moves_rows_to_front(self):
        m = re.search(
            r"function\s+prepend_added_rows\s*\(\s*frm\s*,\s*added_count\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, "Could not locate prepend_added_rows.")
        body = m.group(1)
        # Move-to-front: splice from the end, unshift to the start.
        self.assertIn("lst.length - added_count", body,
            "Helper must pull the newly-added rows from the END of "
            "list_batches.")
        self.assertIn("unshift", body,
            "Helper must unshift (prepend) the new rows to index 0.")

    def test_helper_renumbers_idx(self):
        m = re.search(
            r"function\s+prepend_added_rows\s*\(\s*frm\s*,\s*added_count\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertRegex(
            body, r"r\.idx\s*=\s*i\s*\+\s*1",
            "Helper must renumber every row's idx so the new order "
            "persists on save.",
        )


class TestFetchPathsCallPrepend(FrappeTestCase):
    """Both fetch helpers must call prepend_added_rows before refresh_field
    so the user sees newly-fetched rows at the top."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_js()

    def _fetch_body(self, fn_name):
        m = re.search(
            rf"function\s+{fn_name}\s*\(\s*frm\s*\)\s*\{{(.*?)\n\}}\n",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, f"Could not locate {fn_name}.")
        return m.group(1)

    def test_supplier_batch_path_prepends(self):
        body = self._fetch_body("fetch_and_append_batch")
        self.assertIn("prepend_added_rows(frm, added)", body,
            "fetch_and_append_batch must call prepend_added_rows after "
            "appending — otherwise new rows land at the bottom.")

    def test_cone_path_prepends(self):
        body = self._fetch_body("fetch_and_append_batch_by_cone")
        self.assertIn("prepend_added_rows(frm, added)", body,
            "fetch_and_append_batch_by_cone must also prepend so the "
            "Cone-fetch path is consistent with the Supplier-Batch path.")


class TestBeforeSaveSortRemoved(FrappeTestCase):
    """If we left the alphabetical before_save sort in place, it would
    undo the top-pinning on every save — defeating the whole point."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_js()

    def test_before_save_does_not_sort(self):
        # The 'before_save' key may or may not exist, but if it does it
        # must not call sort_list_batches.
        m = re.search(
            r"before_save:\s*function\s*\(\s*frm\s*\)\s*\{(.*?)\n\s*\}",
            self.js, re.DOTALL,
        )
        if m:
            self.assertNotIn("sort_list_batches", m.group(1),
                "before_save must NOT alphabetically sort list_batches — "
                "would undo the newest-on-top behaviour.")

    def test_sort_list_batches_call_site_removed(self):
        # No live call to sort_list_batches anywhere. (The function can
        # stay defined-but-unused, but we don't want any call site.)
        non_def_calls = re.findall(
            r"(?<!function\s)sort_list_batches\s*\(", self.js
        )
        # The function definition itself starts with `function sort_list_batches(`
        # which the negative lookbehind should already exclude — confirm
        # we found zero remaining call sites.
        # Re-check by scanning each match in context.
        for m in re.finditer(r"sort_list_batches\s*\(", self.js):
            preceding = self.js[max(0, m.start() - 20):m.start()]
            self.assertIn(
                "function ", preceding,
                "Live call to sort_list_batches remains — would re-sort "
                "list_batches on some event and undo top-pinning. "
                f"Context: ...{preceding}{m.group(0)}...",
            )
