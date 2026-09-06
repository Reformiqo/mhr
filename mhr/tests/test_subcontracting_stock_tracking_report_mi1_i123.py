"""MI1-I123 — Subcontracting Stock Tracking report.

End-to-end on real documents, built once per class and rolled back with it:

  SEND  (4 sent batches B1 B2 B3 B5, full balance each, FG -> subcontractor)
    RECEIPT 1  returns 10 of B1 into FG            (Material Transfer)
      DN1 ships 4 of B1, DN2 ships 6              -> B1 lot Fully Delivered
    RECEIPT 2  returns the rest of B1 into FG      (second instalment, same lot)
    RECEIPT 3  consumes B2 and B5 at the subcontractor and produces one NEW
               batch (15) into FG                  (Repack: source-only rows +
               a target-only row — ERPNext cannot post a both-warehouse row
               inside a Repack with bundles, so that is the only shape)
    B3 never comes back                            -> Pending
  SEND 2 (B4) received 5 then the receipt is cancelled -> back to Pending
  3 of B3 come back on a 'Job Work Received' entry with no Send link
                                                 -> unlinked section

Then the 17 FRD columns, the six statuses, the FIFO / per-lot arithmetic,
the filters, Expand Delivery Notes and the distinct-Send-row total row.
"""

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from mhr import utilis
from mhr.mhr.report.subcontracting_stock_tracking import subcontracting_stock_tracking as R

ITEM = "58D/8F"
COMPANY = "Meher Creations"
SRC_WH = "Finished Goods - MC"
SUB_WH = "SURAMYA YARN - MC"
CUSTOMER = "Shree Ram Sevak Silk Mills"
CONTAINER = "MI1I123-C"
LOT = "L1"
NEW_SBN = "RPT1"
REPORT_DIR = os.path.join(frappe.get_app_path("mhr"), "mhr", "report", "subcontracting_stock_tracking")

FRD_COLUMNS = [
    "send_entry", "send_date", "source_warehouse", "subcontractor_warehouse", "sent_item", "sent_qty",
    "receipt_entry", "received_qty", "pending_qty", "received_item", "container_no", "batch_no",
    "target_warehouse", "delivery_note", "delivered_qty", "balance_qty", "status",
]


def _pick_batches(n):
    rows = frappe.db.sql("""
        SELECT b.name, s.bal FROM `tabBatch` b
        JOIN (SELECT sbe.batch_no, SUM(sbe.qty) bal FROM `tabSerial and Batch Entry` sbe
              JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
              WHERE sbb.warehouse = %s AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
                AND sbb.type_of_transaction IN ('Inward', 'Outward')
              GROUP BY sbe.batch_no HAVING bal >= 20) s ON s.batch_no = b.name
        WHERE b.item = %s AND b.disabled = 0 AND IFNULL(b.custom_cone, 0) > 0
        ORDER BY b.name LIMIT 60""", (SRC_WH, ITEM))
    names = [r[0] for r in rows]
    booked = utilis.effective_booking_by_batch(names)
    free = [(r[0], flt(r[1], 3)) for r in rows if flt(booked.get(r[0], {}).get("qty", 0)) == 0]
    return free[:n] if len(free) >= n else None


def _masters_present():
    return all(frappe.db.exists(dt, n) for dt, n in (("Item", ITEM), ("Warehouse", SRC_WH),
                                                     ("Warehouse", SUB_WH), ("Customer", CUSTOMER)))


def _make_send(batches, container=CONTAINER, lot=LOT):
    se = frappe.new_doc("Stock Entry")
    se.update({"stock_entry_type": "Send to Subcontractor", "purpose": "Send to Subcontractor",
               "company": COMPANY, "posting_date": today(), "set_posting_time": 1,
               "from_warehouse": SRC_WH, "to_warehouse": SUB_WH,
               "custom_container_number": container, "custom_lot_no": lot})
    for batch, qty in batches:
        sbn = frappe.db.get_value("Batch", batch, "custom_supplier_batch_no") or batch[-4:]
        se.append("items", {"item_code": ITEM, "qty": qty, "batch_no": batch, "use_serial_batch_fields": 1,
                            "s_warehouse": SRC_WH, "t_warehouse": SUB_WH, "custom_supplier_batch_no": sbn,
                            "custom_cone": 1})
    se.insert(ignore_permissions=True)
    se.submit()
    return se


def _receipt_from(send):
    name = utilis.make_receive_from_subcontractor(send.name)["name"]
    return frappe.get_doc("Stock Entry", name)


def _make_dn(batch, qty, warehouse=SRC_WH):
    dn = frappe.new_doc("Delivery Note")
    dn.update({"customer": CUSTOMER, "company": COMPANY, "transaction_type": "VFY",
               "posting_date": today(), "set_posting_time": 1, "set_warehouse": warehouse,
               "custom_sales_person": "Jayendrabhai", "selling_price_list": "Standard Selling", "currency": "INR"})
    dn.append("items", {"item_code": ITEM, "qty": qty, "rate": 100, "warehouse": warehouse,
                        "batch_no": batch, "use_serial_batch_fields": 1})
    dn.insert(ignore_permissions=True)
    dn.submit()
    return dn


def _run(**extra):
    filters = {"company": COMPANY, "from_date": today(), "to_date": today(), "show_stock_columns": 1}
    filters.update(extra)
    cols, data = R.execute(filters)
    total = data[-1] if data and data[-1].get("is_total_row") else None
    body = data[:-1] if total else data
    return cols, body, total


class TestSubcontractingStockTracking(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ready = False
        if not _masters_present():
            return
        picked = _pick_batches(5)
        if not picked:
            return
        (cls.b1, cls.q1), (cls.b2, cls.q2), (cls.b3, cls.q3), (cls.b4, cls.q4), (cls.b5, cls.q5) = picked

        # SEND: four batches, their full balance.
        cls.send = _make_send([(cls.b1, cls.q1), (cls.b2, cls.q2), (cls.b3, cls.q3), (cls.b5, cls.q5)])

        # RECEIPT 1: 10 of B1 come back into FG.
        r1 = _receipt_from(cls.send)
        r1.items = [row for row in r1.items if row.batch_no == cls.b1]
        for i, row in enumerate(r1.items, 1):
            row.idx = i
            row.qty = 10
        r1.to_warehouse = SRC_WH
        r1.save()
        r1.submit()
        cls.r1 = r1

        # Two Delivery Notes ship the returned 10.
        cls.dn1 = _make_dn(cls.b1, 4)
        cls.dn2 = _make_dn(cls.b1, 6)

        # RECEIPT 2: the rest of B1 comes back (same lot, second instalment).
        r2 = _receipt_from(cls.send)
        r2.items = [row for row in r2.items if row.batch_no == cls.b1]
        for i, row in enumerate(r2.items, 1):
            row.idx = i
        r2.to_warehouse = SRC_WH
        r2.save()
        r2.submit()
        cls.r2 = r2

        # RECEIPT 3 (Repack): B2 and B5 consumed at the subcontractor, one NEW
        # batch (15) produced into FG. B3 left out entirely.
        r3 = _receipt_from(cls.send)
        r3.from_warehouse = None
        r3.to_warehouse = None
        r3.items = [row for row in r3.items if row.batch_no in (cls.b2, cls.b5)]
        for i, row in enumerate(r3.items, 1):
            row.idx = i
            row.t_warehouse = None                             # consumed
        r3.append("items", {"item_code": ITEM, "qty": 15, "t_warehouse": SRC_WH, "s_warehouse": None,
                            "custom_supplier_batch_no": NEW_SBN, "custom_cone": 1, "basic_rate": 100})
        r3.save()
        r3.submit()
        cls.r3 = r3
        cls.new_batch = f"{CONTAINER}-{LOT}-{NEW_SBN}"

        # SEND 2 + a receipt that gets cancelled (UAT-10).
        cls.send2 = _make_send([(cls.b4, cls.q4)], container="MI1I123-C2", lot="L2")
        r4 = _receipt_from(cls.send2)
        r4.items[0].qty = 5
        r4.to_warehouse = SRC_WH
        r4.save()
        r4.submit()
        cls.before_cancel = _run(send_entry=cls.send2.name)
        r4.reload()
        r4.cancel()
        cls.r4 = r4

        # Unlinked job-work receipt (UAT-18): 3 of B3 brought back from the
        # subcontractor as a plain 'Job Work Received' transfer, without the
        # link to the Send — so the Send still shows B3 fully pending.
        unl = frappe.new_doc("Stock Entry")
        unl.update({"stock_entry_type": "Job Work Received", "purpose": "Material Transfer", "company": COMPANY,
                    "posting_date": today(), "set_posting_time": 1,
                    "from_warehouse": SUB_WH, "to_warehouse": SRC_WH})
        unl.append("items", {"item_code": ITEM, "qty": 3, "s_warehouse": SUB_WH, "t_warehouse": SRC_WH,
                             "batch_no": cls.b3, "use_serial_batch_fields": 1, "custom_cone": 1})
        unl.insert(ignore_permissions=True)
        unl.submit()
        cls.unlinked = unl
        cls.ready = True

    def setUp(self):
        if not self.ready:
            self.skipTest("Bench lacks the masters / free stocked batches this suite is built on.")

    # -- helpers ------------------------------------------------------------

    def rows_for(self, send=None, **extra):
        cols, body, total = _run(send_entry=send.name if send else None, **extra)
        return body, total

    def one(self, rows, **match):
        found = [r for r in rows if all(r.get(k) == v for k, v in match.items())]
        self.assertEqual(len(found), 1, f"expected exactly one row matching {match}, got {len(found)}: "
                                        f"{[{k: r.get(k) for k in ('send_entry', 'sent_item', 'receipt_entry', 'batch_no', 'received_qty')} for r in rows]}")
        return found[0]

    # -- report contract ----------------------------------------------------

    def test_columns_are_the_seventeen_of_the_frd_in_order(self):
        cols = R.get_columns({})
        self.assertEqual([c["fieldname"] for c in cols], FRD_COLUMNS)
        labels = [c["label"] for c in cols]
        self.assertEqual(labels[0], "Send Entry No.")
        self.assertEqual(labels[6], "Job Work Received No.")
        self.assertEqual(labels[9], "Received / New Item")
        self.assertEqual(labels[11], "Lot / Batch No.")
        self.assertEqual(labels[16], "Status")

    def test_optional_columns_only_with_the_checkbox(self):
        extra = [c["fieldname"] for c in R.get_columns({"show_stock_columns": 1})][17:]
        self.assertEqual(extra, ["stock_in_hand", "subcontractor_balance", "supplier", "sent_container_lot",
                                 "lot_no", "business_line", "uom", "flags"])

    def test_mandatory_filters(self):
        for missing in ("company", "from_date", "to_date"):
            filters = {"company": COMPANY, "from_date": today(), "to_date": today()}
            filters.pop(missing)
            with self.assertRaises(frappe.ValidationError):
                R.execute(filters)

    def test_report_doc_json(self):
        with open(os.path.join(REPORT_DIR, "subcontracting_stock_tracking.json")) as f:
            doc = json.load(f)
        self.assertEqual(doc["report_type"], "Script Report")
        self.assertEqual(doc["ref_doctype"], "Stock Entry")
        self.assertEqual(doc["is_standard"], "Yes")
        self.assertEqual(doc["add_total_row"], 0, "The total row is built server-side (FR-15).")
        self.assertEqual(doc["prepared_report"], 0, "Interactive filters need an inline run.")
        roles = {r["role"] for r in doc["roles"]}
        self.assertTrue({"Stock User", "Stock Manager", "System Manager"} <= roles)
        with open(os.path.join(REPORT_DIR, "subcontracting_stock_tracking.js")) as f:
            js = f.read()
        for fieldname in ("company", "from_date", "to_date", "supplier", "source_warehouse", "subcontractor_warehouse",
                          "target_warehouse", "sent_item", "received_item", "container_no", "batch_no", "send_entry",
                          "status", "business_line", "only_pending", "only_undelivered", "show_stock_columns",
                          "expand_delivery_notes", "restrict_to_date_range"):
            self.assertIn(f'fieldname: "{fieldname}"', js, f"filter {fieldname} missing from the JS")

    # -- the chain ----------------------------------------------------------

    def test_send_row_returned_in_two_receipts_and_shipped_twice(self):
        rows, _ = self.rows_for(self.send)
        first = self.one(rows, receipt_entry=self.r1.name, batch_no=self.b1)
        self.assertEqual(first["sent_item"], ITEM)
        self.assertEqual(first["source_warehouse"], SRC_WH)
        self.assertEqual(first["subcontractor_warehouse"], SUB_WH)
        self.assertAlmostEqual(first["sent_qty"], self.q1, places=3)
        self.assertAlmostEqual(first["received_qty"], 10, places=3)
        self.assertAlmostEqual(first["pending_qty"], 0, places=3, msg="B1 fully returned across both receipts")
        self.assertEqual(first["received_item"], ITEM)
        self.assertEqual(first["target_warehouse"], SRC_WH)
        self.assertEqual(first["delivery_note"], f"{self.dn1.name}, {self.dn2.name}")
        self.assertAlmostEqual(first["delivered_qty"], 10, places=3)
        self.assertAlmostEqual(first["balance_qty"], 0, places=3)
        self.assertEqual(first["status"], "Fully Delivered")

        second = self.one(rows, receipt_entry=self.r2.name, batch_no=self.b1)
        self.assertAlmostEqual(second["received_qty"], self.q1 - 10, places=3)
        self.assertEqual(second["delivery_note"], "", "the 10 shipped belong to the first instalment")
        self.assertAlmostEqual(second["delivered_qty"], 0, places=3)
        self.assertAlmostEqual(second["balance_qty"], self.q1 - 10, places=3)
        self.assertEqual(second["status"], "Stock Available")
        # Stock in hand is the lot's live balance, shown once per lot.
        self.assertAlmostEqual(first["stock_in_hand"], self.q1 - 10, places=3)
        self.assertIsNone(second["stock_in_hand"])
        self.assertEqual(first["flags"], "", "lot balance agrees with live stock")
        self.assertEqual(second["flags"], "")

    def test_new_batch_from_repack_is_a_lot_row(self):
        rows, _ = self.rows_for(self.send)
        lot = self.one(rows, batch_no=self.new_batch)
        self.assertEqual(lot["receipt_entry"], self.r3.name)
        self.assertEqual(lot["received_item"], ITEM)
        self.assertEqual(lot["container_no"], CONTAINER)
        self.assertEqual(lot["lot_no"], LOT)
        self.assertAlmostEqual(lot["received_qty"], 15, places=3)
        self.assertAlmostEqual(lot["balance_qty"], 15, places=3)
        self.assertAlmostEqual(lot["stock_in_hand"], 15, places=3)
        self.assertEqual(lot["status"], "Stock Available")
        self.assertEqual(lot["sent_item"], ITEM)
        self.assertAlmostEqual(lot["sent_qty"], self.q2, places=3, msg="attached to B2, the first row the Repack consumed")
        self.assertAlmostEqual(lot["pending_qty"], 0, places=3)
        self.assertIn(R.FLAG_FIRST_ROW, lot["flags"], "two Send rows consumed, no supplier-batch match")

    def test_consumed_at_subcontractor_row(self):
        rows, _ = self.rows_for(self.send)
        b2 = self.one(rows, sent_item=ITEM, receipt_entry=self.r3.name, batch_no=None)
        self.assertEqual(b2["source_warehouse"], SRC_WH)
        self.assertAlmostEqual(b2["sent_qty"], self.q5, places=3, msg="B5: consumed, and the new lot went to B2")
        self.assertAlmostEqual(b2["received_qty"], self.q5, places=3)
        self.assertAlmostEqual(b2["pending_qty"], 0, places=3)
        self.assertIsNone(b2["received_item"])
        self.assertEqual(b2["status"], "Fully Received")
        self.assertIn("Consumed at the subcontractor", b2["flags"])
        self.assertAlmostEqual(b2["subcontractor_balance"], 0, places=3)

    def test_send_row_never_received_is_pending(self):
        rows, _ = self.rows_for(self.send)
        b3 = self.one(rows, receipt_entry=None, send_entry=self.send.name)
        self.assertAlmostEqual(b3["sent_qty"], self.q3, places=3)
        self.assertAlmostEqual(b3["received_qty"], 0, places=3)
        self.assertAlmostEqual(b3["pending_qty"], self.q3, places=3)
        self.assertEqual(b3["status"], "Pending")
        self.assertIsNone(b3["batch_no"])
        self.assertEqual(b3["delivery_note"], "")
        self.assertAlmostEqual(b3["subcontractor_balance"], self.q3 - 3, places=3,
                               msg="live stock: 3 of B3 came back on the unlinked receipt the Send knows nothing about")
        container = frappe.db.get_value("Batch", self.b3, "custom_container_no") or CONTAINER
        self.assertTrue(b3["sent_container_lot"].startswith(container), b3["sent_container_lot"])
        self.assertEqual(b3["business_line"], "VFY")
        self.assertEqual(b3["uom"], "Nos")

    def test_report_agrees_with_the_hook_on_the_send_entry(self):
        rows, _ = self.rows_for(self.send)
        self.assertTrue(all("Send row records received" not in r["flags"] for r in rows),
                        "replayed FIFO must equal apply_subcontract_receipt's custom_received_qty")
        self.assertEqual(frappe.db.get_value("Stock Entry", self.send.name, "custom_subcontract_status"),
                         "Partially Received")

    def test_partial_receipt_then_cancellation(self):
        cols, body, total = self.before_cancel
        row = self.one(body, send_entry=self.send2.name)
        self.assertAlmostEqual(row["received_qty"], 5, places=3)
        self.assertAlmostEqual(row["pending_qty"], self.q4 - 5, places=3)
        self.assertEqual(row["status"], "Partially Received")
        self.assertEqual(row["receipt_entry"], self.r4.name)

        rows, _ = self.rows_for(self.send2)
        after = self.one(rows, send_entry=self.send2.name)
        self.assertIsNone(after["receipt_entry"], "a cancelled receipt drops out")
        self.assertAlmostEqual(after["received_qty"], 0, places=3)
        self.assertAlmostEqual(after["pending_qty"], self.q4, places=3)
        self.assertEqual(after["status"], "Pending")
        self.assertEqual(after["flags"], "")

    def test_unlinked_receipt_is_listed_not_dropped(self):
        rows, _ = self.rows_for()
        row = self.one(rows, receipt_entry=self.unlinked.name)
        self.assertIsNone(row["send_entry"])
        self.assertIsNone(row["sent_item"])
        self.assertEqual(row["batch_no"], self.b3)
        self.assertEqual(row["received_item"], ITEM)
        self.assertEqual(row["target_warehouse"], SRC_WH)
        self.assertEqual(row["container_no"], frappe.db.get_value("Batch", self.b3, "custom_container_no") or "")
        self.assertAlmostEqual(row["received_qty"], 3, places=3)
        self.assertAlmostEqual(row["pending_qty"], 0, places=3)
        self.assertAlmostEqual(row["stock_in_hand"], 3, places=3)
        self.assertEqual(row["status"], "Stock Available")
        self.assertIn(R.FLAG_NOT_LINKED, row["flags"])
        # Linked rows come first, the unlinked section after.
        self.assertGreater(rows.index(row), max(i for i, r in enumerate(rows) if r["send_entry"] == self.send.name))

    # -- totals -------------------------------------------------------------

    def test_total_row_counts_sent_and_pending_once_per_send_row(self):
        rows, total = self.rows_for(self.send)
        self.assertEqual(total["send_entry"], "Total")
        self.assertEqual(total["is_total_row"], 1)
        self.assertAlmostEqual(total["sent_qty"], self.q1 + self.q2 + self.q3 + self.q5, places=3)
        self.assertAlmostEqual(total["pending_qty"], self.q3, places=3)
        self.assertAlmostEqual(total["received_qty"], self.q1 + self.q5 + 15, places=3,
                               msg="B1 twice, B5 consumed, the new lot; B2's consumption is represented by the new lot")
        self.assertAlmostEqual(total["delivered_qty"], 10, places=3)
        self.assertAlmostEqual(total["balance_qty"], (self.q1 - 10) + 15, places=3)
        self.assertAlmostEqual(total["stock_in_hand"], total["balance_qty"], places=3)
        self.assertAlmostEqual(total["subcontractor_balance"], self.q3 - 3, places=3)
        naive_sent = sum(flt(r["sent_qty"]) for r in rows)
        self.assertGreater(naive_sent, total["sent_qty"], "B1 has three rows; a naive sum would triple it")

    # -- expand -------------------------------------------------------------

    def test_expand_delivery_notes(self):
        rows, total = self.rows_for(self.send, expand_delivery_notes=1)
        lines = [r for r in rows if r["receipt_entry"] == self.r1.name and r["batch_no"] == self.b1]
        self.assertEqual([l["delivery_note"] for l in lines], [self.dn1.name, self.dn2.name])
        self.assertEqual([l["delivered_qty"] for l in lines], [4.0, 6.0])
        self.assertAlmostEqual(lines[0]["received_qty"], 10, places=3)
        self.assertIsNone(lines[1]["received_qty"])
        self.assertIsNone(lines[1]["balance_qty"])
        self.assertEqual({l["status"] for l in lines}, {"Fully Delivered"})
        _, collapsed_total = self.rows_for(self.send)
        for f in R.QTY_FIELDS:
            self.assertAlmostEqual(total[f], collapsed_total[f], places=3, msg=f"expanding must not change {f}")

    # -- filters ------------------------------------------------------------

    def test_status_multiselect(self):
        rows, _ = self.rows_for(self.send, status=["Pending", "Fully Delivered"])
        self.assertEqual({r["status"] for r in rows}, {"Pending", "Fully Delivered"})
        rows, _ = self.rows_for(self.send, status=json.dumps(["Stock Available"]))
        self.assertEqual({r["status"] for r in rows}, {"Stock Available"})
        self.assertEqual(len(rows), 2)

    def test_only_pending_and_only_undelivered(self):
        rows, _ = self.rows_for(self.send, only_pending=1)
        self.assertEqual([r["status"] for r in rows], ["Pending"])
        rows, _ = self.rows_for(self.send, only_undelivered=1)
        self.assertEqual({r["batch_no"] for r in rows}, {self.b1, self.new_batch})
        self.assertTrue(all(flt(r["balance_qty"]) > 0 for r in rows))

    def test_chain_filters(self):
        rows, _ = self.rows_for(batch_no=self.new_batch)
        self.assertEqual([r["batch_no"] for r in rows], [self.new_batch])
        rows, _ = self.rows_for(batch_no=self.b1)
        self.assertEqual({(r["receipt_entry"], r["batch_no"]) for r in rows},
                         {(self.r1.name, self.b1), (self.r2.name, self.b1)}, "a lot's full chain")
        rows, _ = self.rows_for(container_no="mi1i123-c")
        self.assertTrue(rows and all(self.send.name == r["send_entry"] or r["send_entry"] == self.send2.name for r in rows))
        rows, _ = self.rows_for(received_item=ITEM)
        self.assertTrue(rows and all(r["received_item"] == ITEM for r in rows))
        rows, _ = self.rows_for(target_warehouse=SUB_WH)
        self.assertEqual(rows, [])
        rows, _ = self.rows_for(sent_item="__nope__")
        self.assertEqual(rows, [], "Send-side filters describe a Send, which the unlinked section has none of")
        rows, _ = self.rows_for(subcontractor_warehouse=SUB_WH, source_warehouse=SRC_WH)
        self.assertIn(self.send.name, {r["send_entry"] for r in rows})

    def test_business_line_filter(self):
        rows, _ = self.rows_for(self.send, business_line="VFY")
        self.assertTrue(rows, "a Send without a transaction type is VFY, like every legacy document")
        rows, _ = self.rows_for(self.send, business_line="HTY")
        self.assertEqual(rows, [])
        rows, _ = self.rows_for(self.send, business_line="All")
        self.assertTrue(rows)

    def test_restrict_to_date_range_and_supplier(self):
        rows_a, total_a = self.rows_for(self.send)
        rows_b, total_b = self.rows_for(self.send, restrict_to_date_range=1)
        self.assertEqual(len(rows_a), len(rows_b), "everything was posted today")
        rows, _ = self.rows_for(self.send, supplier="__no_such_supplier__")
        self.assertEqual(rows, [])

    def test_status_precedence_table(self):
        d = R.derive_status
        self.assertEqual(d(received=10, delivered=10, balance=0, pending=0, stock_in_hand=0), "Fully Delivered")
        self.assertEqual(d(10, 4, 6, 0, 6), "Partially Delivered")
        self.assertEqual(d(0, 0, 0, 100, 0), "Pending")
        self.assertEqual(d(40, 0, 40, 60, 40), "Partially Received")
        self.assertEqual(d(100, 0, 100, 0, 100), "Stock Available")
        self.assertEqual(d(100, 0, 100, 0, 0), "Fully Received")
        self.assertEqual(d(10, 10, 0, 50, 0), "Fully Delivered", "delivery is judged before the send position")


class TestFinishedRowAttribution(FrappeTestCase):
    """A finished row with the same item as a Send row can never be attributed
    by supplier batch — an equal supplier batch makes it a consuming row (the
    hook's match key). The supplier-batch rule serves the conversion case,
    where the finished item differs, so it is pinned here on plain dicts."""

    def _rows(self):
        s1 = frappe._dict(name="s1", item_code="POY", custom_supplier_batch_no="1", _alloc={"c1": 5.0})
        s2 = frappe._dict(name="s2", item_code="POY", custom_supplier_batch_no="2", _alloc={"c2": 5.0})
        c1 = frappe._dict(name="c1", item_code="POY", custom_supplier_batch_no="1")
        c2 = frappe._dict(name="c2", item_code="POY", custom_supplier_batch_no="2")
        return s1, s2, c1, c2

    def test_supplier_batch_wins(self):
        s1, s2, c1, c2 = self._rows()
        finished = frappe._dict(name="f", item_code="HTY", custom_supplier_batch_no="2")
        self.assertFalse(R._attribute_finished(finished, s1, [s1, s2], [c1, c2, finished]))
        self.assertTrue(R._attribute_finished(finished, s2, [s1, s2], [c1, c2, finished]))

    def test_single_consumed_row_else_first(self):
        s1, s2, c1, c2 = self._rows()
        finished = frappe._dict(name="f", item_code="HTY", custom_supplier_batch_no="9")
        self.assertTrue(R._attribute_finished(finished, s1, [s1, s2], [c1, c2, finished]), "first consumed row")
        self.assertFalse(R._attribute_finished(finished, s2, [s1, s2], [c1, c2, finished]))
        # Receipt that consumed only s2.
        self.assertTrue(R._attribute_finished(finished, s2, [s1, s2], [c2, finished]))
        self.assertFalse(R._attribute_finished(finished, s1, [s1, s2], [c2, finished]))
