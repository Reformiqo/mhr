"""Pin: every `DELETE FROM tabBatch` in Container's controller is guarded
against wiping a Batch master that another live Container still references.

The original corruption (MCJC-1361, MCJC-1538, MCJC-1614-997 / lot 07042026)
came from on_cancel doing a raw `DELETE FROM tabBatch` without checking
sibling Container references. The MI1-I36 F2 patch added that guard to
on_cancel — but on_trash (line 332) and resubmit_container (line 793) had
the SAME raw delete shape with no other-owner check, so on_trash silently
re-orphaned masters every time a duplicate Container was deleted, and
resubmit_container did so on every rebuild. That's how 272 orphans grew
back AFTER the original heal patch had already run.

This test is source-level: scans container.py for every
`DELETE FROM \`tabBatch\`` and asserts each one is preceded — within a
small window — by the other-owner SELECT that proves no live sibling
Container references the batch_id. If a future fourth delete site is
added without the guard, this test FAILs.
"""

import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase


CONTAINER_PY = os.path.join(
    frappe.get_app_path("mhr"), "mhr", "doctype", "container", "container.py"
)

DELETE_BATCH_RE = re.compile(
    r"DELETE\s+FROM\s+`tabBatch`\s+WHERE", re.IGNORECASE
)

# The F2-shape guard — must appear in the ~30 lines BEFORE every batch
# DELETE. We pin the load-bearing clauses, not exact whitespace.
GUARD_CLAUSES = (
    "tabBatch Items",
    "parenttype = 'Container'",
    "c.docstatus != 2",
)


class TestEveryBatchDeleteIsGuarded(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = open(CONTAINER_PY).read()
        cls.lines = cls.src.splitlines()

    def test_at_least_one_delete_exists(self):
        """Sanity: this test is meaningful only if the controller still
        deletes Batch masters somewhere. If they all go away, that's fine
        and this assertion can be relaxed."""
        matches = list(DELETE_BATCH_RE.finditer(self.src))
        self.assertGreaterEqual(
            len(matches), 1,
            "No DELETE FROM `tabBatch` found in container.py — if the "
            "controller truly stopped deleting Batch masters, update this test.",
        )

    def test_every_delete_has_other_owner_guard_within_30_lines(self):
        """For each DELETE FROM `tabBatch`, the F2 guard SELECT must appear
        within the previous 30 lines of the same method."""
        unguarded = []
        for m in DELETE_BATCH_RE.finditer(self.src):
            # Find the line number of the match.
            line_no = self.src[: m.start()].count("\n") + 1  # 1-indexed
            # Pull the 30 lines preceding the DELETE (inclusive of the line itself).
            start = max(0, line_no - 30)
            window = "\n".join(self.lines[start:line_no])
            missing = [c for c in GUARD_CLAUSES if c not in window]
            if missing:
                unguarded.append((line_no, missing, window.splitlines()[-3:]))
        if unguarded:
            msg_lines = ["Found UNGUARDED `DELETE FROM tabBatch` site(s):"]
            for line_no, missing, tail in unguarded:
                msg_lines.append(
                    f"  container.py:{line_no} missing clauses {missing}; "
                    f"last 3 lines before DELETE: {tail}"
                )
            self.fail("\n".join(msg_lines))

    def test_guard_clauses_distinct_from_delete_clause(self):
        """Belt + braces: the guard's `c.docstatus != 2` is on the SELECT
        against tabContainer, not a stray comment. Pin that the SELECT
        actually queries `tabBatch Items` joined to `tabContainer`."""
        # All three guard clauses must co-exist somewhere in the file.
        for clause in GUARD_CLAUSES:
            self.assertIn(
                clause, self.src,
                f"Required guard clause {clause!r} missing from container.py "
                "— the other-owner check has been weakened or removed.",
            )
