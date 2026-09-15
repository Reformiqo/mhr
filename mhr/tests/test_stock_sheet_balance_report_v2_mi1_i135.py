"""MI1-I135 (Raj 2026-09-10) — "Create a new stock sheet report": an exact
replica of Stock Sheet (Balance Report), as a SEPARATE report, with the
changes from MHR_Stock_Sheet_Book2_Reviewed_v3.0.xlsx (the FRD, reviewed and
corrected to v3.0 by Reformiqo's own analyst before this was built).

MI1-I136 (Change Request, 2026-09-13) re-specified all nine quantity
columns; one of its three changes survives to today (Booked / Delivered /
Pending Qty show the Sales Order's RAW figures, not the original report's
"effective/released" booking). MI1-I143 (2026-09-15, live review with Raj,
two passes) reverted the other two and pinned down every column's exact
source:

  * Row grain: one row per (Container, Item, Lot, Cone, Pulp, Lusture,
    Glue, Grade) — the original report's own grain — not MI1-I136's
    collapsed one row per (Container, Lot).
  * In Qty / Out Qty: computed exactly like the existing "Container
    Report" — summed from the real Serial and Batch Bundle ledger, not
    Container.total_net_weight (MI1-I136) and not the raw Batch Items /
    Delivery Note Item / Stock Entry Detail child tables (MI1-I135's
    original design). In Qty excludes return-driven Inward bundles (a
    return posts an Inward-tagged bundle on the SAME batch it shipped
    from — confirmed on real data — which would otherwise double-count
    against GR Received).
  * GR Received / Job work Send Qty: unchanged since MI1-I135 — returns
    and Send to Subcontractor, read from the raw document tables directly.
  * Balance Qty: In Qty − Out Qty + GR Received − Job work Send Qty — the
    ORIGINAL MI1-I135/136 formula, confirmed again explicitly by Raj.

Tests below reflect the CURRENT (post-MI1-I143) behaviour; see that report
module's own get_data() / get_ledger_in_out() docstrings for the full
history and reasoning.

Real documents throughout, built directly (skipping ERPNext's full stock-
transaction validation the way the existing Container tests already do,
mhr.tests.test_dn_notes_from_container_mi1_i83) for GR Received / Job work
Send Qty fixtures — those still read raw child-table rows directly,
independent of whether a document's stock-ledger side effects were ever
posted. In Qty and Out Qty, however, are ledger-based (MI1-I143) — a
fixture that needs to show up in either MUST be a REAL, PROPERLY SUBMITTED
document (real `.insert()` + `.submit()`, not the force-docstatus
shortcut), so those fixtures are built that way.
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
WAREHOUSE = "Finished Goods - MC"

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


def _make_real_receipt(item, batch_id, qty, warehouse=WAREHOUSE):
    """A real Material Receipt -- posts a genuine Inward Serial and Batch
    Bundle entry, which is what MI1-I143's ledger-based In Qty (and the
    live balance_map used for Accepted Warehouse) actually reads."""
    receipt = frappe.new_doc("Stock Entry")
    receipt.stock_entry_type = "Material Receipt"
    receipt.purpose = "Material Receipt"
    receipt.company = COMPANY
    receipt.posting_date = frappe.utils.nowdate()
    receipt.set_posting_time = 1
    receipt.append("items", {
        "item_code": item, "qty": qty, "t_warehouse": warehouse,
        "batch_no": batch_id, "use_serial_batch_fields": 1, "basic_rate": 10,
    })
    receipt.insert(ignore_permissions=True)
    receipt.submit()
    return receipt.name


def _make_real_delivery(item, batch_id, qty, container_no, lot_no, warehouse=WAREHOUSE):
    """A real (non-return) Delivery Note -- posts a genuine Outward Serial
    and Batch Bundle entry, which is what MI1-I143's ledger-based Out Qty
    reads. The header container/lot context still needs to be on the row
    (that is what get_data's grouping reads), independent of the real
    stock-ledger posting.

    Deliberately does NOT set the row's own custom_cone: mhr.utilis.
    update_item_batch (Delivery Note.on_submit) decrements the SOURCE
    batch's own custom_cone by exactly that row value on a REAL (non-
    force-docstatus) submit -- setting it to the batch's full cone count
    zeroes the batch's cone out, which then fails the VFY "cone > 0" gate
    in get_data() and silently drops the row (found empirically: a real
    delivery carrying custom_cone=6 against a 6-cone batch left that
    batch's own custom_cone at 0 afterwards). Cone consumption is not
    what this fixture is testing.
    """
    dn = frappe.new_doc("Delivery Note")
    dn.customer = frappe.db.get_value("Customer", {}, "name")
    dn.company = COMPANY
    dn.posting_date = frappe.utils.nowdate()
    dn.set_posting_time = 1
    # MI1-I139: a VFY Delivery Note's header Batch is mandatory on submit.
    dn.custom_batch = batch_id
    dn.append("items", {
        "item_code": item, "qty": qty, "rate": 10, "warehouse": warehouse,
        "batch_no": batch_id, "use_serial_batch_fields": 1,
        "custom_container_no": container_no, "custom_lot_no": lot_no,
    })
    # Real submit needs the actual stock-ledger posting to go through, but
    # a mandatory custom field (custom_sales_person) unrelated to stock
    # posting shouldn't block a fixture that only cares about the ledger.
    dn.flags.ignore_mandatory = True
    dn.insert(ignore_permissions=True)
    dn.submit()
    return dn.name


def _force_delete_stock_voucher(doctype, name, child_table):
    """Cleanup for a REAL (properly submitted) stock voucher fixture,
    bypassing ERPNext's real `.cancel()` controller entirely.

    Several test classes in this file post real, same-day stock vouchers.
    `.cancel()` on any one of them runs ERPNext core's own
    repost_future_sle_and_gle -> create_item_wise_repost_entries, which
    walks EVERY OTHER stock voucher posted on/after the same date/time and
    re-`frappe.get_doc()`s it -- including a SIBLING test class's own real
    voucher that has, by then, already been hard-deleted by that class's
    own teardown. That throws DoesNotExistError deep inside ERPNext core,
    which aborts THIS class's tearDownClass partway through and leaves
    whatever cleanup was still queued after it (e.g. force-docstatus
    fixtures in `cls.docs`) undone -- explaining figures that mysteriously
    double on a later run (a stale, never-actually-cancelled real receipt
    still sitting at docstatus=1, picked up again by get_ledger_in_out's
    own docstatus=1 filter).

    These are disposable test fixtures on a scratch site -- correct GL /
    valuation reversal is not needed, only that the voucher and its posted
    Serial and Batch Bundle stop reading as docstatus=1 to a later run.
    Setting docstatus directly sidesteps the whole repost cascade."""
    if not name or not frappe.db.exists(doctype, name):
        return
    frappe.db.set_value(doctype, name, "docstatus", 2, update_modified=False)
    frappe.db.sql(
        "UPDATE `tabSerial and Batch Bundle` SET docstatus=2 WHERE voucher_type=%s AND voucher_no=%s",
        (doctype, name),
    )
    frappe.db.sql(f"DELETE FROM `tab{child_table}` WHERE parent=%s", (name,))
    frappe.db.sql(f"DELETE FROM `tab{doctype}` WHERE name=%s", (name,))
    frappe.db.commit()


def _cancel_and_delete_stock_entry(name):
    _force_delete_stock_voucher("Stock Entry", name, "Stock Entry Detail")


def _cancel_and_delete_delivery_note(name):
    _force_delete_stock_voucher("Delivery Note", name, "Delivery Note Item")


def _purge_batch_ledger(batch_no):
    """Defensive pre-clean: force-cancel (docstatus=2, never a real
    `.cancel()` -- see _force_delete_stock_voucher) any REAL stock voucher
    still sitting active against this batch_no from a prior interrupted
    run, so a stale receipt/delivery never gets counted twice. Batch names
    in this file are fixed strings reused on every run, so this is the
    only reliable guard against that class of leftover."""
    vouchers = frappe.db.sql(
        """
        SELECT DISTINCT sbb.voucher_type, sbb.voucher_no
        FROM `tabSerial and Batch Entry` sbe
        JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
        WHERE sbe.batch_no = %s AND sbb.docstatus = 1
        """,
        (batch_no,), as_dict=True,
    )
    for v in vouchers:
        if frappe.db.exists(v.voucher_type, v.voucher_no):
            frappe.db.set_value(v.voucher_type, v.voucher_no, "docstatus", 2, update_modified=False)
    frappe.db.sql(
        "UPDATE `tabSerial and Batch Bundle` sbb "
        "JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name "
        "SET sbb.docstatus = 2 WHERE sbe.batch_no = %s",
        (batch_no,),
    )
    frappe.db.commit()


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


class TestGetLedgerInOut(FrappeTestCase):
    """MI1-I143: In Qty / Out Qty read the real Serial and Batch Bundle
    ledger, exactly like the existing Container Report, with returns
    excluded from In Qty (see get_ledger_in_out's own docstring for why)."""

    ITEM = "MI1I143-LEDGER-ITEM"
    BATCH = "MI1I143-LEDGER-A"
    RECEIPT_QTY = 45.0
    SHIP_QTY = 12.0

    @classmethod
    def _wipe(cls):
        _purge_batch_ledger(cls.BATCH)
        frappe.db.sql("DELETE FROM `tabBatch` WHERE name=%s", (cls.BATCH,))
        frappe.db.commit()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._wipe()
        if not frappe.db.exists("Item", cls.ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": cls.ITEM, "item_name": cls.ITEM, "item_group": "Products",
                "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 0,
            }).insert(ignore_permissions=True)
        b = frappe.new_doc("Batch")
        b.batch_id = cls.BATCH
        b.item = cls.ITEM
        b.custom_transaction_type = "VFY"
        b.flags.ignore_mandatory = True
        b.insert(ignore_permissions=True)
        cls.receipt = _make_real_receipt(cls.ITEM, cls.BATCH, cls.RECEIPT_QTY)
        cls.delivery = _make_real_delivery(
            cls.ITEM, cls.BATCH, cls.SHIP_QTY, "ANY-CONTAINER", "ANY-LOT",
        )
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        _cancel_and_delete_delivery_note(getattr(cls, "delivery", None))
        _cancel_and_delete_stock_entry(getattr(cls, "receipt", None))
        cls._wipe()
        super().tearDownClass()

    def test_in_and_out_qty_match_the_real_ledger(self):
        m = _get_module()
        out = m.get_ledger_in_out([self.BATCH])
        self.assertIn(self.BATCH, out)
        self.assertEqual(out[self.BATCH]["in_qty"], self.RECEIPT_QTY)
        self.assertEqual(out[self.BATCH]["out_qty"], self.SHIP_QTY)
        self.assertEqual(out[self.BATCH]["in_box"], 1)
        self.assertEqual(out[self.BATCH]["out_box"], 1)

    def test_a_batch_with_no_ledger_history_is_absent(self):
        m = _get_module()
        out = m.get_ledger_in_out(["__does_not_exist__"])
        self.assertEqual(out, {})

    def test_empty_input_returns_empty_dict(self):
        m = _get_module()
        self.assertEqual(m.get_ledger_in_out([]), {})

    def test_a_return_delivery_note_does_not_inflate_in_qty(self):
        """The double-counting concern this ticket was built around: a
        return posts an Inward-tagged bundle on the SAME batch it shipped
        from (confirmed on real data, MAT-DN-RET-2026-00014) -- it must be
        excluded from In Qty here so GR Received (which reads the same
        return from Delivery Note Item directly) doesn't count it twice."""
        return_dn = frappe.new_doc("Delivery Note")
        return_dn.customer = frappe.db.get_value("Customer", {}, "name")
        return_dn.company = COMPANY
        return_dn.is_return = 1
        return_dn.posting_date = frappe.utils.nowdate()
        return_dn.set_posting_time = 1
        # MI1-I139: header Batch is mandatory on submit (VFY), returns included.
        return_dn.custom_batch = self.BATCH
        return_dn.append("items", {
            "item_code": self.ITEM, "qty": -5.0, "rate": 10, "warehouse": WAREHOUSE,
            "batch_no": self.BATCH, "use_serial_batch_fields": 1,
        })
        return_dn.flags.ignore_mandatory = True
        return_dn.insert(ignore_permissions=True)
        return_dn.submit()
        try:
            m = _get_module()
            out = m.get_ledger_in_out([self.BATCH])
            self.assertEqual(
                out[self.BATCH]["in_qty"], self.RECEIPT_QTY,
                "a return's Inward-tagged bundle must not add to In Qty",
            )
        finally:
            _cancel_and_delete_delivery_note(return_dn.name)


class TestMovementTotalsAndEndToEnd(FrappeTestCase):
    """One controlled scenario exercising GR Received / Job work Send Qty
    (still raw-child-table-based, force-docstatus fixtures are fine) AND
    In Qty / Out Qty (ledger-based, MI1-I143 -- these fixtures MUST be
    real submits), verified both at the helper level and through the full
    rendered get_data() output."""

    LIVE_QTY_A = 60.0    # real Material Receipt, batch A
    LIVE_QTY_B = 25.0    # real Material Receipt, batch B (identical spec to A -- merges into the same row)
    REAL_OUT_QTY = 15.0  # real (non-return) Delivery Note, ships from batch A
    RETURN_QTY = 10.0    # force-docstatus return DN -- GR Received only
    SEND_QTY = 30.0      # force-docstatus Send to Subcontractor -- Job work Send Qty only

    EXPECTED_IN = LIVE_QTY_A + LIVE_QTY_B
    EXPECTED_OUT = REAL_OUT_QTY
    EXPECTED_GR = RETURN_QTY
    EXPECTED_JW = SEND_QTY
    EXPECTED_BALANCE = EXPECTED_IN - EXPECTED_OUT + EXPECTED_GR - EXPECTED_JW
    EXPECTED_BALANCE_BOX = 2 - 1 + 1 - 1  # in_box(2 receipts) - out_box(1) + gr_box(1) - jw_box(1)

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
            _cancel_and_delete_delivery_note(r.name)
        for r in frappe.db.sql(
            "SELECT DISTINCT se.name FROM `tabStock Entry` se "
            "WHERE se.custom_container_number = %s OR se.custom_received_container_no = %s",
            (CONTAINER_NO, CONTAINER_NO), as_dict=True,
        ):
            _cancel_and_delete_stock_entry(r.name)
        # Receipts don't carry container/lot fields at all, so a prior
        # interrupted run's real Material Receipt for these exact batch
        # names (fixed strings, reused every run) would otherwise survive
        # the sweeps above and silently double In Qty on this run.
        _purge_batch_ledger("MI1I135-TEST-A")
        _purge_batch_ledger("MI1I135-TEST-B")
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

        cls.batch_a = cls._make_batch("MI1I135-TEST-A")
        cls.batch_b = cls._make_batch("MI1I135-TEST-B")
        cls.receipt_a = _make_real_receipt(ITEM, cls.batch_a, cls.LIVE_QTY_A)
        cls.receipt_b = _make_real_receipt(ITEM, cls.batch_b, cls.LIVE_QTY_B)
        cls.real_dn_out = _make_real_delivery(
            ITEM, cls.batch_a, cls.REAL_OUT_QTY, CONTAINER_NO, LOT_NO,
        )
        cls.dn_return = cls._make_delivery(-cls.RETURN_QTY, is_return=1)
        cls.se_send = cls._make_stock_entry_send()
        frappe.db.commit()

    CHILD_TABLE = {
        "Delivery Note": "tabDelivery Note Item",
        "Stock Entry": "tabStock Entry Detail",
    }

    @classmethod
    def tearDownClass(cls):
        _cancel_and_delete_delivery_note(getattr(cls, "real_dn_out", None))
        _cancel_and_delete_stock_entry(getattr(cls, "receipt_a", None))
        _cancel_and_delete_stock_entry(getattr(cls, "receipt_b", None))
        for doctype, name in reversed(cls.docs):
            child = cls.CHILD_TABLE.get(doctype)
            if child:
                frappe.db.sql(f"DELETE FROM `{child}` WHERE parent=%s", (name,))
            frappe.db.sql(f"DELETE FROM `tab{doctype}` WHERE name=%s", (name,))
        frappe.db.commit()
        super().tearDownClass()

    @classmethod
    def _make_batch(cls, batch_id):
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
        """Force-docstatus -- fine for GR Received, which reads Delivery
        Note Item directly and doesn't need a real stock-ledger posting."""
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
            "s_warehouse": WAREHOUSE, "custom_cone": CONE,
        })
        se.flags.ignore_validate = True
        se.flags.ignore_mandatory = True
        se.insert(ignore_permissions=True)
        frappe.db.set_value("Stock Entry", se.name, "docstatus", 1, update_modified=False)
        cls.docs.append(("Stock Entry", se.name))
        return se.name

    def test_get_movement_totals_matches_expected_numbers(self):
        """MI1-I143: get_movement_totals now covers ONLY GR Received / Job
        work Send Qty -- In Qty / Out Qty moved to get_ledger_in_out()."""
        m = _get_module()
        mv = m.get_movement_totals(container=CONTAINER_NO, lot_no=LOT_NO)
        key = m._movement_key(CONTAINER_NO, ITEM, LOT_NO, CONE)
        self.assertIn(key, mv)
        t = mv[key]
        self.assertAlmostEqual(t["gr_qty"], self.EXPECTED_GR, places=3,
                               msg="return qty is stored negative; must be ABS()'d")
        self.assertAlmostEqual(t["jw_qty"], self.EXPECTED_JW, places=3)
        self.assertEqual(t["gr_box"], 1)
        self.assertEqual(t["jw_box"], 1)
        self.assertNotIn("in_qty", t, "In Qty no longer comes from the movement ledger map")
        self.assertNotIn("out_qty", t, "Out Qty no longer comes from the movement ledger map")

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

    def test_end_to_end_row_shows_the_ledger_formula_balance(self):
        """batch A and B share an identical (item, cone, pulp, lusture,
        glue, grade) spec and creation date, so they still merge into ONE
        group here -- this is the ORIGINAL report's own grouping behaviour
        for two batches of an identical spec, not MI1-I136's collapse."""
        m = _get_module()
        rows = m.get_data({"container": CONTAINER_NO, "lot_no": LOT_NO})
        detail = [r for r in rows if r["sort_order"] == 0]
        self.assertEqual(len(detail), 1, "identical-spec batches A and B still merge into one group")
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
                               msg="no Sales Order bookings on this container, so Available Qty = Balance")

    def test_movement_columns_render_clean_not_float_noise(self):
        """Real walkthrough bug (MI1-I135): SUM() over many decimal-weight
        rows can return values like 3044.0000000000027 — In Qty / Out Qty /
        GR Received / Job work Send Qty must be rounded the same way
        Balance already is, not shown as raw float noise on screen."""
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

    def test_passthrough_identity_fields_match_the_original_report(self):
        """A second, simpler container with ONLY a real Material Receipt —
        the identity fields (everything but the four movement columns and
        the quantities Balance Qty now computes differently by design --
        Balance / Balance Box / Booked / Available) must be byte-identical
        to the original report's own detail row for the same batch."""
        _purge_batch_ledger("MI1I135-PLAIN-A")
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
        receipt_name = _make_real_receipt(plain_item, "MI1I135-PLAIN-A", 77.0)
        try:
            frappe.db.commit()
            m = _get_module()
            orig = _get_original_module()
            filt = {"container": "MI1I135-PLAIN", "lot_no": "PLOT"}
            v2_rows = m.get_data(filt)
            orig_rows = orig.get_data(filt)
            v2_detail = next(r for r in v2_rows if r["sort_order"] == 0)
            orig_detail = next(r for r in orig_rows if r["sort_order"] == 0)
            excluded = {
                "In Qty", "Out Qty", "GR Received", "Job work Send Qty",
                "Balance", "Balance Box", "Booked Qty", "Available Qty",
            }
            for k in orig_detail:
                if k in excluded:
                    continue
                self.assertEqual(v2_detail.get(k), orig_detail.get(k), f"field {k!r} diverged from the original")
        finally:
            _cancel_and_delete_stock_entry(receipt_name)
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


class TestSeparateRowsPerItemConeAndRawBooking(FrappeTestCase):
    """MI1-I143 (2026-09-15, live review with Raj — "Item and Cone values
    are currently getting combined ... reflected separately for each
    individual stock/batch record, exactly like the original"): reverts
    MI1-I136's row collapse. Exercises the behaviours specific to this
    revert that TestMovementTotalsAndEndToEnd's single-spec fixture can't
    (its two batches share one spec and merge by design):

      1. A lot holding batches of two DIFFERENT items/cones/specs now
         renders as TWO SEPARATE rows — no comma-joining.
      2. A lot with more than one row gets its own per-lot "Total:" row
         back; a container spanning two lots still gets a Grand Total row.
      3. Booked / Delivered / Pending Qty still show the Sales Order's
         RAW, un-reduced figures (MI1-I136, not reverted) — a delivery
         against the order must not shrink Booked Qty — and now attach to
         the SPECIFIC row whose batch the order actually named, not to a
         lot-wide collapsed row.
    """

    CONTAINER_NO = "MI1I143-TEST"
    LOT_A = "L-A"
    LOT_B = "L-B"
    ITEM_1 = "MI1I143-ITEM-1"
    ITEM_2 = "MI1I143-ITEM-2"
    CONE_1 = 6
    CONE_2 = 8
    LIVE_QTY_A1 = 40.0   # real receipt for MI1I143-A-1 (item 1, cone 1)
    LIVE_QTY_A2 = 35.0   # real receipt for MI1I143-A-2 (item 2, cone 2)
    LIVE_QTY_B1 = 20.0   # real receipt for MI1I143-B-1 (item 1, cone 1)
    SO_ORDERED_QTY = 100.0
    DN_DELIVERED_QTY = 60.0

    @classmethod
    def _wipe(cls):
        for r in frappe.db.sql(
            "SELECT DISTINCT dn.name FROM `tabDelivery Note` dn "
            "JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name "
            "WHERE dni.custom_container_no = %s", (cls.CONTAINER_NO,), as_dict=True,
        ):
            frappe.db.sql("DELETE FROM `tabDelivery Note Item` WHERE parent=%s", (r.name,))
            frappe.db.sql("DELETE FROM `tabDelivery Note` WHERE name=%s", (r.name,))
        for r in frappe.get_all("Sales Order", filters={"name": ["like", "SO-MI1I143%"]}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabSales Order Item` WHERE parent=%s", (r,))
            frappe.db.sql("DELETE FROM `tabSales Order` WHERE name=%s", (r,))
        # Real Material Receipts don't carry container/lot fields, so a
        # prior interrupted run's leftover receipt for these exact batch
        # names would otherwise survive the sweep above.
        for batch_id in ("MI1I143-A-1", "MI1I143-A-2", "MI1I143-B-1"):
            _purge_batch_ledger(batch_id)
        for r in frappe.get_all("Container", filters={"container_no": cls.CONTAINER_NO}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabBatch Items` WHERE parent=%s", (r,))
            frappe.db.sql("DELETE FROM `tabContainer` WHERE name=%s", (r,))
        frappe.db.sql("DELETE FROM `tabBatch` WHERE custom_container_no=%s", (cls.CONTAINER_NO,))
        frappe.db.commit()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._wipe()
        cls.receipts = []
        for item in (cls.ITEM_1, cls.ITEM_2):
            if not frappe.db.exists("Item", item):
                frappe.get_doc({
                    "doctype": "Item", "item_code": item, "item_name": item, "item_group": "Products",
                    "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 0,
                }).insert(ignore_permissions=True)

        # Lot A: two items, two cones, two batches -> must render as TWO rows.
        cls.container_a = cls._make_container(cls.LOT_A, [
            ("MI1I143-A-1", cls.ITEM_1, cls.CONE_1, "Pulp-White", "Lusture-Bright", "Glue-PVA", "Grade-AA"),
            ("MI1I143-A-2", cls.ITEM_2, cls.CONE_2, "Pulp-Black", "Lusture-Matte", "Glue-EVA", "Grade-BB"),
        ])
        cls.receipts.append(_make_real_receipt(cls.ITEM_1, "MI1I143-A-1", cls.LIVE_QTY_A1))
        cls.receipts.append(_make_real_receipt(cls.ITEM_2, "MI1I143-A-2", cls.LIVE_QTY_A2))

        # Lot B: one item, one batch -> a second lot on the SAME container,
        # so a Grand Total row is expected.
        cls.container_b = cls._make_container(cls.LOT_B, [
            ("MI1I143-B-1", cls.ITEM_1, cls.CONE_1, "Pulp-White", "Lusture-Bright", "Glue-PVA", "Grade-AA"),
        ])
        cls.receipts.append(_make_real_receipt(cls.ITEM_1, "MI1I143-B-1", cls.LIVE_QTY_B1))

        cls.so = cls._make_sales_order()
        cls.dn = cls._make_delivery_against_so()
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        for name in cls.receipts:
            _cancel_and_delete_stock_entry(name)
        if frappe.db.exists("Delivery Note", getattr(cls, "dn", None) or ""):
            frappe.db.sql("DELETE FROM `tabDelivery Note Item` WHERE parent=%s", (cls.dn,))
            frappe.db.sql("DELETE FROM `tabDelivery Note` WHERE name=%s", (cls.dn,))
        if frappe.db.exists("Sales Order", getattr(cls, "so", None) or ""):
            frappe.db.sql("DELETE FROM `tabSales Order Item` WHERE parent=%s", (cls.so,))
            frappe.db.sql("DELETE FROM `tabSales Order` WHERE name=%s", (cls.so,))
        frappe.db.sql("DELETE FROM `tabBatch Items` WHERE parent IN (%s, %s)", (cls.container_a, cls.container_b))
        frappe.db.sql("DELETE FROM `tabContainer` WHERE name IN (%s, %s)", (cls.container_a, cls.container_b))
        frappe.db.sql("DELETE FROM `tabBatch` WHERE custom_container_no=%s", (cls.CONTAINER_NO,))
        frappe.db.commit()
        super().tearDownClass()

    @classmethod
    def _make_container(cls, lot_no, batch_specs):
        c = frappe.new_doc("Container")
        c.container_no = cls.CONTAINER_NO
        c.lot_no = lot_no
        c.item = batch_specs[0][1]
        c.transaction_type = "VFY"
        c.posting_date = frappe.utils.nowdate()
        for batch_id, item, cone, *_ in batch_specs:
            c.append("batches", {"batch_id": batch_id, "item": item, "qty": 10, "cone": cone})
        c.flags.ignore_validate = True
        c.flags.ignore_mandatory = True
        c.insert(ignore_permissions=True)
        frappe.db.set_value("Container", c.name, "docstatus", 1, update_modified=False)
        for batch_id, item, cone, pulp, lusture, glue, grade in batch_specs:
            if frappe.db.exists("Batch", batch_id):
                frappe.db.sql("DELETE FROM `tabBatch` WHERE name=%s", (batch_id,))
            b = frappe.new_doc("Batch")
            b.batch_id = batch_id
            b.item = item
            b.custom_container_no = cls.CONTAINER_NO
            b.custom_lot_no = lot_no
            b.custom_cone = cone
            b.custom_pulp = pulp
            b.custom_lusture = lusture
            b.custom_glue = glue
            b.custom_grade = grade
            b.custom_transaction_type = "VFY"
            b.flags.ignore_mandatory = True
            b.insert(ignore_permissions=True)
        return c.name

    @classmethod
    def _make_sales_order(cls):
        customer = frappe.db.get_value("Customer", {}, "name")
        so = frappe.new_doc("Sales Order")
        so.naming_series = "SO-MI1I143-.####"
        so.customer = customer
        so.company = COMPANY
        so.transaction_type = "VFY"
        so.transaction_date = frappe.utils.nowdate()
        so.delivery_date = frappe.utils.add_days(frappe.utils.nowdate(), 7)
        so.append("items", {
            "item_code": cls.ITEM_1, "qty": cls.SO_ORDERED_QTY, "rate": 10,
            "delivery_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
            "custom_batch_no": "MI1I143-A-1",
        })
        so.flags.ignore_validate = True
        so.flags.ignore_mandatory = True
        so.insert(ignore_permissions=True)
        frappe.db.set_value("Sales Order", so.name, "docstatus", 1, update_modified=False)
        # Forcing docstatus this way (matching this file's established
        # shortcut for Container/Batch/Stock Entry) skips ERPNext's own
        # on_submit status computation -- status would stay "Draft"
        # otherwise, and sales_order_booking_state only picks up orders
        # whose status is in SO_OPEN_STATUSES.
        frappe.db.set_value("Sales Order", so.name, "status", "To Deliver and Bill", update_modified=False)
        return so.name

    @classmethod
    def _make_delivery_against_so(cls):
        """Force-docstatus -- this DN only needs to exist for the RAW
        Sales-Order-booking-figure tests below (Booked/Delivered/Pending),
        which read Delivery Note Item / against_sales_order directly. It
        deliberately does NOT post a real stock-ledger entry, so it has NO
        effect on Out Qty / Balance Qty (both ledger-based, MI1-I143) --
        those are exercised separately by TestMovementTotalsAndEndToEnd's
        own dedicated real-delivery fixture."""
        dn = frappe.new_doc("Delivery Note")
        dn.customer = frappe.db.get_value("Customer", {}, "name")
        dn.company = COMPANY
        dn.is_return = 0
        dn.posting_date = frappe.utils.nowdate()
        dn.set_posting_time = 1
        dn.append("items", {
            "item_code": cls.ITEM_1, "qty": cls.DN_DELIVERED_QTY, "rate": 10,
            "against_sales_order": cls.so, "so_detail": frappe.db.get_value(
                "Sales Order Item", {"parent": cls.so}, "name"),
            "custom_container_no": cls.CONTAINER_NO, "custom_lot_no": cls.LOT_A, "custom_cone": cls.CONE_1,
        })
        dn.flags.ignore_validate = True
        dn.flags.ignore_mandatory = True
        dn.insert(ignore_permissions=True)
        frappe.db.set_value("Delivery Note", dn.name, "docstatus", 1, update_modified=False)
        return dn.name

    def test_two_items_and_cones_render_as_separate_rows_not_comma_joined(self):
        m = _get_module()
        rows = m.get_data({"container": self.CONTAINER_NO, "lot_no": self.LOT_A})
        detail = [r for r in rows if r["sort_order"] == 0]
        self.assertEqual(len(detail), 2, "two different item/cone specs must render as two separate rows")
        by_item = {r["Item"]: r for r in detail}
        self.assertIn(self.ITEM_1, by_item)
        self.assertIn(self.ITEM_2, by_item)
        row_1 = by_item[self.ITEM_1]
        row_2 = by_item[self.ITEM_2]
        self.assertEqual(row_1["Cone"], self.CONE_1)
        self.assertEqual(row_2["Cone"], self.CONE_2)
        self.assertEqual(row_1["Pulp"], "White")
        self.assertEqual(row_2["Pulp"], "Black")
        self.assertEqual(row_1["Lusture"], "Bright")
        self.assertEqual(row_2["Lusture"], "Matte")
        self.assertEqual(row_1["Glue"], "PVA")
        self.assertEqual(row_2["Glue"], "EVA")
        self.assertEqual(row_1["Grade"], "AA")
        self.assertEqual(row_2["Grade"], "BB")
        # In Qty is ledger-based per batch (MI1-I143) -- each row shows
        # ONLY its own batch's real receipt, genuinely additive, no longer
        # a shared per-lot constant repeated across rows.
        self.assertEqual(row_1["In Qty"], self.LIVE_QTY_A1)
        self.assertEqual(row_2["In Qty"], self.LIVE_QTY_A2)
        self.assertEqual(row_1["Balance"], self.LIVE_QTY_A1)
        self.assertEqual(row_2["Balance"], self.LIVE_QTY_A2)

    def test_per_lot_total_row_is_back_and_grand_total_still_present(self):
        m = _get_module()
        rows = m.get_data({"container": self.CONTAINER_NO})
        sort_orders = sorted(set(r["sort_order"] for r in rows))
        self.assertIn(1, sort_orders, "lot A has two rows again -- its per-lot Total: row must be back")
        self.assertIn(2, sort_orders, "container spans two lots -- Grand Total: row must still exist")
        detail_rows = [r for r in rows if r["sort_order"] == 0]
        self.assertEqual(len(detail_rows), 3, "two rows for lot A, one for lot B")
        lot_a_total = next(
            r for r in rows if r["sort_order"] == 1 and r["Lot Number"] == self.LOT_A
        )
        self.assertAlmostEqual(
            lot_a_total["In Qty"], self.LIVE_QTY_A1 + self.LIVE_QTY_A2, places=2
        )
        grand_total = next(r for r in rows if r["sort_order"] == 2)
        self.assertAlmostEqual(
            grand_total["In Qty"], self.LIVE_QTY_A1 + self.LIVE_QTY_A2 + self.LIVE_QTY_B1, places=2
        )

    def test_booked_qty_is_the_raw_ordered_amount_on_the_specific_batchs_row(self):
        """The Sales Order ordered 100 against batch MI1I143-A-1
        specifically; a Delivery Note against it has already shipped 60.
        The original report's own "effective/released" booking would show
        40 (100 - 60) -- MI1-I136 (not reverted) requires the RAW 100
        instead. With rows un-collapsed (MI1-I143), the booking now
        attaches ONLY to A-1's own row, not A-2's (a different batch, no
        Sales Order reference)."""
        m = _get_module()
        rows = m.get_data({"container": self.CONTAINER_NO, "lot_no": self.LOT_A})
        detail = [r for r in rows if r["sort_order"] == 0]
        a1_rows = [r for r in detail if r["Item"] == self.ITEM_1]
        a2_rows = [r for r in detail if r["Item"] == self.ITEM_2]
        self.assertEqual(len(a1_rows), 1)
        row = a1_rows[0]
        self.assertEqual(row["Sales Order"], self.so)
        self.assertEqual(row["Booked Qty"], self.SO_ORDERED_QTY, "must be the raw ordered qty, not ordered-minus-delivered")
        self.assertEqual(row["Delivered Qty"], self.DN_DELIVERED_QTY)
        self.assertEqual(row["Pending Qty"], self.SO_ORDERED_QTY - self.DN_DELIVERED_QTY)
        self.assertEqual(len(a2_rows), 1)
        self.assertEqual(a2_rows[0]["Sales Order"], "", "the order named A-1's batch, not A-2's -- must not leak across rows")

    def test_available_qty_on_the_so_row_is_ledger_balance_minus_raw_booked(self):
        """The Delivery Note built for the booking above is force-
        submitted (never posts a real Stock Ledger Entry), so it has no
        effect on Out Qty / Balance Qty (both ledger-based, MI1-I143) --
        Available Qty is A-1's own real receipt (40) minus the raw booked
        amount (100), independent of the DN's delivered qty."""
        m = _get_module()
        rows = m.get_data({"container": self.CONTAINER_NO, "lot_no": self.LOT_A})
        so_row = next(r for r in rows if r.get("Sales Order") == self.so)
        self.assertEqual(so_row["Available Qty"], round(self.LIVE_QTY_A1 - self.SO_ORDERED_QTY, 2))


class TestMovementMapCaching(FrappeTestCase):
    """MI1-I135 follow-up (2026-09-12, "still the report is not fixed"): a
    live investigation caught the report getting stuck in background mode
    on roughly half of a handful of unfiltered opens — get_movement_totals()
    alone took ~6-7 s unfiltered, on top of the original report's own
    whole-site scan, putting some runs over frappe's 15 s watcher. The
    whole-site (unfiltered) call is now cached the same way
    get_all_warehouse_balances caches the balance map.

    MI1-I143: this cache now covers only GR Received / Job work Send Qty
    (two queries, not the original five) -- In Qty / Out Qty moved to
    get_ledger_in_out(), which is not cached (it is already scoped to the
    exact batch_ids get_data() loads, the same way get_batch_balances is)."""

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

    def test_second_unfiltered_call_skips_the_expensive_queries(self):
        """A warm hit still runs movement_cache_key()'s own three cheap
        MAX(modified) probes — that IS the content-addressing check — but
        must never re-run either of the two per-source movement
        aggregations (each easily identified: every one GROUPs BY the
        movement key)."""
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

        m.keep_inline()

        frappe.db.commit()
        committed = frappe.db.sql(
            "SELECT prepared_report FROM `tabReport` WHERE name=%s", (self.REPORT,)
        )[0][0]
        self.assertEqual(committed, 0, "keep_inline must undo a flip even when its own read sees stale data")

    def test_resets_even_when_the_flag_was_already_set_before_this_call(self):
        """MI1-I135 round 2 (2026-09-12): the first fix only reset the flag
        when THIS call had itself observed it as 0 at the start
        (`started_inline`). That gate was the actual closed-loop bug — once
        `prepared_report` reads 1 at the start of a request, frappe's own
        dispatcher (frappe.desk.query_report.run) routes around this
        module's execute() entirely (or, for "Rebuild", into a worker
        execute() call that ALSO sees the flag already at 1) — so no
        execute() call downstream of the flip could ever see
        started_inline=True again, and the flag stayed stuck forever short
        of the hourly job. keep_inline() must reset unconditionally: there
        is no legitimate "administrator wants this report to stay in
        background mode" case for either balance report — every ticket on
        this flag has been a client complaint about it getting stuck."""
        m = _get_module()
        frappe.db.set_value("Report", self.REPORT, "prepared_report", 1, update_modified=False)
        frappe.db.commit()
        m.keep_inline()
        self.assertEqual(frappe.db.get_value("Report", self.REPORT, "prepared_report"), 0)

    def test_is_a_no_op_when_never_flipped(self):
        m = _get_module()
        modified_before = frappe.db.get_value("Report", self.REPORT, "modified")
        m.keep_inline()
        self.assertEqual(frappe.db.get_value("Report", self.REPORT, "modified"), modified_before)


class TestExecuteRecoversFromAnAlreadyStuckFlag(FrappeTestCase):
    """MI1-I135 round 2, end-to-end reproduction of the exact closed loop a
    live stress test hit: frappe.desk.query_report.run() only calls this
    module's execute() while prepared_report reads 0 at the request's own
    start; once it reads 1, every normal open (and "Rebuild", which just
    enqueues a background worker call to the SAME execute()) is routed
    around a fresh inline call — so under the old started_inline gate, no
    call downstream of the flip could ever reset it. This proves execute()
    itself — not just keep_inline() in isolation — clears the flag even
    when called while prepared_report is already 1, which is exactly what
    a background-job (Rebuild) invocation of execute() looks like."""

    REPORT = "STOCK SHEET (BALANCE REPORT) v2"

    def setUp(self):
        self._original = frappe.db.get_value("Report", self.REPORT, "prepared_report")

    def tearDown(self):
        frappe.db.set_value("Report", self.REPORT, "prepared_report", self._original, update_modified=False)
        frappe.db.commit()

    def test_execute_clears_a_flag_that_was_already_set_before_the_call(self):
        m = _get_module()
        frappe.db.set_value("Report", self.REPORT, "prepared_report", 1, update_modified=False)
        frappe.db.commit()

        m.execute(filters={})

        self.assertEqual(
            frappe.db.get_value("Report", self.REPORT, "prepared_report"), 0,
            "execute() must clear prepared_report even when it read 1 at its own "
            "start — this is exactly the state a Rebuild-triggered background "
            "worker call to execute() sees, and the old gate left it stuck forever.",
        )


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
