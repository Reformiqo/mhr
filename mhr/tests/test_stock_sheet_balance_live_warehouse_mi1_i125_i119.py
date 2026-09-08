"""MI1-I125 / MI1-I119 / MI1-I124 — Stock Sheet (Balance Report) live location,
balance performance, and the numeric Fetch Batches window.

MI1-I125 (Rohit 2026-09-05): MAT-GD-2026-00011-1 moved MCL-29 from Finished
Goods - MC to Vadod - MC and the sheet's "Accepted Warehouse" still said
Finished Goods - MC, because the column read Container.set_warehouse — the
inward warehouse, which MI1-I103 settled no stock movement may rewrite. The
column now shows where the Serial and Batch Bundle balance actually is.

MI1-I119 (Raj 2026-09-02): a full-range run took ~85 s, 73 of them joining
every Serial and Batch Entry to its bundle through the bundle table's primary
key, so frappe flipped the report into prepared-report mode and the HTY run
never came back. The balance stage now goes through a covering index and
returns only non-zero (batch, warehouse) pairs, and only stocked batches are
loaded in full.

MI1-I124 (2026-09-05): "Count 10" on an HTY Delivery Note fetched supplier
batches 1, 10, 11, 12, 13, 14, 100, 101, 102, 103 — the string-smallest ten,
re-sorted — because the scan window was ordered as text.
"""

import importlib
import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from mhr import note, utilis

REPORT_MODULE = "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"
ITEM = "58D/8F"
COMPANY = "Meher Creations"
SRC_WH = "Finished Goods - MC"
DEST_WH = "Vadod - MC"
PATCHES = os.path.join(frappe.get_app_path("mhr"), "patches.txt")


def _report():
    return importlib.import_module(REPORT_MODULE)


def _pick_stocked_batch():
    """A VFY batch of ITEM with cone > 0 whose whole live balance sits in
    SRC_WH and that no open Sales Order books."""
    rows = frappe.db.sql("""
        SELECT b.name, b.custom_container_no, b.custom_lot_no, b.custom_cone, s.bal FROM `tabBatch` b
        JOIN (SELECT sbe.batch_no, SUM(sbe.qty) bal FROM `tabSerial and Batch Entry` sbe
              JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
              WHERE sbb.warehouse = %s AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
                AND sbb.type_of_transaction IN ('Inward', 'Outward')
              GROUP BY sbe.batch_no HAVING bal >= 5) s ON s.batch_no = b.name
        WHERE b.item = %s AND b.disabled = 0 AND IFNULL(b.custom_cone, 0) > 0
          AND IFNULL(b.custom_container_no, '') != '' AND IFNULL(b.custom_lot_no, '') != ''
        ORDER BY b.name LIMIT 40""", (SRC_WH, ITEM), as_dict=True)
    booked = utilis.effective_booking_by_batch([r.name for r in rows])
    for r in rows:
        if flt(booked.get(r.name, {}).get("qty", 0)) == 0:
            everywhere = utilis._resolve_batch_warehouse(r.name)
            if everywhere == SRC_WH and abs(utilis._batch_balance_in_warehouse(r.name, SRC_WH) - flt(r.bal)) < 0.0005:
                return r
    return None


def _detail_rows(filters):
    r = _report()
    cols, data = r.execute(dict(filters))
    return [row for row in data if row.get("sort_order") == 0]


class TestLiveAcceptedWarehouse(FrappeTestCase):
    """MI1-I125 on a real Material Transfer."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.batch = _pick_stocked_batch()
        if not cls.batch:
            return
        b = cls.batch
        cls.filters = {"container": b.custom_container_no, "lot_no": b.custom_lot_no, "cone": b.custom_cone}
        cls.before = _detail_rows(cls.filters)
        se = frappe.new_doc("Stock Entry")
        se.update({"stock_entry_type": "Material Transfer", "purpose": "Material Transfer", "company": COMPANY,
                   "posting_date": today(), "set_posting_time": 1, "from_warehouse": SRC_WH, "to_warehouse": DEST_WH})
        se.append("items", {"item_code": ITEM, "qty": flt(b.bal, 3), "batch_no": b.name, "use_serial_batch_fields": 1,
                            "s_warehouse": SRC_WH, "t_warehouse": DEST_WH, "custom_cone": b.custom_cone})
        se.insert(ignore_permissions=True)
        se.submit()
        cls.transfer = se
        cls.after = _detail_rows(cls.filters)

    def setUp(self):
        if not getattr(self, "batch", None):
            self.skipTest("No free stocked VFY batch of the item in the source warehouse.")

    def test_accepted_warehouse_follows_the_stock(self):
        self.assertTrue(self.before, "the group must be on the sheet before the transfer")
        self.assertTrue(all(DEST_WH not in (r["Accepted Warehouse"] or "") for r in self.before),
                        [r["Accepted Warehouse"] for r in self.before])
        self.assertTrue(any(DEST_WH in (r["Accepted Warehouse"] or "") for r in self.after),
                        f"after moving {self.batch.name} to {DEST_WH}: {[r['Accepted Warehouse'] for r in self.after]}")

    def test_inward_warehouse_untouched(self):
        """MI1-I103: the report reads live stock; the Container master keeps
        the inward warehouse, which the transfer must not have rewritten."""
        before = frappe.db.get_value("Container", {"container_no": self.batch.custom_container_no,
                                                    "lot_no": self.batch.custom_lot_no, "docstatus": 1}, "set_warehouse")
        self.assertNotEqual(before, DEST_WH)

    def test_balance_figures_unchanged_by_a_transfer(self):
        """A transfer moves stock, it does not change how much there is."""
        total = lambda rows: (round(sum(flt(r["Balance"]) for r in rows), 2), sum(int(r["Balance Box"]) for r in rows))
        self.assertEqual(total(self.before), total(self.after))


class TestLiveWarehouseHelpers(FrappeTestCase):

    def test_largest_first_and_fallback(self):
        r = _report()
        by_batch = r.live_warehouses_by_batch({("B1", "Vadod - MC"): 30.0, ("B1", "Finished Goods - MC"): 10.0,
                                                ("B2", "Vadod - MC"): 5.0, ("B3", "Stores - MC"): -2.0,
                                                ("B4", ""): 9.0})
        self.assertEqual(r.live_warehouses(["B1", "B2"], by_batch), "Vadod - MC, Finished Goods - MC")
        self.assertEqual(r.live_warehouses(["B2"], by_batch), "Vadod - MC")
        self.assertEqual(r.live_warehouses(["B3", "B4", "nope"], by_batch), "", "negatives and blanks do not place stock")

    def test_batch_balances_are_the_warehouse_sums(self):
        r = _report()
        b = _pick_stocked_batch()
        if not b:
            self.skipTest("No stocked batch to compare.")
        wb = r.get_batch_warehouse_balances([b.name])
        self.assertAlmostEqual(sum(q for (bn, wh), q in wb.items() if bn == b.name), r.get_batch_balances([b.name])[b.name], places=3)
        self.assertAlmostEqual(wb[(b.name, SRC_WH)], utilis._batch_balance_in_warehouse(b.name, SRC_WH), places=3,
                               msg="same rule as the app's single-batch helper")

    def test_orphan_entries_do_not_count(self):
        """~51K Serial and Batch Entry rows on this site have no bundle behind
        them; a bundle-less sum would show 22.6K batches of phantom stock."""
        r = _report()
        batch = frappe.new_doc("Batch")
        batch.batch_id = "MI1I119-ORPHAN-TEST"
        batch.item = ITEM
        batch.insert(ignore_permissions=True)
        sbe = frappe.new_doc("Serial and Batch Entry")
        sbe.update({"parent": "MI1I119-NO-SUCH-BUNDLE", "parenttype": "Serial and Batch Bundle", "parentfield": "entries",
                    "idx": 1, "batch_no": batch.name, "qty": 99, "warehouse": SRC_WH, "docstatus": 1})
        sbe.name = frappe.generate_hash(length=10)
        sbe.db_insert()
        self.assertEqual(r.get_batch_warehouse_balances([batch.name]), {})
        self.assertEqual(r.get_batch_balances([batch.name]), {})

    def test_external_job_work_warehouses_still_excluded(self):
        r = _report()
        src = inspect.getsource(r.get_batch_warehouse_balances)
        self.assertIn("get_external_job_work_warehouses", src)
        self.assertIn("NOT IN", src)


class TestBalanceStagePerformance(FrappeTestCase):
    """MI1-I119 — the shape that makes a full-range run fit the inline budget."""

    def test_index_patch_registered_and_idempotent(self):
        with open(PATCHES) as f:
            lines = [l.strip().split()[0] for l in f if l.strip() and not l.startswith("#")]
        self.assertIn("mhr.patches.v1_0.add_serial_batch_bundle_status_index", lines)
        self.assertIn("mhr.patches.v1_0.set_stock_sheet_balance_report_inline", lines)
        self.assertGreater(lines.index("mhr.patches.v1_0.add_serial_batch_bundle_status_index"),
                           lines.index("mhr.patches.v1_0.heal_receive_batch_qty"))
        patch = importlib.import_module("mhr.patches.v1_0.add_serial_batch_bundle_status_index")
        patch.execute()
        patch.execute()
        for table, index_name, columns in patch.INDEXES:
            self.assertTrue(patch.index_exists(table, index_name), index_name)
            self.assertEqual(frappe.db.sql("""SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics
                                              WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s""",
                                           (table, index_name))[0][0], ",".join(columns))
        self.assertEqual([i[1] for i in patch.INDEXES], ["idx_sbb_name_status", "idx_sbe_batch_cover"])

    def test_join_uses_the_covering_index_when_present(self):
        r = _report()
        frappe.local._mhr_sbb_status_index = None
        self.assertEqual(r._sbb_status_index_hint(), f"FORCE INDEX (`{r.SBB_STATUS_INDEX}`)")
        src = inspect.getsource(r.get_batch_warehouse_balances)
        self.assertIn("{hint}", src)
        self.assertIn("HAVING ABS(SUM(sbe.qty)) > 0.0005", src, "zero-sum pairs stay in the database")
        self.assertEqual(r.BALANCE_CHUNK, 5000)

    def test_only_stocked_batches_are_loaded_in_full(self):
        r = _report()
        src = inspect.getsource(r.get_data)
        self.assertIn("batch_ids = query.run(pluck=\"batch_id\")", src)
        self.assertIn("batches = get_batch_rows(stocked_ids)", src)
        self.assertIn("booked_map = get_booked_quantities(stocked_ids)", src)

    def test_inline_patch_resets_prepared_report(self):
        frappe.db.set_value("Report", "STOCK SHEET (BALANCE REPORT)", "prepared_report", 1, update_modified=False)
        importlib.import_module("mhr.patches.v1_0.set_stock_sheet_balance_report_inline").execute()
        self.assertEqual(frappe.db.get_value("Report", "STOCK SHEET (BALANCE REPORT)", "prepared_report"), 0)


class TestFetchBatchesNumericWindow(FrappeTestCase):
    """MI1-I124."""

    CONTAINER = "MIJAM-07"

    def test_scan_orders_numerically(self):
        src = inspect.getsource(note.fetch_batches)
        self.assertIn('order_by="CAST(custom_supplier_batch_no AS UNSIGNED) asc, custom_supplier_batch_no asc, name asc"', src)

    def test_window_is_the_numerically_smallest(self):
        if not frappe.db.exists("Batch", {"custom_container_no": self.CONTAINER}):
            self.skipTest("Container with three-digit supplier batches not on this bench.")
        numeric = frappe.get_all("Batch", filters={"custom_container_no": self.CONTAINER, "disabled": 0},
                                 fields=["custom_supplier_batch_no"],
                                 order_by="CAST(custom_supplier_batch_no AS UNSIGNED) asc, custom_supplier_batch_no asc, name asc",
                                 limit=12)
        values = [int(x.custom_supplier_batch_no) for x in numeric if str(x.custom_supplier_batch_no).isdigit()]
        self.assertEqual(values, sorted(values))
        smallest = frappe.db.sql("SELECT MIN(CAST(custom_supplier_batch_no AS UNSIGNED)) FROM `tabBatch` WHERE custom_container_no = %s AND disabled = 0",
                                 (self.CONTAINER,))[0][0]
        self.assertEqual(values[0], int(smallest))
        text = frappe.get_all("Batch", filters={"custom_container_no": self.CONTAINER, "disabled": 0},
                              fields=["custom_supplier_batch_no"], order_by="custom_supplier_batch_no asc", limit=12)
        self.assertNotEqual([x.custom_supplier_batch_no for x in text], [x.custom_supplier_batch_no for x in numeric],
                            "the text order is the bug; the two windows must differ on this container")


class TestWholeSiteBalanceMap(FrappeTestCase):
    """MI1-I119 follow-up (prod 2026-09-08): the full-range run took 16 s on
    prod and frappe flipped the report back into prepared mode; every HTY run
    died sorting int cones against blank ones."""

    def test_cache_key_is_content_addressed(self):
        r = _report()
        key = r.balance_cache_key()
        sbb_count, sbb_modified = frappe.db.sql("SELECT COUNT(*), MAX(modified) FROM `tabSerial and Batch Bundle`")[0]
        sle_modified = frappe.db.sql("SELECT MAX(modified) FROM `tabStock Ledger Entry`")[0][0]
        self.assertEqual(key, f"{r.BALANCE_CACHE_KEY}:{sbb_count}:{sbb_modified}:{sle_modified}:"
                              + "|".join(sorted(r.get_external_job_work_warehouses())))
        self.assertEqual(r.balance_cache_key(), key, "stable while nothing moves")

    def test_whole_site_map_equals_the_chunked_aggregate_and_caches(self):
        r = _report()
        frappe.local._mhr_all_warehouse_balances = None
        frappe.cache().delete_value(r.balance_cache_key())
        fresh = r.get_all_warehouse_balances()
        self.assertTrue(fresh)
        sample = list(fresh)[:50]
        direct = r.get_batch_warehouse_balances([b for b, _w in sample])
        for k in sample:
            self.assertAlmostEqual(fresh[k], direct[k], places=3)
        # Second call: served from Redis (a fresh request), same content.
        frappe.local._mhr_all_warehouse_balances = None
        cached = r.get_all_warehouse_balances()
        self.assertEqual(len(cached), len(fresh))
        self.assertEqual(cached[sample[0]], fresh[sample[0]])
        self.assertIsNotNone(r._read_cached_map(r.balance_cache_key()))

    def test_use_cache_false_never_touches_redis(self):
        r = _report()
        frappe.local._mhr_all_warehouse_balances = None
        frappe.cache().delete_value(r.balance_cache_key())
        r.get_all_warehouse_balances(use_cache=False)
        self.assertIsNone(r._read_cached_map(r.balance_cache_key()))

    def test_full_range_and_narrow_paths_agree(self):
        """The same lot through both code paths — whole-site map filtered in
        Python versus names-first — must render identically."""
        r = _report()
        b = _pick_stocked_batch()
        if not b:
            self.skipTest("No stocked batch.")
        narrow = [x for x in r.execute({"container": b.custom_container_no, "lot_no": b.custom_lot_no})[1] if x["sort_order"] == 0]
        wide = [x for x in r.execute({})[1] if x["sort_order"] == 0 and x["Container Number"] == b.custom_container_no
                and x["Lot Number"] == b.custom_lot_no]
        key = lambda x: (x["Item"], x["Cone"], x["Grade"], x["Sales Order"])
        self.assertEqual(sorted(map(key, narrow)), sorted(map(key, wide)))
        for n, w in zip(sorted(narrow, key=key), sorted(wide, key=key)):
            for col in ("Balance", "Balance Box", "Accepted Warehouse", "Booked Qty", "Available Qty"):
                self.assertEqual(n[col], w[col], col)

    def test_hty_lot_with_coned_and_coneless_rows_sorts(self):
        """Prod Prepared Report 3jhahn82je: TypeError '<' between int and str."""
        r = _report()
        mixed = frappe.db.sql("""SELECT custom_container_no FROM `tabBatch` WHERE custom_transaction_type = 'HTY'
                                 GROUP BY custom_container_no, custom_lot_no
                                 HAVING SUM(IFNULL(custom_cone, 0) = 0) > 0 AND SUM(IFNULL(custom_cone, 0) > 0) > 0 LIMIT 1""")
        if not mixed:
            self.skipTest("No HTY lot with both coned and coneless batches on this bench.")
        cols, data = r.execute({"container": mixed[0][0], "transaction_type": "HTY"})
        self.assertIsInstance(data, list)
        rows = [{"batch_date": frappe.utils.getdate("2026-01-01"), "container_no": "C", "lot_no": "L", "sort_order": 0, "cone": 12},
                {"batch_date": frappe.utils.getdate("2026-01-01"), "container_no": "C", "lot_no": "L", "sort_order": 0, "cone": ""},
                {"batch_date": frappe.utils.getdate("2026-01-01"), "container_no": "C", "lot_no": "L", "sort_order": 1, "cone": ""}]
        rows.sort(key=lambda x: (-x["batch_date"].toordinal(), x["container_no"], x["lot_no"], x["sort_order"], frappe.utils.cint(x["cone"])))
        self.assertEqual([x["cone"] for x in rows], ["", 12, ""])

    def test_inline_patch_re_registered(self):
        with open(PATCHES) as f:
            lines = [l.strip() for l in f if l.strip()]
        self.assertIn("mhr.patches.v1_0.set_stock_sheet_balance_report_inline #2026-09-08", lines,
                      "the 2026-09-06 run was undone by frappe's 15 s watcher; a new line re-runs the reset")


class TestBalanceCacheWarmup(FrappeTestCase):

    def test_hooks_queue_a_warmup_on_every_stock_movement(self):
        events = frappe.get_hooks("doc_events")
        target = "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report).enqueue_balance_cache_warmup"
        for dt in ("Stock Entry", "Delivery Note", "Purchase Receipt", "Stock Reconciliation"):
            for ev in ("on_submit", "on_cancel"):
                self.assertIn(target, events[dt].get(ev, []), f"{dt}.{ev}")
        self.assertIn("mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report).warm_balance_cache",
                      frappe.get_hooks("scheduler_events")["hourly"])

    def test_warm_builds_once_then_reports_warm(self):
        r = _report()
        frappe.local._mhr_all_warehouse_balances = None
        frappe.cache().delete_value(r.balance_cache_key())
        self.assertEqual(r.warm_balance_cache(), "built")
        frappe.local._mhr_all_warehouse_balances = None
        self.assertEqual(r.warm_balance_cache(), "warm")

    def test_enqueue_is_deduplicated_and_never_raises(self):
        r = _report()
        import inspect
        src = inspect.getsource(r.enqueue_balance_cache_warmup)
        self.assertIn("deduplicate=True", src)
        self.assertIn("job_id=WARM_JOB_ID", src)
        self.assertIn("enqueue_after_commit=True", src)
        r.enqueue_balance_cache_warmup(frappe._dict(name="x"))  # must not raise


class TestKeepInline(FrappeTestCase):
    """frappe's 15 s watcher flips the report into prepared mode during a
    slow run; a run that started inline undoes that flip when it finishes."""

    def test_flip_during_an_inline_run_is_undone(self):
        r = _report()
        frappe.db.set_value("Report", r.REPORT_NAME, "prepared_report", 1, update_modified=False)
        r.keep_inline(started_inline=True)
        self.assertEqual(frappe.db.get_value("Report", r.REPORT_NAME, "prepared_report"), 0)

    def test_a_deliberate_prepared_setting_is_respected(self):
        r = _report()
        frappe.db.set_value("Report", r.REPORT_NAME, "prepared_report", 1, update_modified=False)
        r.keep_inline(started_inline=False)
        self.assertEqual(frappe.db.get_value("Report", r.REPORT_NAME, "prepared_report"), 1)
        frappe.db.set_value("Report", r.REPORT_NAME, "prepared_report", 0, update_modified=False)

    def test_execute_calls_it(self):
        import inspect
        r = _report()
        src = inspect.getsource(r.execute)
        self.assertIn("started_inline = _runs_inline()", src)
        self.assertIn("keep_inline(started_inline)", src)
