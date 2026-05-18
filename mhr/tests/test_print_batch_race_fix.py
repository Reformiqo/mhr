"""Print Batch PDF — race-condition + missing-doc hardening.

Raj reported the Print Batch PDF flow "was working fine before HTY" but
fails sometimes now. Inspection on FC found:

  - File `apps/mhr/mhr/mhr/doctype/print_batch/print_batch.py` had
    `enqueue_generate_multi_pdf_url` calling `enqueue(...)` WITHOUT a
    `frappe.db.commit()` first. A fast worker picks up the job before
    the parent transaction is committed → `frappe.get_doc("Print Batch",
    X)` inside the worker raises `DoesNotExistError("Print Batch X not
    found")`. That's exactly what the May 14 Error Log on FC showed
    (Error Log b27iivka7n).

  - The background-job code didn't tolerate the missing-doc case — it
    let the exception propagate as an uncaught failure, leaving the
    Print Batch with `file_url=NULL` and no usable diagnostic for the
    user.

Fixes pinned by this test:

  1. `enqueue_generate_multi_pdf_url` calls `frappe.db.commit()` BEFORE
     `enqueue(...)`, so the doc is guaranteed to be visible to the
     worker.
  2. `enqueue(...)` passes `enqueue_after_commit=True` — second belt
     against the race (works even if a caller forgot to commit).
  3. `generate_multi_pdf_url` short-circuits with a `frappe.log_error`
     when the Print Batch doesn't exist — no uncaught DoesNotExistError,
     no scary stack-trace email, no infinite-retry storm.
"""
import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.doctype.print_batch import print_batch as pb_mod


class TestEnqueueCommitsBeforeEnqueue(FrappeTestCase):

    def test_commit_precedes_enqueue_call(self):
        src = inspect.getsource(pb_mod.PrintBatch.enqueue_generate_multi_pdf_url)
        commit_idx = src.find("frappe.db.commit()")
        enqueue_idx = src.find("enqueue(")
        self.assertGreater(commit_idx, 0,
            "enqueue_generate_multi_pdf_url must call frappe.db.commit() "
            "before enqueue() so the worker sees the just-inserted doc.")
        self.assertGreater(enqueue_idx, 0,
            "enqueue() call must be present.")
        self.assertLess(
            commit_idx, enqueue_idx,
            "frappe.db.commit() MUST precede enqueue() — otherwise the worker "
            "races the parent transaction and raises DoesNotExistError.",
        )

    def test_enqueue_after_commit_flag_passed(self):
        src = inspect.getsource(pb_mod.PrintBatch.enqueue_generate_multi_pdf_url)
        self.assertIn(
            "enqueue_after_commit=True", src,
            "enqueue(...) must pass enqueue_after_commit=True — defensive "
            "double-guard in case a caller adds another DB write between "
            "commit() and enqueue().",
        )


class TestMissingDocShortCircuits(FrappeTestCase):

    def test_returns_silently_when_doc_missing(self):
        # No raise — just logs and returns. We monkey-patch frappe.log_error
        # so the test doesn't actually create a real Error Log row.
        original_log = frappe.log_error
        captured = {}
        try:
            frappe.log_error = lambda **kw: captured.update(kw)
            pb_mod.PrintBatch.generate_multi_pdf_url("__nope_does_not_exist__")
        finally:
            frappe.log_error = original_log
        self.assertIn("title", captured,
            "Missing-doc path must call frappe.log_error with a title.")
        self.assertIn("__nope_does_not_exist__", captured.get("title", ""),
            "Logged title must name the missing Print Batch so it's "
            "searchable in the Error Log.")

    def test_missing_doc_check_uses_db_exists(self):
        src = inspect.getsource(pb_mod.PrintBatch.generate_multi_pdf_url)
        self.assertIn(
            'frappe.db.exists("Print Batch"', src,
            "Worker must guard with frappe.db.exists before frappe.get_doc — "
            "otherwise the race manifests as an uncaught DoesNotExistError.",
        )
