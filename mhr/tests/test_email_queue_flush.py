"""MI1 — Email Queue minute-flush hook.

Meher's Email Queue had 1000+ Not Sent rows because the default
scheduled flush wasn't draining. To unstick it without forcing
`now=True` on the bulk-PDF send path (which trips gunicorn timeout
for 20+ DN batches), we add a 1-minute cron entry calling
`mhr.email.flush_email_queue` which is a thin wrapper around
`frappe.email.queue.flush()` plus titled Error-Log on failure.
"""

import inspect
from unittest.mock import patch
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import email as mhr_email


class TestFlushEmailQueue(FrappeTestCase):

    def test_function_exists_and_callable(self):
        self.assertTrue(
            callable(mhr_email.flush_email_queue),
            "mhr.email.flush_email_queue must be a callable — the cron schedule references it.",
        )

    def test_delegates_to_frappe_queue_flush(self):
        # Pin the delegation so a refactor doesn't accidentally invent a
        # parallel flush implementation that drifts from Frappe core.
        src = inspect.getsource(mhr_email.flush_email_queue)
        self.assertIn(
            "from frappe.email.queue import flush", src,
            "Wrapper must import frappe.email.queue.flush — that's the "
            "canonical drain entry-point.",
        )
        self.assertIn(
            "flush()", src,
            "Wrapper must call flush() — otherwise it's a no-op.",
        )

    def test_logs_on_smtp_failure(self):
        # A transient SMTP failure must NOT bubble out as an uncaught
        # exception — that would mark the entire scheduler tick as
        # failed and Frappe would back off the job. We catch + log
        # with a titled Error Log row.
        src = inspect.getsource(mhr_email.flush_email_queue)
        self.assertIn("except Exception", src,
            "Wrapper must catch exceptions from flush().")
        self.assertIn("frappe.log_error", src,
            "Wrapper must log failures via frappe.log_error.")
        self.assertIn("MI1: flush_email_queue failed", src,
            "Error Log title must be searchable — pin the exact string.")

    def test_exception_is_swallowed(self):
        # Behavioral test — mock flush to raise, ensure wrapper returns
        # cleanly instead of propagating the exception.
        called = {}
        def fake_log_error(**kw):
            called.update(kw)
        with patch("frappe.email.queue.flush", side_effect=RuntimeError("smtp down")), \
             patch.object(frappe, "log_error", side_effect=fake_log_error):
            try:
                mhr_email.flush_email_queue()
            except Exception as e:
                self.fail(f"flush_email_queue must swallow exceptions; raised {e!r}.")
        self.assertIn("title", called,
            "On failure the wrapper must call frappe.log_error with a title kwarg.")
        self.assertIn("MI1", called.get("title", ""),
            "Error Log title must include MI1 prefix for searchability.")


class TestCronScheduleRegistered(FrappeTestCase):
    """Hooks.py must list mhr.email.flush_email_queue under a 1-minute
    cron entry — otherwise the wrapper exists but never runs."""

    def test_minute_cron_present(self):
        import importlib
        hooks_mod = importlib.import_module("mhr.hooks")
        cron = hooks_mod.scheduler_events.get("cron", {})
        # Find any minute-level entry (`* * * * *`).
        minute_entries = cron.get("* * * * *", [])
        self.assertIn(
            "mhr.email.flush_email_queue", minute_entries,
            "Every-minute cron entry must include mhr.email.flush_email_queue. "
            f"Current `* * * * *` entries: {minute_entries}",
        )

    def test_other_cron_entries_intact(self):
        # Sanity: the existing */5 entry must not be removed by the edit.
        import importlib
        hooks_mod = importlib.import_module("mhr.hooks")
        cron = hooks_mod.scheduler_events.get("cron", {})
        five_min = cron.get("*/5 * * * *", [])
        self.assertIn(
            "mhr.utilis.enqueue_cancel_receipts", five_min,
            "*/5 minute cron entry must still include the existing "
            "enqueue_cancel_receipts hook — we only added, never replaced.",
        )
