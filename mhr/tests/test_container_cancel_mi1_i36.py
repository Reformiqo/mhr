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
            "c.docstatus = 1",
            src,
            "F3 regression: before_submit must filter on docstatus=1 "
            "(only Submitted Containers count as duplicates).",
        )
        self.assertIn(
            "frappe.throw",
            src,
            "F3 regression: before_submit must call frappe.throw on duplicate.",
        )

    def test_before_submit_throws_when_batch_ids_overlap(self):
        """MI1-I37 follow-up: F3 must throw when batch_ids overlap with
        an existing submitted Container at the same (container_no,
        lot_no, item). That's the real MI1-I36 corruption signature."""
        # Build a container with 3 batch_ids.
        c = container_mod.Container(
            {
                "doctype": "Container",
                "name": "TEST-1361-A",
                "container_no": "MCJC-1361",
                "lot_no": "21112025",
                "item": "75D/30f",
                "batches": [
                    {"batch_id": "MCJC-13612111202551"},
                    {"batch_id": "MCJC-13612111202552"},
                    {"batch_id": "MCJC-13612111202553"},
                ],
            }
        )
        # Mock frappe.db.sql so the overlap query returns a row.
        fake_row = frappe._dict(parent="MCJC-1361-OTHER", batch_id="MCJC-13612111202551")
        with patch.object(frappe.db, "sql", return_value=[fake_row]):
            with self.assertRaises(frappe.ValidationError) as ctx:
                c.before_submit()
            msg = str(ctx.exception)
            self.assertIn("MCJC-13612111202551", msg,
                "Throw must name the overlapping batch_id so the user knows which.")
            self.assertIn("MCJC-1361-OTHER", msg,
                "Throw must name the other Container so the user can navigate to it.")

    def test_before_submit_allows_when_batch_ids_disjoint(self):
        """The whole point of this iteration: two Containers with the
        same (container_no, lot_no, item) but DISJOINT batch_id ranges
        (e.g. 1-500 + 501-512) MUST be allowed — legitimate two-shipment
        scenario reported by Raj."""
        c = container_mod.Container(
            {
                "doctype": "Container",
                "name": "MCJC-1593-429-1",
                "container_no": "MCJC-1593",
                "lot_no": "04042026",
                "item": "58D/12F",
                "batches": [
                    # cones 1..500
                    {"batch_id": f"MCJC-1593040420261"},
                    {"batch_id": f"MCJC-159304042026500"},
                ],
            }
        )
        # Mock the overlap query to return 0 rows (no overlap with the
        # existing -431-1 which uses suffixes 501-512).
        with patch.object(frappe.db, "sql", return_value=[]):
            c.before_submit()  # must NOT raise

    def test_before_submit_skips_when_keys_missing(self):
        """If container_no/lot_no/item is missing, the check is a no-op
        (no spurious throw, no DB hit)."""
        c = container_mod.Container(
            {
                "doctype": "Container",
                "name": "TEST-MISSING",
                "container_no": None,
                "lot_no": None,
                "item": None,
                "batches": [],
            }
        )
        with patch.object(frappe.db, "sql", side_effect=AssertionError("must not call DB")):
            c.before_submit()  # must not raise

    def test_before_submit_skips_when_no_batch_ids(self):
        """If THIS Container has no batch_ids yet (empty child rows),
        there's nothing to overlap on — skip cleanly."""
        c = container_mod.Container(
            {
                "doctype": "Container",
                "name": "TEST-EMPTY",
                "container_no": "MCJC-X",
                "lot_no": "L",
                "item": "75D/30f",
                "batches": [],
            }
        )
        with patch.object(frappe.db, "sql", side_effect=AssertionError("must not call DB")):
            c.before_submit()  # must not raise

    def test_before_submit_query_filters_by_triple_and_batch_ids(self):
        """Source-level pin: the overlap query must filter on all four
        constraints (container_no, lot_no, item, c.docstatus=1) AND
        the batch_id IN (mine) — otherwise corrupt data slips through."""
        import inspect
        src = inspect.getsource(container_mod.Container.before_submit)
        self.assertIn("c.container_no = %s", src,
            "Query must filter on container_no.")
        self.assertIn("c.lot_no = %s", src,
            "Query must filter on lot_no.")
        self.assertIn("c.item = %s", src,
            "Query must filter on item — different denier ≠ duplicate.")
        self.assertIn("c.docstatus = 1", src,
            "Query must consider only Submitted (docstatus=1) containers.")
        self.assertIn("bi.batch_id IN", src,
            "Query must check batch_id overlap — the actual corruption signal.")
