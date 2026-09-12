"""MI1-I135 (Raj 2026-09-10) — "Create a new stock sheet report": an exact
replica of Stock Sheet (Balance Report), as a SEPARATE report, with the
changes from MHR_Stock_Sheet_Book2_Reviewed_v3.0.xlsx (the FRD, reviewed and
corrected to v3.0 by Reformiqo's own analyst before this was built):

  * Four new columns — In Qty, Out Qty, GR Received, Job work Send Qty —
    immediately after Glue and before Balance Qty.
  * Balance Qty itself: In Qty - Out Qty + GR Received - Job work Send Qty
    (the FRD's sheet 1 marks this formula "accepted as given"), replacing
    the live Serial and Batch Bundle balance the original report uses.
  * Everything else — Booked Qty, Available Qty (Balance minus Booked, now
    fed by the new Balance), Delivered/Pending, Accepted Warehouse,
    HTY/VFY column swaps, per-Sales-Order row expansion, lot/container/
    grand totals — behaves exactly as the original, which is untouched.

Real documents throughout, built directly (skipping ERPNext's full stock-
transaction validation the way the existing Container tests already do,
mhr.tests.test_dn_notes_from_container_mi1_i83) — the movement query reads
child-row fields (item_code, qty, custom_cone, s_warehouse/t_warehouse,
custom_container_no/custom_lot_no) straight off the tables, independent of
whether a document's GL / stock-ledger side effects were ever posted.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis

CONTAINER_NO = "MI1I135-TEST"
LOT_NO = "L1"
ITEM = "MI1I135-TEST-ITEM"
CONE = 6
COMPANY = "Meher Creations"

REPORT_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "report.json")


def _report_fixture_entry():
    with open(REPORT_FIXTURE) as f:
        for r in json.load(f):
            if r["name"] == "STOCK SHEET (BALANCE REPORT) v2":
                return r


def _get_module():
    import importlib
    return importlib.import_module(
        "mhr.mhr.report.stock_sheet_(balance_report)_v2.stock_sheet_(balance_report)_v2"
    )


def _get_original_module():
    import importlib
    return importlib.import_module(
        "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"
    )


class TestReportIsRegistered(FrappeTestCase):

    def test_report_doc_exists_correctly_typed(self):
        doc = frappe.db.get_value(
            "Report", "STOCK SHEET (BALANCE REPORT) v2",
            ["report_type", "module", "is_standard", "ref_doctype"], as_dict=True,
        )
        self.assertIsNotNone(doc, "STOCK SHEET (BALANCE REPORT) v2 Report record missing")
        self.assertEqual(doc.report_type, "Script Report")
        self.assertEqual(doc.module, "Mhr")
        self.assertEqual(doc.is_standard, "Yes")
        self.assertEqual(doc.ref_doctype, "Batch")

    def test_fixture_carries_the_new_report(self):
        entry = _report_fixture_entry()
        self.assertIsNotNone(entry, "new report missing from fixtures/report.json")
        self.assertEqual(entry["report_type"], "Script Report")

    def test_original_report_fixture_entry_is_still_present_and_unrenamed(self):
        with open(REPORT_FIXTURE) as f:
            names = [r["name"] for r in json.load(f)]
        self.assertIn("STOCK SHEET (BALANCE REPORT)", names)
        self.assertEqual(names.count("STOCK SHEET (BALANCE REPORT)"), 1)


class TestColumnsMatchOriginalPlusFour(FrappeTestCase):

    def test_four_new_columns_after_glue_before_balance(self):
        m = _get_module()
        cols = [c["fieldname"] for c in m.get_columns({})]
        glue_i = cols.index("Glue")
        balance_i = cols.index("Balance")
        self.assertEqual(
            cols[glue_i + 1 : balance_i],
            ["In Qty", "Out Qty", "GR Received", "Job work Send Qty"],
        )

    def test_removing_the_four_new_columns_reproduces_the_original_exactly(self):
        m = _get_module()
        orig = _get_original_module()
        v2_cols = [c["fieldname"] for c in m.get_columns({})]
        stripped = [c for c in v2_cols if c not in
                    ("In Qty", "Out Qty", "GR Received", "Job work Send Qty")]
        self.assertEqual(stripped, [c["fieldname"] for c in orig.get_columns({})])

    def test_hty_mode_still_drops_merge_no_and_cross_section(self):
        """The original's HTY/VFY swap must survive untouched underneath
        the spliced-in columns."""
        m = _get_module()
        hty_cols = [c["fieldname"] for c in m.get_columns({"transaction_type": "HTY"})]
        self.assertNotIn("Merge No", hty_cols)
        self.assertNotIn("Cross Section", hty_cols)
        self.assertIn("In Qty", hty_cols)


class TestMovementKeyNormalisation(FrappeTestCase):

    def test_item_code_case_is_normalised(self):
        m = _get_module()
        self.assertEqual(
            m._movement_key("C1", "50d/8f", "L1", 6),
            m._movement_key("C1", "50D/8F", "L1", 6),
        )

    def test_cone_type_is_normalised(self):
        m = _get_module()
        self.assertEqual(m._movement_key("C1", "X", "L1", "6"), m._movement_key("C1", "X", "L1", 6))

    def test_blank_cone_becomes_zero(self):
        m = _get_module()
        self.assertEqual(m._movement_key("C1", "X", "L1", "")[3], 0)
        self.assertEqual(m._movement_key("C1", "X", "L1", None)[3], 0)


class TestMovementTotalsAndEndToEnd(FrappeTestCase):
    """One controlled scenario exercising all five movement sources with
    known numbers, verified both at the get_movement_totals() level and
    through the full rendered get_data() output."""

    IN_INWARD = 100.0     # Container Inward, batch A
    IN_INWARD_B = 50.0    # Container Inward, batch B
    IN_JWR = 20.0         # Job Work Received produce row
    OUT_QTY = 40.0        # non-return delivery
    RETURN_QTY = 10.0     # return delivery (stored negative on the row)
    SEND_QTY = 30.0       # Send to Subcontractor issue row

    # In Qty = 100 + 50 + 20 = 170; Out Qty = 40; GR Received = 10 (ABS);
    # Job work Send Qty = 30. Balance = 170 - 40 + 10 - 30 = 110.
    # In Box = 3 (2 inward batches + 1 JWR row); Out Box = 1; GR Box = 1;
    # JW Box = 1. Balance Box = 3 - 1 + 1 - 1 = 2.
    EXPECTED_IN = 170.0
    EXPECTED_OUT = 40.0
    EXPECTED_GR = 10.0
    EXPECTED_JW = 30.0
    EXPECTED_BALANCE = 110.0
    EXPECTED_BALANCE_BOX = 2

    @classmethod
    def _cleanup_stale(cls):
        """Idempotent: a prior run that crashed before its own tearDownClass
        (or a manual debugging call to setUpClass alone) can leave a
        Delivery Note or Stock Entry behind that still matches this
        container/lot — silently inflating every movement total on the next
        run. Sweep everything touching CONTAINER_NO / LOT_NO first."""
        for r in frappe.db.sql(
            "SELECT DISTINCT dn.name FROM `tabDelivery Note` dn "
            "JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name "
            "WHERE dni.custom_container_no = %s", (CONTAINER_NO,), as_dict=True,
        ):
            frappe.db.sql("DELETE FROM `tabDelivery Note Item` WHERE parent=%s", (r.name,))
            frappe.db.sql("DELETE FROM `tabDelivery Note` WHERE name=%s", (r.name,))
        for r in frappe.db.sql(
            "SELECT DISTINCT se.name FROM `tabStock Entry` se "
            "WHERE se.custom_container_number = %s OR se.custom_received_container_no = %s",
            (CONTAINER_NO, CONTAINER_NO), as_dict=True,
        ):
            frappe.db.sql("DELETE FROM `tabStock Entry Detail` WHERE parent=%s", (r.name,))
            frappe.db.sql("DELETE FROM `tabStock Entry` WHERE name=%s", (r.name,))
        for r in frappe.get_all("Container", filters={"container_no": CONTAINER_NO}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabBatch Items` WHERE parent=%s", (r,))
            frappe.db.sql("DELETE FROM `tabContainer` WHERE name=%s", (r,))
        frappe.db.sql("DELETE FROM `tabBatch` WHERE custom_container_no=%s", (CONTAINER_NO,))
        frappe.db.commit()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.docs = []
        cls._cleanup_stale()

        if not frappe.db.exists("Item", ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Products",
                "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 0,
            }).insert(ignore_permissions=True)

        cls.container = cls._make_container()
        cls.batch_a = cls._make_batch("MI1I135-TEST-A", cls.IN_INWARD)
        cls.batch_b = cls._make_batch("MI1I135-TEST-B", cls.IN_INWARD_B)
        cls.dn_out = cls._make_delivery(cls.OUT_QTY, is_return=0)
        cls.dn_return = cls._make_delivery(-cls.RETURN_QTY, is_return=1)
        cls.se_send = cls._make_stock_entry_send()
        cls.se_jwr = cls._make_stock_entry_jwr()
        frappe.db.commit()

    CHILD_TABLE = {
        "Delivery Note": "tabDelivery Note Item",
        "Stock Entry": "tabStock Entry Detail",
    }

    @classmethod
    def tearDownClass(cls):
        for doctype, name in reversed(cls.docs):
            child = cls.CHILD_TABLE.get(doctype)
            if child:
                frappe.db.sql(f"DELETE FROM `{child}` WHERE parent=%s", (name,))
            frappe.db.sql(f"DELETE FROM `tab{doctype}` WHERE name=%s", (name,))
        frappe.db.sql("DELETE FROM `tabBatch Items` WHERE parent=%s", (cls.container,))
        frappe.db.commit()
        super().tearDownClass()

    @classmethod
    def _make_container(cls):
        # Container's own autoname (container_no + a running counter)
        # overrides any name set before insert(), so stale rows from a
        # prior run are found by container_no, not by a guessed name.
        for stale in frappe.get_all("Container", filters={"container_no": CONTAINER_NO}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabBatch Items` WHERE parent=%s", (stale,))
            frappe.db.sql("DELETE FROM `tabContainer` WHERE name=%s", (stale,))
        c = frappe.new_doc("Container")
        c.container_no = CONTAINER_NO
        c.lot_no = LOT_NO
        c.item = ITEM
        c.transaction_type = "VFY"
        c.posting_date = frappe.utils.nowdate()
        c.append("batches", {"batch_id": "MI1I135-TEST-A", "item": ITEM, "qty": cls.IN_INWARD, "cone": CONE})
        c.append("batches", {"batch_id": "MI1I135-TEST-B", "item": ITEM, "qty": cls.IN_INWARD_B, "cone": CONE})
        c.flags.ignore_validate = True
        c.flags.ignore_mandatory = True
        c.insert(ignore_permissions=True)
        frappe.db.set_value("Container", c.name, "docstatus", 1, update_modified=False)
        cls.docs.append(("Container", c.name))
        return c.name

    @classmethod
    def _make_batch(cls, batch_id, qty):
        if frappe.db.exists("Batch", batch_id):
            frappe.db.sql("DELETE FROM `tabBatch` WHERE name=%s", (batch_id,))
        b = frappe.new_doc("Batch")
        b.batch_id = batch_id
        b.item = ITEM
        b.custom_container_no = CONTAINER_NO
        b.custom_lot_no = LOT_NO
        b.custom_cone = CONE
        b.custom_transaction_type = "VFY"
        b.flags.ignore_mandatory = True
        b.insert(ignore_permissions=True)
        cls.docs.append(("Batch", b.name))
        return b.name

    @classmethod
    def _make_delivery(cls, qty, is_return):
        dn = frappe.new_doc("Delivery Note")
        dn.customer = frappe.db.get_value("Customer", {}, "name")
        dn.company = COMPANY
        dn.is_return = is_return
        dn.posting_date = frappe.utils.nowdate()
        dn.set_posting_time = 1
        dn.append("items", {
            "item_code": ITEM, "qty": qty, "rate": 10,
            "custom_container_no": CONTAINER_NO, "custom_lot_no": LOT_NO, "custom_cone": CONE,
        })
        dn.flags.ignore_validate = True
        dn.flags.ignore_mandatory = True
        dn.insert(ignore_permissions=True)
        frappe.db.set_value("Delivery Note", dn.name, "docstatus", 1, update_modified=False)
        cls.docs.append(("Delivery Note", dn.name))
        return dn.name

    @classmethod
    def _make_stock_entry_send(cls):
        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Send to Subcontractor"
        se.purpose = "Send to Subcontractor"
        se.company = COMPANY
        se.posting_date = frappe.utils.nowdate()
        se.set_posting_time = 1
        se.custom_container_number = CONTAINER_NO
        se.custom_lot_no = LOT_NO
        se.append("items", {
            "item_code": ITEM, "qty": cls.SEND_QTY,
            "s_warehouse": "Finished Goods - MC", "custom_cone": CONE,
        })
        se.flags.ignore_validate = True
        se.flags.ignore_mandatory = True
        se.insert(ignore_permissions=True)
        frappe.db.set_value("Stock Entry", se.name, "docstatus", 1, update_modified=False)
        cls.docs.append(("Stock Entry", se.name))
        return se.name

    @classmethod
    def _make_stock_entry_jwr(cls):
        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Job Work Received"
        se.purpose = "Material Transfer"
        se.company = COMPANY
        se.posting_date = frappe.utils.nowdate()
        se.set_posting_time = 1
        se.custom_received_container_no = CONTAINER_NO
        se.custom_received_lot_no = LOT_NO
        se.append("items", {
            "item_code": ITEM, "qty": cls.IN_JWR,
            "t_warehouse": "Finished Goods - MC", "custom_cone": CONE,
        })
        se.flags.ignore_validate = True
        se.flags.ignore_mandatory = True
        se.insert(ignore_permissions=True)
        frappe.db.set_value("Stock Entry", se.name, "docstatus", 1, update_modified=False)
        cls.docs.append(("Stock Entry", se.name))
        return se.name

    def test_get_movement_totals_matches_expected_numbers(self):
        m = _get_module()
        mv = m.get_movement_totals(container=CONTAINER_NO, lot_no=LOT_NO)
        key = m._movement_key(CONTAINER_NO, ITEM, LOT_NO, CONE)
        self.assertIn(key, mv)
        t = mv[key]
        self.assertAlmostEqual(t["in_qty"], self.EXPECTED_IN, places=3)
        self.assertAlmostEqual(t["out_qty"], self.EXPECTED_OUT, places=3)
        self.assertAlmostEqual(t["gr_qty"], self.EXPECTED_GR, places=3,
                               msg="return qty is stored negative; must be ABS()'d")
        self.assertAlmostEqual(t["jw_qty"], self.EXPECTED_JW, places=3)
        self.assertEqual(t["in_box"], 3)
        self.assertEqual(t["out_box"], 1)
        self.assertEqual(t["gr_box"], 1)
        self.assertEqual(t["jw_box"], 1)

    def test_container_only_filter_narrows_correctly(self):
        m = _get_module()
        mv = m.get_movement_totals(container=CONTAINER_NO)
        key = m._movement_key(CONTAINER_NO, ITEM, LOT_NO, CONE)
        self.assertIn(key, mv)

    def test_unrelated_container_never_pollutes_the_key(self):
        m = _get_module()
        mv = m.get_movement_totals(container=CONTAINER_NO, lot_no=LOT_NO)
        for (c, i, l, cn) in mv:
            self.assertEqual(c, CONTAINER_NO)
            self.assertEqual(l, LOT_NO)

    def test_end_to_end_row_shows_the_movement_ledger_balance(self):
        m = _get_module()
        rows = m.get_data({"container": CONTAINER_NO, "lot_no": LOT_NO})
        detail = [r for r in rows if r["sort_order"] == 0]
        self.assertEqual(len(detail), 1, "one stock group, no Sales Order bookings here")
        row = detail[0]
        self.assertEqual(row["Container Number"], CONTAINER_NO)
        self.assertEqual(row["Lot Number"], LOT_NO)
        self.assertAlmostEqual(row["In Qty"], self.EXPECTED_IN, places=2)
        self.assertAlmostEqual(row["Out Qty"], self.EXPECTED_OUT, places=2)
        self.assertAlmostEqual(row["GR Received"], self.EXPECTED_GR, places=2)
        self.assertAlmostEqual(row["Job work Send Qty"], self.EXPECTED_JW, places=2)
        self.assertAlmostEqual(row["Balance"], self.EXPECTED_BALANCE, places=2)
        self.assertEqual(row["Balance Box"], self.EXPECTED_BALANCE_BOX)
        self.assertAlmostEqual(row["Available Qty"], self.EXPECTED_BALANCE, places=2,
                               msg="no bookings on this container, so Available Qty = Balance")

    def test_movement_columns_render_clean_not_float_noise(self):
        """Real walkthrough bug (MI1-I135): SUM() over many decimal-weight
        Batch Items rows returns values like 3044.0000000000027 — In Qty /
        Out Qty / GR Received / Job work Send Qty must be rounded the same
        way Balance already is, not shown as raw float noise on screen."""
        m = _get_module()
        rows = m.get_data({"container": CONTAINER_NO, "lot_no": LOT_NO})
        detail = [r for r in rows if r["sort_order"] == 0]
        for col in ("In Qty", "Out Qty", "GR Received", "Job work Send Qty"):
            value = detail[0][col]
            self.assertEqual(value, round(value, 2), f"{col} carries unrounded float noise: {value!r}")

    def test_grand_total_matches_the_single_detail_row_not_doubled(self):
        m = _get_module()
        rows = m.get_data({"container": CONTAINER_NO, "lot_no": LOT_NO})
        total = next(r for r in rows if r["sort_order"] == 3)
        self.assertAlmostEqual(total["In Qty"], self.EXPECTED_IN, places=2)
        self.assertAlmostEqual(total["Balance"], self.EXPECTED_BALANCE, places=2)
        self.assertEqual(total["Balance Box"], self.EXPECTED_BALANCE_BOX)

    def test_passthrough_fields_match_a_pure_inward_container_exactly(self):
        """A second, simpler container with ONLY Container Inward movement —
        Balance under the new formula equals Balance under the live SBB
        balance (nothing has ever left), so every field but the four new
        movement columns must be byte-identical to the original report."""
        if frappe.db.exists("Batch", "MI1I135-PLAIN-A"):
            frappe.db.sql("DELETE FROM `tabBatch` WHERE name=%s", ("MI1I135-PLAIN-A",))
        for stale in frappe.get_all("Container", filters={"container_no": "MI1I135-PLAIN"}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabBatch Items` WHERE parent=%s", (stale,))
            frappe.db.sql("DELETE FROM `tabContainer` WHERE name=%s", (stale,))
        plain_item = f"{ITEM}-PLAIN"
        if not frappe.db.exists("Item", plain_item):
            frappe.get_doc({
                "doctype": "Item", "item_code": plain_item, "item_name": plain_item,
                "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 0,
            }).insert(ignore_permissions=True)
        c = frappe.new_doc("Container")
        c.container_no = "MI1I135-PLAIN"
        c.lot_no = "PLOT"
        c.item = plain_item
        c.transaction_type = "VFY"
        c.posting_date = frappe.utils.nowdate()
        c.append("batches", {"batch_id": "MI1I135-PLAIN-A", "item": plain_item, "qty": 77.0, "cone": 9})
        c.flags.ignore_validate = True
        c.flags.ignore_mandatory = True
        c.insert(ignore_permissions=True)
        frappe.db.set_value("Container", c.name, "docstatus", 1, update_modified=False)
        batch = frappe.new_doc("Batch")
        batch.batch_id = "MI1I135-PLAIN-A"
        batch.item = plain_item
        batch.custom_container_no = "MI1I135-PLAIN"
        batch.custom_lot_no = "PLOT"
        batch.custom_cone = 9
        batch.custom_transaction_type = "VFY"
        batch.flags.ignore_mandatory = True
        batch.insert(ignore_permissions=True)
        # A real stock transaction, so this batch has genuine Serial and
        # Batch Bundle balance too — the original report only shows a batch
        # with live stock, and this test's whole point is comparing the two
        # reports' output for the SAME visible row.
        receipt = frappe.new_doc("Stock Entry")
        receipt.stock_entry_type = "Material Receipt"
        receipt.purpose = "Material Receipt"
        receipt.company = COMPANY
        receipt.posting_date = frappe.utils.nowdate()
        receipt.set_posting_time = 1
        receipt.append("items", {
            "item_code": plain_item, "qty": 77.0, "t_warehouse": "Finished Goods - MC",
            "batch_no": "MI1I135-PLAIN-A", "use_serial_batch_fields": 1, "basic_rate": 10,
        })
        receipt.insert(ignore_permissions=True)
        receipt.submit()
        try:
            frappe.db.commit()
            m = _get_module()
            orig = _get_original_module()
            filt = {"container": "MI1I135-PLAIN", "lot_no": "PLOT"}
            v2_rows = m.get_data(filt)
            orig_rows = orig.get_data(filt)
            self.assertEqual(len(v2_rows), len(orig_rows))
            movement_cols = {"In Qty", "Out Qty", "GR Received", "Job work Send Qty"}
            for a, b in zip(v2_rows, orig_rows):
                for k in b:
                    if k in movement_cols:
                        continue
                    self.assertEqual(a.get(k), b.get(k), f"field {k!r} diverged from the original")
        finally:
            receipt.reload()
            if receipt.docstatus == 1:
                receipt.cancel()
            frappe.db.sql("DELETE FROM `tabStock Entry Detail` WHERE parent=%s", (receipt.name,))
            frappe.db.sql("DELETE FROM `tabStock Entry` WHERE name=%s", (receipt.name,))
            frappe.db.sql("DELETE FROM `tabBatch` WHERE name=%s", ("MI1I135-PLAIN-A",))
            for stale in frappe.get_all("Container", filters={"container_no": "MI1I135-PLAIN"}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabBatch Items` WHERE parent=%s", (stale,))
                frappe.db.sql("DELETE FROM `tabContainer` WHERE name=%s", (stale,))
            frappe.db.commit()


class TestOriginalReportUntouched(FrappeTestCase):

    def test_original_has_no_movement_columns(self):
        orig = _get_original_module()
        cols = [c["fieldname"] for c in orig.get_columns({})]
        for fn in ("In Qty", "Out Qty", "GR Received", "Job work Send Qty"):
            self.assertNotIn(fn, cols)

    def test_original_report_name_unchanged(self):
        orig = _get_original_module()
        self.assertEqual(orig.REPORT_NAME, "STOCK SHEET (BALANCE REPORT)")


class TestRealDataRegression(FrappeTestCase):
    """MI1-I135 browser walkthrough, MCJC-1680 / 02042026: SUM() over the
    real ~34-plus decimal-weight rows behind this lot returned
    3044.0000000000027 on screen instead of 3044 — the exact class of float
    noise the synthetic round-number scenario above cannot reproduce on its
    own. Skips cleanly if this bench's data has moved on."""

    def test_in_qty_has_no_float_noise_on_a_real_multi_batch_lot(self):
        import unittest
        m = _get_module()
        rows = m.get_data({"container": "MCJC-1680", "lot_no": "02042026"})
        if not rows:
            raise unittest.SkipTest("MCJC-1680 / 02042026 no longer has data on this bench")
        for r in rows:
            if r["sort_order"] != 0:
                continue
            self.assertEqual(r["In Qty"], round(r["In Qty"], 2),
                             f"In Qty carries float noise: {r['In Qty']!r}")


class TestMovementMapCaching(FrappeTestCase):
    """MI1-I135 follow-up (2026-09-12, "still the report is not fixed"): a
    live investigation caught the report getting stuck in background mode
    on roughly half of a handful of unfiltered opens — get_movement_totals()
    alone took ~6-7 s unfiltered, on top of the original report's own
    whole-site scan, putting some runs over frappe's 15 s watcher. The
    whole-site (unfiltered) call is now cached the same way
    get_all_warehouse_balances caches the balance map."""

    def setUp(self):
        m = _get_module()
        self.key = m.movement_cache_key()
        frappe.cache().delete_value(self.key)

    def tearDown(self):
        frappe.cache().delete_value(self.key)

    def test_unfiltered_call_is_cached(self):
        m = _get_module()
        first = m.get_movement_totals()
        cached = m._original._read_cached_map(self.key)
        self.assertIsInstance(cached, dict)
        self.assertEqual(cached, first)

    def test_second_unfiltered_call_skips_the_five_expensive_queries(self):
        """A warm hit still runs movement_cache_key()'s own three cheap
        MAX(modified) probes — that IS the content-addressing check — but
        must never re-run any of the five per-source movement aggregations
        (each easily identified: every one GROUPs BY the movement key)."""
        m = _get_module()
        m.get_movement_totals()  # populates the cache
        calls = []
        original_sql = frappe.db.sql

        def _tracking_sql(*args, **kwargs):
            calls.append(args[0] if args else "")
            return original_sql(*args, **kwargs)

        frappe.db.sql = _tracking_sql
        try:
            m.get_movement_totals()
        finally:
            frappe.db.sql = original_sql
        expensive = [c for c in calls if "GROUP BY" in c]
        self.assertEqual(expensive, [], "a warm cache hit must not re-run any movement aggregation query")

    def test_narrow_filtered_call_is_never_cached(self):
        """A Container/Lot/Cone-filtered call is already fast and must stay
        live — caching every distinct filter combination would grow
        unbounded and could go stale between the container's own writes."""
        m = _get_module()
        m.get_movement_totals(container=CONTAINER_NO, lot_no=LOT_NO)
        self.assertIsNone(m._original._read_cached_map(self.key))

    def test_warm_movement_cache_builds_once_then_reports_warm(self):
        m = _get_module()
        self.assertEqual(m.warm_movement_cache(), "built")
        self.assertEqual(m.warm_movement_cache(), "warm")

    def test_cache_key_changes_when_a_container_is_touched(self):
        m = _get_module()
        key_before = m.movement_cache_key()
        c = self._touch_a_container()
        try:
            key_after = m.movement_cache_key()
            self.assertNotEqual(key_before, key_after)
        finally:
            frappe.db.sql("DELETE FROM `tabContainer` WHERE name=%s", (c,))

    @staticmethod
    def _touch_a_container():
        c = frappe.new_doc("Container")
        c.container_no = "MI1I135-CACHEKEY-TOUCH"
        c.transaction_type = "VFY"
        c.posting_date = frappe.utils.nowdate()
        c.flags.ignore_validate = True
        c.flags.ignore_mandatory = True
        c.insert(ignore_permissions=True)
        return c.name


class TestKeepInlineSurvivesTheRepeatableReadRace(FrappeTestCase):
    """MI1-I135 follow-up (2026-09-12, "still the report is not fixed" —
    reported a second time, after a live stress test caught the report
    getting stuck on attempt 4 of 7 unfiltered opens and staying stuck).

    keep_inline() used to read the flag with a plain SELECT
    (frappe.db.get_value) inside the SAME REPEATABLE READ transaction as the
    run it protects. frappe's 15s watcher runs on an INDEPENDENT connection
    and commits prepared_report=1 the moment 15s elapses — a plain SELECT on
    a transaction whose snapshot predates that commit can still see the
    pre-flip 0 and silently no-op, with no Error Log entry either way. The
    fix is a single conditional UPDATE: InnoDB always evaluates a DML
    statement's WHERE clause against the LATEST COMMITTED row ("current
    read"), never an older consistent-read snapshot, so it correctly finds
    and undoes a same-run flip regardless of when this transaction opened.
    """

    REPORT = "STOCK SHEET (BALANCE REPORT) v2"

    def setUp(self):
        self._original = frappe.db.get_value("Report", self.REPORT, "prepared_report")
        frappe.db.set_value("Report", self.REPORT, "prepared_report", 0, update_modified=False)
        frappe.db.commit()

    def tearDown(self):
        frappe.db.set_value("Report", self.REPORT, "prepared_report", self._original, update_modified=False)
        frappe.db.commit()

    def test_survives_a_flip_committed_after_this_transactions_snapshot(self):
        """Reproduces the exact race with two real connections: this
        transaction's snapshot is opened BEFORE a separate connection
        commits the flip, mirroring the watcher's independent connection."""
        import pymysql

        m = _get_module()

        # Open this transaction's REPEATABLE READ snapshot before the
        # external commit below, exactly like a request that had already
        # started running the report when the watcher fires.
        frappe.db.sql("SELECT COUNT(*) FROM `tabReport`")

        conn2 = pymysql.connect(
            host=frappe.conf.db_host or "localhost",
            user=frappe.conf.db_name,
            password=frappe.conf.db_password,
            database=frappe.conf.db_name,
            port=int(frappe.conf.db_port or 3306),
        )
        try:
            with conn2.cursor() as cur:
                cur.execute("UPDATE `tabReport` SET prepared_report=1 WHERE name=%s", (self.REPORT,))
            conn2.commit()
        finally:
            conn2.close()

        # Sanity check the race is real: a plain read on the older
        # transaction must NOT see the external commit.
        self.assertEqual(
            frappe.db.get_value("Report", self.REPORT, "prepared_report"), 0,
            "test setup invalid: this transaction's snapshot already sees the external commit",
        )

        m.keep_inline(started_inline=True)

        frappe.db.commit()
        committed = frappe.db.sql(
            "SELECT prepared_report FROM `tabReport` WHERE name=%s", (self.REPORT,)
        )[0][0]
        self.assertEqual(committed, 0, "keep_inline must undo a flip even when its own read sees stale data")

    def test_does_nothing_when_the_run_did_not_start_inline(self):
        m = _get_module()
        frappe.db.set_value("Report", self.REPORT, "prepared_report", 1, update_modified=False)
        frappe.db.commit()
        m.keep_inline(started_inline=False)
        self.assertEqual(frappe.db.get_value("Report", self.REPORT, "prepared_report"), 1)

    def test_is_a_no_op_when_never_flipped(self):
        m = _get_module()
        modified_before = frappe.db.get_value("Report", self.REPORT, "modified")
        m.keep_inline(started_inline=True)
        self.assertEqual(frappe.db.get_value("Report", self.REPORT, "modified"), modified_before)


class TestKeepReportsInlineCoversBothBalanceReports(FrappeTestCase):
    """MI1-I131's hourly self-heal generalised: mhr-owned reports have their
    own in-run keep_inline, but that runs inside the SAME request/
    transaction as the run it protects, and a live investigation found the
    exact gap — frappe's 15 s watcher commits prepared_report=1 from an
    independent connection, and under MySQL REPEATABLE READ that commit can
    be invisible to keep_inline's own read at the end of the same request,
    so the flip survives with no Error Log entry. The hourly job (a fresh
    transaction every time) is the backstop of last resort."""

    def test_both_balance_reports_are_covered(self):
        self.assertIn("STOCK SHEET (BALANCE REPORT)", utilis.REPORTS_TO_KEEP_INLINE)
        self.assertIn("STOCK SHEET (BALANCE REPORT) v2", utilis.REPORTS_TO_KEEP_INLINE)
        self.assertIn("Stock Ledger", utilis.REPORTS_TO_KEEP_INLINE)

    def test_resets_v2_when_flipped(self):
        original = frappe.db.get_value("Report", "STOCK SHEET (BALANCE REPORT) v2", "prepared_report")
        frappe.db.set_value("Report", "STOCK SHEET (BALANCE REPORT) v2", "prepared_report", 1, update_modified=False)
        try:
            utilis.keep_core_reports_inline()
            self.assertEqual(
                frappe.db.get_value("Report", "STOCK SHEET (BALANCE REPORT) v2", "prepared_report"), 0
            )
        finally:
            frappe.db.set_value(
                "Report", "STOCK SHEET (BALANCE REPORT) v2", "prepared_report", original, update_modified=False
            )


class TestWarmupHooksAreWired(FrappeTestCase):

    def test_registered_on_container_submit_and_cancel(self):
        import mhr.hooks as hooks
        for event in ("on_submit", "on_cancel"):
            self.assertIn(
                "mhr.mhr.report.stock_sheet_(balance_report)_v2.stock_sheet_(balance_report)_v2.enqueue_movement_cache_warmup",
                hooks.doc_events["Container"][event],
            )

    def test_registered_on_stock_entry_submit_and_cancel(self):
        import mhr.hooks as hooks
        for event in ("on_submit", "on_cancel"):
            self.assertIn(
                "mhr.mhr.report.stock_sheet_(balance_report)_v2.stock_sheet_(balance_report)_v2.enqueue_movement_cache_warmup",
                hooks.doc_events["Stock Entry"][event],
            )

    def test_registered_on_delivery_note_submit_and_cancel(self):
        import mhr.hooks as hooks
        for event in ("on_submit", "on_cancel"):
            self.assertIn(
                "mhr.mhr.report.stock_sheet_(balance_report)_v2.stock_sheet_(balance_report)_v2.enqueue_movement_cache_warmup",
                hooks.doc_events["Delivery Note"][event],
            )

    def test_hourly_scheduler_keeps_the_movement_map_warm(self):
        import mhr.hooks as hooks
        self.assertIn(
            "mhr.mhr.report.stock_sheet_(balance_report)_v2.stock_sheet_(balance_report)_v2.warm_movement_cache",
            hooks.scheduler_events["hourly"],
        )
