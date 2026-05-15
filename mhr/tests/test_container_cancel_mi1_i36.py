"""MI1-I36 — Container cancel surfaces real error + guards Batch DELETE
+ blocks duplicate (container_no, lot_no) submissions.

Original ticket: Raj Tiwari reported that container MCJC-1361 (lot 21112025)
got submitted twice. Cancelling one duplicate ran
`DELETE FROM tabBatch WHERE name = %s` for all 250 batch_ids without
verifying ownership, wiping rows the OTHER Container still depended on.
The orphaned references caused:
  - Container Report not reflecting qty
  - Batch form opens blank
  - A subsequent cancel attempt threw "Batch has negative quantity -24.6"
    inside ERPNext stock validation, which the mhr code tried to log via
    `frappe.log_error(long_msg, "Container Cancel")` — but in Frappe v15
    the signature is `log_error(title=None, message=None, ...)`, so the
    long_msg landed in Title (140-char Data field) and crashed with
    CharacterLengthExceededError, surfaced to the user as "Value too big".

Three defensive fixes, each pinned by tests below:
  F1 — `on_cancel`'s except: `frappe.log_error(message=..., title=...)`
       so long messages don't truncate-explode.
  F2 — `on_cancel`'s DELETE loop: skip if another non-cancelled Container
       (different docname) still references the same batch_id.
  F3 — `before_submit`: throw a clear error if a duplicate (container_no,
       lot_no) is already submitted under a different docname.
"""

import inspect
import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.doctype.container import container as container_mod


class TestContainerCancelLogErrorFix(FrappeTestCase):
    """F1 — log_error must use keyword args so long messages land in
    `message` (LongText), not `title` (Data, 140-char cap)."""

    def test_log_error_uses_message_kwarg(self):
        # Inspect the raw source of on_cancel — no clever stripping; we just
        # need a few unambiguous markers to be present together.
        src = inspect.getsource(container_mod.Container.on_cancel)

        self.assertIn(
            "Failed to cancel PR",
            src,
            "Sanity: the on_cancel except branch should still mention "
            "'Failed to cancel PR' in its log_error message.",
        )
        self.assertIn(
            'message=f"Failed to cancel PR',
            src,
            "F1 regression: on_cancel's log_error must pass the long error via "
            "`message=` (LongText). A positional long string lands in `title` "
            "(140-char Data cap) and crashes with CharacterLengthExceededError, "
            "which the user sees as 'Value too big'.",
        )
        self.assertIn(
            'title="Container Cancel"',
            src,
            "F1 regression: on_cancel's log_error should keep the short "
            'label as `title="Container Cancel"`.',
        )


class TestContainerCancelDeleteGuard(FrappeTestCase):
    """F2 — on_cancel's DELETE must skip batch_ids referenced by any
    other non-cancelled Container."""

    def test_on_cancel_has_other_owner_guard(self):
        # Inspect raw source — the SQL guard lives in a triple-quoted
        # string, so we deliberately don't strip docstrings.
        src = inspect.getsource(container_mod.Container.on_cancel)

        # The guard query must look up other Container parents that still
        # reference the same batch_id.
        self.assertIn(
            "tabBatch Items",
            src,
            "F2 regression: on_cancel must consult tabBatch Items to find "
            "other containers referencing the same batch_id before deleting.",
        )
        self.assertIn(
            "docstatus != 2",
            src,
            "F2 regression: the guard must filter on parent Container "
            "docstatus != 2 (i.e. not Cancelled) — otherwise it would skip "
            "deletes even when the other owner is itself cancelled.",
        )
        # The DELETE must filter on custom_container_no + custom_lot_no
        # (matching on_trash's safer SQL) — never a bare DELETE by name.
        self.assertNotIn(
            'DELETE FROM `tabBatch` WHERE name = %s",',
            src,
            "F2 regression: the bare DELETE by name (no container/lot "
            "filter) is the original bug. Must filter on custom_container_no "
            "and custom_lot_no.",
        )
        self.assertIn(
            "custom_container_no",
            src,
            "F2 regression: DELETE must filter on custom_container_no.",
        )
        self.assertIn(
            "custom_lot_no",
            src,
            "F2 regression: DELETE must filter on custom_lot_no.",
        )


class TestContainerBeforeSubmitDuplicateCheck(FrappeTestCase):
    """F3 — before_submit must block a duplicate (container_no, lot_no)
    where another Submitted Container already exists under a different
    docname."""

    def test_before_submit_method_exists(self):
        self.assertTrue(
            hasattr(container_mod.Container, "before_submit"),
            "F3 regression: Container.before_submit must exist to block "
            "duplicate (container_no, lot_no) submissions.",
        )

    def test_before_submit_queries_duplicate(self):
        src = inspect.getsource(container_mod.Container.before_submit)
        self.assertIn(
            "container_no",
            src,
            "F3 regression: before_submit must check the container_no field.",
        )
        self.assertIn(
            "lot_no",
            src,
            "F3 regression: before_submit must check the lot_no field.",
        )
        self.assertIn(
            '"docstatus": 1',
            src,
            "F3 regression: before_submit must filter on docstatus=1 "
            "(only Submitted Containers count as duplicates).",
        )
        self.assertIn(
            "frappe.throw",
            src,
            "F3 regression: before_submit must call frappe.throw on duplicate.",
        )

    def test_before_submit_throws_when_duplicate_exists(self):
        """Behavioral: with frappe.db.get_value mocked to return a
        duplicate, before_submit raises ValidationError."""
        c = container_mod.Container(
            {
                "doctype": "Container",
                "name": "TEST-1361-A",
                "container_no": "MCJC-1361",
                "lot_no": "21112025",
                "batches": [],
            }
        )
        with patch.object(frappe.db, "get_value", return_value="MCJC-1361-OTHER"):
            with self.assertRaises(frappe.ValidationError) as ctx:
                c.before_submit()
            msg = str(ctx.exception)
            self.assertIn("MCJC-1361", msg)
            self.assertIn("21112025", msg)
            self.assertIn("MCJC-1361-OTHER", msg)

    def test_before_submit_passes_when_no_duplicate(self):
        """Behavioral: with no existing duplicate, before_submit returns
        silently (no throw)."""
        c = container_mod.Container(
            {
                "doctype": "Container",
                "name": "TEST-1361-B",
                "container_no": "MCJC-1361",
                "lot_no": "21112025",
                "batches": [],
            }
        )
        with patch.object(frappe.db, "get_value", return_value=None):
            # Must not raise.
            c.before_submit()

    def test_before_submit_skips_when_keys_missing(self):
        """If container_no or lot_no is missing, the duplicate check
        is a no-op (no spurious throw, no DB hit)."""
        c = container_mod.Container(
            {
                "doctype": "Container",
                "name": "TEST-MISSING",
                "container_no": None,
                "lot_no": None,
                "batches": [],
            }
        )
        with patch.object(frappe.db, "get_value", side_effect=AssertionError("must not call DB")):
            c.before_submit()  # must not raise
