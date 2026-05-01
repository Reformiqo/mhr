"""Regression test for MI1-I25.

Raj reported that after cancelling a Purchase Receipt and submitting
a new Container (MCJC-1592-LOT-04032026), the Purchase Receipt was
not auto-created. The bench Error Log showed
`pymysql.err.OperationalError: (1205, 'Lock wait timeout exceeded')`
from inside `Container.create_purchase_receipt` — three concurrent
Container submits were fighting for SLE row locks on the same
item + warehouse pair, and PR.submit() lost the race.

Old behaviour: on lock timeout the except clause did
`frappe.db.rollback()` + `frappe.msgprint()` and swallowed the
exception. Because Frappe writes the parent doc before running
on_submit, the Container was already committed at docstatus=1, so we
ended up with a submitted Container, persisted batches, and no PR.
The user's only escape was the Actions -> Resubmit menu.

New contract:
  1) Lock-timeout / deadlock is retried up to 3 times with backoff.
  2) If the final attempt still fails, the exception is re-raised
     (via frappe.throw) so on_submit aborts. Frappe's outer save flow
     then rolls back the Container too — the user gets an actionable
     error and can simply retry the Container submit.
"""
import frappe
import inspect
import re
from frappe.tests.utils import FrappeTestCase


class TestContainerPRLockRetry(FrappeTestCase):

    def _src(self):
        from mhr.mhr.doctype.container.container import Container
        return inspect.getsource(Container.create_purchase_receipt)

    def test_create_pr_has_lock_retry_loop(self):
        src = self._src()
        # Strip comments so we don't match docstring/comment text.
        code = re.sub(r"#[^\n]*", "", src)
        code = re.sub(r'""".*?"""', "", code, flags=re.DOTALL)
        self.assertIn(
            "lock wait timeout", code.lower(),
            "create_purchase_receipt must detect 'Lock wait timeout' "
            "errors and retry — MI1-I25 root cause.",
        )
        self.assertIn(
            "for attempt in range",
            code,
            "create_purchase_receipt must retry the PR submit in a loop.",
        )

    def test_create_pr_reraises_on_terminal_failure(self):
        """If the retry exhausts, the function must re-raise via
        frappe.throw so the Container submit aborts atomically — not
        silently msgprint and leave an orphan Container."""
        src = self._src()
        code = re.sub(r"#[^\n]*", "", src)
        code = re.sub(r'""".*?"""', "", code, flags=re.DOTALL)
        self.assertIn(
            "frappe.throw",
            code,
            "On terminal failure create_purchase_receipt must "
            "frappe.throw so the parent submit rolls back. "
            "Pre-fix it called frappe.msgprint and swallowed the error.",
        )
        # Must NOT silently swallow with msgprint as the only failure
        # path — that's the old bug.
        # We allow msgprint to exist (e.g. for non-error info) but
        # there must be a throw too.
        self.assertGreater(
            code.count("frappe.throw"), 0,
            "Need at least one frappe.throw in the failure path.",
        )

    def test_create_pr_imports_time_for_backoff(self):
        from mhr.mhr.doctype.container import container as container_module
        self.assertTrue(
            hasattr(container_module, "time"),
            "container.py must import the `time` module for the "
            "retry backoff (time.sleep between attempts).",
        )

    def test_simulated_lock_failure_retries_then_raises(self):
        """Drive create_purchase_receipt against a fake PR that always
        raises a lock-timeout error. Verify it retries 3 times and
        finally raises (Container submit must not silently succeed)."""
        from mhr.mhr.doctype.container import container as container_module

        class FakePR:
            def __init__(self):
                self.flags = frappe._dict()
                self.items = []
                self.name = "PR-TEST-000"
                self.attempts = 0
                # Other fields the production method assigns to.
                self.company = self.supplier = self.posting_date = None
                self.custom_container_no = self.custom_lot_number = None
                self.custom_lusture = self.custom_glue = None
                self.custom_grade = self.custom_pulp = self.custom_fsc = None
                self.custom_merge_no = self.custom_notes = None
                self.custom_total_batches = 0
                self.is_return = 0
                self.return_against = None
            def __setattr__(self, k, v):
                object.__setattr__(self, k, v)
            def append(self, table, row):
                # Mimic Frappe's child-table append.
                self.items.append(row)
            def save(self):
                pass
            def submit(self):
                self.attempts += 1
                # pymysql wraps it as an OperationalError with the
                # message "Lock wait timeout exceeded; try restarting
                # transaction" — the retry path is keyed on that string.
                raise Exception("(1205, 'Lock wait timeout exceeded; try restarting transaction')")

        fake_pr = FakePR()

        # Patch frappe.new_doc to return our fake.
        orig_new_doc = frappe.new_doc
        orig_throw = frappe.throw
        orig_log_error = frappe.log_error
        thrown = []

        def fake_throw(msg, *args, **kwargs):
            thrown.append(str(msg))
            raise frappe.ValidationError(msg)

        frappe.new_doc = lambda dt: fake_pr if dt == "Purchase Receipt" else orig_new_doc(dt)
        frappe.throw = fake_throw
        frappe.log_error = lambda *a, **k: None

        # Build a stub Container that the method can run against.
        from mhr.mhr.doctype.container.container import Container

        class StubContainer:
            company = "_TEST_CO"
            supplier = "_TEST_SUP"
            posting_date = "2026-05-01"
            name = "TEST-CONT-1"
            lot_no = "TESTLOT"
            lusture = ""
            glue = ""
            grade = ""
            pulp = ""
            fsc = ""
            merge_no = ""
            notes = ""
            set_warehouse = ""
            batches = []
            def get_items(self):
                return [{"item": "ITM-1", "batch_qty": 1, "stock_uom": "Nos"}]
            def create_serial_and_batch_bundle(self, item, direction):
                return "SBB-FAKE"
            correct_batch_qty_after_pr_submit = (
                Container.correct_batch_qty_after_pr_submit
            )

        stub = StubContainer()

        try:
            with self.assertRaises(frappe.ValidationError):
                Container.create_purchase_receipt(stub)
        finally:
            frappe.new_doc = orig_new_doc
            frappe.throw = orig_throw
            frappe.log_error = orig_log_error

        self.assertEqual(
            fake_pr.attempts, 3,
            "Expected exactly 3 PR.submit() attempts before giving up — "
            "got {0}. Retry loop is wrong.".format(fake_pr.attempts),
        )
        self.assertTrue(
            any("MI1-I25" in t or "lock" in t.lower() or "transient" in t.lower()
                for t in thrown),
            "Final frappe.throw message should reference the lock / "
            "transient nature so the user knows to retry.",
        )
