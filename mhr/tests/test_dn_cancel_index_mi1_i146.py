"""MI1-I146 (2026-09-15) — "why is cancelling this Delivery Note taking so
long ... is that scheduler inactive or not".

A live "Cancel in Background" run on a real 217-row Delivery Note was
caught mid-flight: 6+ minutes in, 0 of 217 Serial and Batch Bundles
cancelled, worker CPU time nearly idle. `information_schema.processlist`
showed the live query:

    UPDATE `tabSerial and Batch Entry`
    SET is_cancelled = 1
    WHERE voucher_no = %s AND voucher_type = %s

— part of ERPNext's OWN core document-cancellation flow (Serial and Batch
Bundle -> voucher reversal), not anything mhr's own hooks do. `EXPLAIN`
confirmed `type: ALL`, `rows: ~1191543` — a full scan of the whole table,
because neither `voucher_no` nor `voucher_type` was ever indexed on this
table (only `batch_no` / `parent`, from MI1-I119's own add_serial_batch_
bundle_status_index patch, added for the balance report's own different
join shape). This UPDATE runs once PER ROW being cancelled, so a 217-row
note pays a ~1.2M-row scan 217 times over — nothing to do with the
Scheduler (a separate subsystem entirely; it governs periodic/cron jobs,
not this document's own cancel-time work, and was confirmed still Inactive
throughout while OTHER on-demand background jobs on the same "long" queue
worker ran and completed normally).

Adding the index while the cancel was STILL actively running (attempted
live, ALGORITHM=INPLACE, LOCK=NONE) sat "Waiting for table metadata lock"
for 8+ minutes and had to be killed — a continuous stream of quick writes
against the same table starves even a non-blocking ALTER's brief
metadata-lock acquisition. The patch itself is unconditional and safe to
run any time nothing is actively hammering this table (a fresh site,
`bench migrate`, or a quiet moment on an existing one).

**This table's schema itself differs between v15 and v16**: a real
DESCRIBE on this app's own v15 bench found `Serial and Batch Entry` has NO
`voucher_type` / `voucher_no` / `is_cancelled` columns at all (only
`is_outward`, with the voucher reference tracked solely on the parent
Serial and Batch Bundle) — Frappe v16 denormalised the voucher reference
onto every entry row; this whole slow-query shape is unreachable on v15.
Tests below check both worlds: a no-op that never crashes on a v15-shaped
table, and the real index + EXPLAIN win on a v16-shaped one.
"""
import importlib
import os

import frappe
from frappe.tests.utils import FrappeTestCase

PATCHES = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
PATCH_MODULE = "mhr.patches.v1_0.add_serial_batch_entry_voucher_index"


def _patch():
    return importlib.import_module(PATCH_MODULE)


class TestIndexPatchRegistered(FrappeTestCase):

    def test_registered_in_patches_txt_after_its_prerequisite(self):
        with open(PATCHES) as f:
            lines = [l.strip().split()[0] for l in f if l.strip() and not l.startswith("#")]
        self.assertIn(PATCH_MODULE, lines)
        # Not a hard dependency, just documents intent: this is the second
        # Serial and Batch Entry index added to this table, after MI1-I119's.
        self.assertGreater(
            lines.index(PATCH_MODULE),
            lines.index("mhr.patches.v1_0.add_serial_batch_bundle_status_index"),
        )


class TestColumnsExistIsAccurateEitherWay(FrappeTestCase):

    def test_matches_a_real_describe_of_the_live_table(self):
        patch = _patch()
        live_columns = {
            r[0]
            for r in frappe.db.sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (patch.TABLE,),
            )
        }
        expected = set(patch.COLUMNS).issubset(live_columns)
        self.assertEqual(patch.columns_exist(), expected)

    def test_false_for_a_table_that_has_neither_column(self):
        patch = _patch()
        self.assertFalse(patch.columns_exist(table="tabBatch", columns=("voucher_type", "voucher_no")))


class TestPatchBehavesCorrectlyOnThisBenchsOwnSchema(FrappeTestCase):
    """Runs the patch against whatever this bench's OWN Serial and Batch
    Entry schema actually is (v15: no-op; v16: real index) — both branches
    must be idempotent and must never raise."""

    def test_execute_is_safe_and_idempotent(self):
        patch = _patch()
        patch.execute()
        patch.execute()  # must not raise on a second run either way
        if patch.columns_exist():
            self.assertTrue(patch.index_exists())
        else:
            self.assertFalse(patch.index_exists(), "no columns to index -- must not create it anyway")

    def test_index_covers_the_exact_columns_when_it_is_created(self):
        patch = _patch()
        patch.execute()
        if not patch.columns_exist():
            self.skipTest("this bench's Serial and Batch Entry has no voucher_type/voucher_no (v15 schema)")
        cols = frappe.db.sql(
            """SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index)
               FROM information_schema.statistics
               WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s""",
            (patch.TABLE, patch.INDEX_NAME),
        )[0][0]
        self.assertEqual(cols, ",".join(patch.COLUMNS))

    def test_explain_no_longer_shows_a_full_table_scan_when_applicable(self):
        """The actual regression this ticket is about — but only
        meaningful where the query itself is reachable at all."""
        patch = _patch()
        if not patch.columns_exist():
            self.skipTest("voucher_type/voucher_no do not exist on this bench's schema (v15) -- query is unreachable")
        patch.execute()
        plan = frappe.db.sql(
            """EXPLAIN SELECT * FROM `tabSerial and Batch Entry`
               WHERE voucher_no = %s AND voucher_type = %s""",
            ("__mi1_i146_does_not_exist__", "Delivery Note"),
            as_dict=True,
        )[0]
        self.assertNotEqual(plan.get("type"), "ALL",
            "query must not full-scan tabSerial and Batch Entry any more")
        self.assertEqual(plan.get("key"), patch.INDEX_NAME)
