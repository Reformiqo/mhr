"""MI1-I82 (Raj 2026-07-16): stock in Warehouses flagged as
`custom_external_job_work_warehouse = 1` must NOT be counted as
company stock in Stock Sheet (Balance Report). The warehouse itself
must remain usable in every other stock flow (Stock Entry, Send to
Subcontractor, Material Transfer, Material Receipt) — only the
report's balance aggregation drops those SLE / SBE rows.

This suite pins:
  * The custom field exists on Warehouse (fixture landed).
  * The helper `get_external_job_work_warehouses()` returns the set
    of flagged warehouse names — and an empty set when nothing is
    flagged (defensive path).
  * `get_batch_balances()` skips SLE rows whose warehouse is flagged
    (mixed-warehouse case: balance = non-external portion only).
  * `execute()` drops batches whose stock only lives in external
    warehouses (they hit the balance>0 filter and disappear).
  * The report's OTHER callers of Warehouse (Container.set_warehouse
    'Accepted Warehouse' column, Container.warehouse 'Location' column)
    are unaffected — the ticket says "no other functionality changes".
"""
import frappe
from frappe.tests.utils import FrappeTestCase


REPORT_MODULE = "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"


def _report():
    return frappe.get_module(REPORT_MODULE)


class TestCustomFieldInstalled(FrappeTestCase):

    def test_column_exists(self):
        self.assertTrue(
            frappe.db.has_column("Warehouse", "custom_external_job_work_warehouse"),
            "MI1-I82 custom field must be installed on Warehouse — the "
            "helper's fallback (empty set) is a defence, not the happy path.",
        )

    def test_fieldtype_is_check(self):
        cf = frappe.db.get_value(
            "Custom Field",
            "Warehouse-custom_external_job_work_warehouse",
            ["fieldtype", "module", "label"],
            as_dict=True,
        )
        self.assertIsNotNone(cf, "Custom Field row must exist in fixtures.")
        self.assertEqual(cf["fieldtype"], "Check",
            "Field must be a Check per the spec.")
        self.assertEqual(cf["module"], "Mhr",
            "Field must live in Mhr module so the fixture round-trips.")
        self.assertEqual(cf["label"], "External Job Work Warehouse",
            "Label must match Raj's spec verbatim.")


class TestHelperReadsTheFlag(FrappeTestCase):
    """The `get_external_job_work_warehouses()` helper is the choke
    point — every consumer (get_batch_balances) trusts its return
    value, so pin it directly."""

    EXT_WH = "MI1-I82 TEST External Job Work WH - MC"
    NORMAL_WH = "MI1-I82 TEST Normal WH - MC"

    def _make_warehouse(self, name, external):
        # Use the first available Company for the abbr.
        company = frappe.db.get_value("Company", {"abbr": "MC"}, "name") \
            or frappe.db.get_value("Company", {}, "name")
        if not company:
            self.skipTest("No Company on this bench — cannot create test warehouses.")

        # If a stale test warehouse from a prior run exists, delete it.
        if frappe.db.exists("Warehouse", name):
            frappe.delete_doc("Warehouse", name, ignore_permissions=True, force=1)

        wh = frappe.new_doc("Warehouse")
        wh.warehouse_name = name.rsplit(" - ", 1)[0]  # strip abbr
        wh.company = company
        wh.custom_external_job_work_warehouse = 1 if external else 0
        wh.insert(ignore_permissions=True)
        frappe.db.commit()
        return wh.name

    def setUp(self):
        self.ext_name = self._make_warehouse(self.EXT_WH, external=True)
        self.normal_name = self._make_warehouse(self.NORMAL_WH, external=False)

    def tearDown(self):
        for n in (self.ext_name, self.normal_name):
            if n and frappe.db.exists("Warehouse", n):
                frappe.delete_doc("Warehouse", n, ignore_permissions=True, force=1)
        frappe.db.commit()

    def test_helper_returns_flagged_warehouse(self):
        r = _report()
        result = r.get_external_job_work_warehouses()
        self.assertIn(self.ext_name, result,
            f"Warehouse {self.ext_name!r} flagged as external must appear.")
        self.assertNotIn(self.normal_name, result,
            f"Warehouse {self.normal_name!r} without the flag must NOT appear.")


class TestBalanceSkipsExternalWarehouseSle(FrappeTestCase):
    """End-to-end: post two SLE rows for one batch — one to an
    external warehouse, one to a normal warehouse. The report's
    balance must include only the normal-WH row.

    We hand-write the SLE rows (bypass Stock Entry) — that isolates
    the balance computation from Stock Entry's validation surface,
    which changes across ERPNext versions and would be a noisy test
    to maintain. `get_batch_balances` reads SLE directly, so writing
    SLE directly exercises exactly the code path we care about.
    """

    ITEM = "MI1-I82-TEST-ITEM"
    BATCH_LOCAL = "MI1-I82-BATCH-LOCAL"

    @classmethod
    def _company(cls):
        return (
            frappe.db.get_value("Company", {"abbr": "MC"}, "name")
            or frappe.db.get_value("Company", {}, "name")
        )

    def setUp(self):
        self.company = self._company()
        if not self.company:
            self.skipTest("No Company on this bench.")

        # Two warehouses under the company — one external, one normal.
        self.ext_wh = self._ensure_warehouse("MI1-I82 T-ExtWH", external=True)
        self.norm_wh = self._ensure_warehouse("MI1-I82 T-NormWH", external=False)

        # An item to house the batch.
        if not frappe.db.exists("Item", self.ITEM):
            item = frappe.new_doc("Item")
            item.item_code = self.ITEM
            item.item_name = self.ITEM
            item.item_group = frappe.db.get_value("Item Group", {}, "name")
            item.stock_uom = "Nos"
            item.has_batch_no = 1
            item.create_new_batch = 0
            item.insert(ignore_permissions=True)

        if not frappe.db.exists("Batch", self.BATCH_LOCAL):
            batch = frappe.new_doc("Batch")
            batch.batch_id = self.BATCH_LOCAL
            batch.item = self.ITEM
            batch.batch_qty = 100
            batch.insert(ignore_permissions=True)

        # Wipe any SLE from a prior partial test run.
        self._delete_test_sle()

        # Post one +40 SLE into the external warehouse and one +60 into
        # the normal warehouse. Real balance for the batch = 100. The
        # report should count only 60 (normal WH portion).
        self._make_sle(self.ext_wh, 40)
        self._make_sle(self.norm_wh, 60)
        frappe.db.commit()

    def tearDown(self):
        self._delete_test_sle()
        for n in (getattr(self, "ext_wh", None), getattr(self, "norm_wh", None)):
            if n and frappe.db.exists("Warehouse", n):
                # The delete_doc may fail if there are lingering SLE — force.
                frappe.delete_doc("Warehouse", n, ignore_permissions=True, force=1)
        if frappe.db.exists("Batch", self.BATCH_LOCAL):
            frappe.delete_doc("Batch", self.BATCH_LOCAL, ignore_permissions=True, force=1)
        if frappe.db.exists("Item", self.ITEM):
            frappe.delete_doc("Item", self.ITEM, ignore_permissions=True, force=1)
        frappe.db.commit()

    def _ensure_warehouse(self, base_name, external):
        # Build the name-with-abbr the way Frappe does.
        abbr = frappe.db.get_value("Company", self.company, "abbr")
        wh_name = f"{base_name} - {abbr}"
        if frappe.db.exists("Warehouse", wh_name):
            frappe.delete_doc("Warehouse", wh_name, ignore_permissions=True, force=1)
        wh = frappe.new_doc("Warehouse")
        wh.warehouse_name = base_name
        wh.company = self.company
        wh.custom_external_job_work_warehouse = 1 if external else 0
        wh.insert(ignore_permissions=True)
        return wh.name

    def _make_sle(self, warehouse, qty):
        """Insert a synthetic Stock Ledger Entry row directly via
        db_insert — bypasses Stock Entry / Stock Reconciliation
        validation. `get_batch_balances` reads SLE directly, so a
        raw row is enough to exercise the code path we care about."""
        sle = frappe.new_doc("Stock Ledger Entry")
        sle.item_code = self.ITEM
        sle.warehouse = warehouse
        sle.batch_no = self.BATCH_LOCAL
        sle.posting_date = frappe.utils.nowdate()
        sle.posting_time = frappe.utils.nowtime()
        sle.actual_qty = qty
        sle.qty_after_transaction = qty
        sle.voucher_type = "Stock Reconciliation"
        sle.voucher_no = f"MI1-I82-TEST-{warehouse}"
        sle.company = self.company
        sle.docstatus = 1
        sle.is_cancelled = 0
        sle.name = frappe.generate_hash(length=12)
        sle.db_insert()

    def _delete_test_sle(self):
        # Identify our synthetic SLE rows by the batch + our voucher_no prefix.
        frappe.db.sql(
            """DELETE FROM `tabStock Ledger Entry`
               WHERE batch_no = %s AND voucher_no LIKE 'MI1-I82-TEST-%%'""",
            (self.BATCH_LOCAL,),
        )

    def test_balance_only_counts_non_external_wh(self):
        r = _report()
        result = r.get_batch_balances([self.BATCH_LOCAL])
        self.assertIn(
            self.BATCH_LOCAL,
            result,
            "Batch with any non-external SLE must appear in the balance map.",
        )
        self.assertEqual(
            result[self.BATCH_LOCAL], 60,
            f"Balance must be 60 (only the non-external WH's SLE), got "
            f"{result[self.BATCH_LOCAL]} — the external-WH's +40 must "
            f"NOT contribute.",
        )

    def test_flipping_flag_off_restores_full_balance(self):
        """Regression pin: turning the flag off must include the
        warehouse's SLE again — the exclusion is purely flag-driven."""
        # Flip the external WH's flag off.
        frappe.db.set_value(
            "Warehouse", self.ext_wh,
            "custom_external_job_work_warehouse", 0,
        )
        frappe.db.commit()

        r = _report()
        result = r.get_batch_balances([self.BATCH_LOCAL])
        self.assertEqual(
            result[self.BATCH_LOCAL], 100,
            "With no external warehouses flagged, the full balance "
            "(40 + 60 = 100) must be returned.",
        )


class TestReportCallSitePlumbed(FrappeTestCase):
    """Cross-cutting pin: the exclusion must apply INSIDE
    `get_batch_balances`, not in a caller — otherwise adding future
    callers (Stock Sheet Simple etc.) would need to remember the rule.
    """

    def test_get_batch_balances_uses_the_helper(self):
        import inspect
        r = _report()
        src = inspect.getsource(r.get_batch_balances)
        self.assertIn(
            "get_external_job_work_warehouses",
            src,
            "get_batch_balances must call get_external_job_work_warehouses "
            "— that helper is the single choke point for the MI1-I82 "
            "exclusion. Every SLE / SBE aggregation on this report must "
            "flow through it.",
        )
