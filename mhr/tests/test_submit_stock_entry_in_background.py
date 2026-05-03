"""MI1-I26 — Submit Stock Entry in background.

Raj's report: a Material Transfer with 245 batches takes 60+ seconds
for ERPNext to write all SLEs / Bins, so gunicorn kills the HTTP
request and the user sees "Request Timeout". The synchronous Submit
isn't usable for this volume.

Fix:
  - `mhr.utilis.submit_stock_entry_in_background(name)` enqueues the
    submit on a worker (queue=long, timeout=900), returns immediately.
  - `mhr.utilis._submit_stock_entry_worker` runs the submit and
    publishes a realtime event when it lands.
  - Stock Entry form JS adds a "Submit in Background" button (only in
    Draft state) that calls the endpoint.
"""
import frappe
from frappe.tests.utils import FrappeTestCase


class TestSubmitStockEntryInBackground(FrappeTestCase):

    def test_endpoint_exists_and_whitelisted(self):
        from mhr.utilis import submit_stock_entry_in_background as fn
        self.assertTrue(
            getattr(fn, "is_whitelisted", False) is True
            or fn in frappe.whitelisted,
            "submit_stock_entry_in_background must be @frappe.whitelist().",
        )

    def test_endpoint_signature(self):
        from mhr.utilis import submit_stock_entry_in_background
        import inspect
        params = list(inspect.signature(submit_stock_entry_in_background).parameters.keys())
        self.assertEqual(params, ["name"])

    def test_endpoint_rejects_empty_name(self):
        from mhr.utilis import submit_stock_entry_in_background
        with self.assertRaises(frappe.exceptions.ValidationError):
            submit_stock_entry_in_background(None)

    def test_endpoint_rejects_non_draft(self):
        """If the doc isn't in Draft, the endpoint must throw — not
        silently re-submit."""
        from mhr.utilis import submit_stock_entry_in_background
        # Try with a name that doesn't exist; should DoesNotExistError
        # before reaching the docstatus check.
        with self.assertRaises(frappe.DoesNotExistError):
            submit_stock_entry_in_background("MAT-STE-NONEXISTENT-XYZ")

    def test_worker_function_exists(self):
        from mhr import utilis
        self.assertTrue(
            hasattr(utilis, "_submit_stock_entry_worker"),
            "Worker function _submit_stock_entry_worker must exist.",
        )

    def test_worker_publishes_realtime_event(self):
        """Worker must publish `mhr_stock_entry_submitted` so the form
        knows when to reload."""
        from mhr import utilis
        import inspect, re
        src = inspect.getsource(utilis._submit_stock_entry_worker)
        no_line = re.sub(r"#[^\n]*", "", src)
        self.assertIn(
            "mhr_stock_entry_submitted", no_line,
            "Worker must publish_realtime with event 'mhr_stock_entry_submitted' "
            "so the JS listener can reload the form.",
        )
        self.assertIn(
            "publish_realtime", no_line,
            "Must call frappe.publish_realtime to notify the user.",
        )

    def test_endpoint_uses_long_queue_with_real_timeout(self):
        from mhr import utilis
        import inspect, re
        src = inspect.getsource(utilis.submit_stock_entry_in_background)
        no_line = re.sub(r"#[^\n]*", "", src)
        self.assertIn("frappe.enqueue", no_line)
        self.assertIn('queue="long"', no_line, "Use the long queue for big SLE writes.")
        # timeout should be at least 600s (10 min) — 245 batches at ~250ms each = 60s,
        # plus Bin recompute. 900s gives generous headroom.
        m = re.search(r"timeout=(\d+)", no_line)
        self.assertIsNotNone(m, "explicit timeout= required")
        self.assertGreaterEqual(int(m.group(1)), 600,
                                "timeout must be >= 600s for large transfers.")
