"""MI1-I50 (Raj 2026-09-03) — Job Work Received: received container / lot,
batch rules, and the per-document purpose.

Raj's points, verbatim:
  1. Two editable header fields: Received Container Number, Received Lot No
     (the subcontractor may return material in a different container / lot).
  2. Sent items: Batch No auto-fetched from the original Send — never typed.
  3. New / finished items: Batch created on Submit as
     `Received Container No + Received Lot No + Supplier Batch No`
     e.g. MC-JC-2222 + 13042026 + 6086 -> MC-JC-2222-13042026-6086.
  4. Delivery Challan against the received container picks the new batch up
     with its available qty (covered by the existing Batch-driven DN flows —
     the batch carries container / lot / mode / spec; stock is SBB-driven).
  5. Container information editable at receiving, saved against the
     Received Container Number (copied onto the created Batch).
  6. Same for HTY and VFY (the Batch inherits the entry's transaction type).

The modelling decision this forces: a receipt may now carry target-only
(finished) rows next to source-only / transfer rows. ERPNext's "Material
Transfer" cannot hold a target-only row; "Repack" can, but throws when no
row is finished. So a Receive entry settles its own `purpose` in
before_validate: Repack when any row is target-only, Material Transfer for
a pure return. `purpose` is per-document (StockEntry.set_purpose_for_stock_entry
only fills it when empty), so the "Job Work Received" type is untouched.
"""
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


CF_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")


def _fake_receipt(items, **header):
    doc = frappe._dict({
        "doctype": "Stock Entry",
        "custom_original_send_entry": "MAT-GD-SEND-FAKE",
        "custom_received_container_no": "MC-JC-2222",
        "custom_received_lot_no": "13042026",
        "transaction_type": "VFY",
        "posting_date": "2026-09-03",
        "items": [frappe._dict(r) for r in items],
    })
    doc.update(header)
    return doc


# ---------------------------------------------------------------------------
# 1. fields
# ---------------------------------------------------------------------------


class TestReceivedContainerAndLotFields(FrappeTestCase):

    FIELDS = {
        "custom_received_container_no": ("Received Container Number", "custom_container_number"),
        "custom_received_lot_no": ("Received Lot No", "custom_lot_no"),
    }

    def test_fields_exist_editable_and_gated_to_receipts(self):
        meta = frappe.get_meta("Stock Entry")
        for fn, (label, after) in self.FIELDS.items():
            with self.subTest(field=fn):
                f = meta.get_field(fn)
                self.assertIsNotNone(f, f"Stock Entry.{fn} missing.")
                self.assertEqual(f.fieldtype, "Data")
                self.assertEqual(f.label, label)
                self.assertFalse(f.read_only, "Raj: both fields must be editable.")
                self.assertIn("custom_original_send_entry", f.depends_on or "",
                              "Only meaningful on a Receive-from-Subcontractor entry.")

    def test_fields_ship_via_fixture_next_to_their_sent_counterparts(self):
        with open(CF_FIXTURE, encoding="utf-8") as f:
            se = {r["fieldname"]: r for r in json.load(f) if r.get("dt") == "Stock Entry"}
        for fn, (_label, after) in self.FIELDS.items():
            with self.subTest(field=fn):
                self.assertIn(fn, se, "Field must ship via fixture, not a patch.")
                self.assertEqual(se[fn]["module"], "Mhr")
                self.assertEqual(se[fn]["insert_after"], after,
                                 "Sits right under the sent value it may override.")


# ---------------------------------------------------------------------------
# 2. the draft built from the Send
# ---------------------------------------------------------------------------


class TestDraftFromSend(FrappeTestCase):

    def setUp(self):
        from mhr import utilis
        self.src = inspect.getsource(utilis.make_receive_from_subcontractor)

    def test_sent_rows_carry_their_batch(self):
        self.assertIn('"batch_no": src_item.batch_no', self.src)
        self.assertIn('"use_serial_batch_fields": 1', self.src,
                      "The sent batch must post through the legacy batch field.")

    def test_received_fields_default_to_the_sent_values(self):
        self.assertIn("receipt.custom_received_container_no = source.get(\"custom_container_number\")", self.src)
        self.assertIn("receipt.custom_received_lot_no = source.get(\"custom_lot_no\")", self.src)


# ---------------------------------------------------------------------------
# 3. purpose per document
# ---------------------------------------------------------------------------


class TestSetReceivePurpose(FrappeTestCase):

    def test_registered_on_before_validate(self):
        import mhr.hooks as hooks
        bv = hooks.doc_events["Stock Entry"].get("before_validate", [])
        self.assertIn("mhr.utilis.set_receive_purpose", bv,
                      "Must run before StockEntry.validate() reads purpose.")

    def test_pure_return_is_a_material_transfer(self):
        from mhr.utilis import set_receive_purpose
        doc = _fake_receipt([
            {"s_warehouse": "SUB - MC", "t_warehouse": "FG - MC", "batch_no": "B1"},
            {"s_warehouse": "SUB - MC", "t_warehouse": "FG - MC", "batch_no": "B2"},
        ])
        set_receive_purpose(doc)
        self.assertEqual(doc.purpose, "Material Transfer",
                         "No target-only row -> Repack would throw 'atleast 1 Finished Good'.")

    def test_any_finished_row_makes_it_a_repack(self):
        from mhr.utilis import set_receive_purpose
        doc = _fake_receipt([
            {"s_warehouse": "SUB - MC", "t_warehouse": "", "batch_no": "B1"},      # consumed
            {"s_warehouse": "", "t_warehouse": "FG - MC", "batch_no": ""},         # finished
        ])
        set_receive_purpose(doc)
        self.assertEqual(doc.purpose, "Repack",
                         "A target-only row cannot exist under Material Transfer.")

    def test_non_receipt_entries_are_untouched(self):
        from mhr.utilis import set_receive_purpose
        doc = _fake_receipt([{"s_warehouse": "", "t_warehouse": "FG - MC"}],
                            custom_original_send_entry=None, purpose="Material Receipt")
        set_receive_purpose(doc)
        self.assertEqual(doc.purpose, "Material Receipt",
                         "Every Stock Entry fires this hook; only receipts may be changed.")

    def test_erpnext_accepts_the_mixed_shape_under_repack(self):
        """Prove the modelling against ERPNext itself, without stock: an
        in-memory Repack with one consumed and one finished row passes the
        warehouse + finished-goods validators, and the finished row is the
        one ERPNext flags."""
        se = frappe.new_doc("Stock Entry")
        se.purpose = "Repack"
        se.company = frappe.db.get_value("Company", {}, "name")
        se.append("items", {"item_code": "X", "qty": 1, "s_warehouse": "SUB - MC"})
        se.append("items", {"item_code": "Y", "qty": 1, "t_warehouse": "FG - MC"})
        se.validate_warehouse()
        se.mark_finished_and_scrap_items()
        se.validate_finished_goods()   # would throw without a finished row
        self.assertEqual([bool(r.is_finished_item) for r in se.items], [False, True])

    def test_erpnext_rejects_a_pure_return_under_repack(self):
        """The reason the hook falls back to Material Transfer."""
        from erpnext.stock.doctype.stock_entry.stock_entry import FinishedGoodError
        se = frappe.new_doc("Stock Entry")
        se.purpose = "Repack"
        se.append("items", {"item_code": "X", "qty": 1, "s_warehouse": "SUB - MC", "t_warehouse": "FG - MC"})
        se.mark_finished_and_scrap_items()
        with self.assertRaises(FinishedGoodError):
            se.validate_finished_goods()


# ---------------------------------------------------------------------------
# 4. batch creation for new items
# ---------------------------------------------------------------------------


class TestCreateReceiveBatchesForNewItems(FrappeTestCase):

    ITEM = "MI1I50-RCV-ITEM"
    EXPECTED = "MC-JC-2222-13042026-6086"

    def setUp(self):
        self.tearDown()
        if not frappe.db.exists("Item", self.ITEM):
            item = frappe.new_doc("Item")
            item.update({"item_code": self.ITEM, "item_name": self.ITEM,
                         "item_group": frappe.db.get_value("Item Group", {}, "name"),
                         "stock_uom": "Nos", "has_batch_no": 1, "create_new_batch": 0})
            item.insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        if frappe.db.exists("Batch", self.EXPECTED):
            frappe.delete_doc("Batch", self.EXPECTED, ignore_permissions=True, force=1)
        if frappe.db.exists("Item", self.ITEM):
            frappe.delete_doc("Item", self.ITEM, ignore_permissions=True, force=1)
        frappe.db.commit()

    def _doc(self, **header):
        return _fake_receipt(
            [
                # sent item coming back — must be left alone
                {"item_code": self.ITEM, "qty": 5, "batch_no": "KEEP-ME",
                 "custom_supplier_batch_no": "1", "s_warehouse": "SUB - MC"},
                # new / finished item — gets a batch
                {"item_code": self.ITEM, "qty": 20, "batch_no": "",
                 "custom_supplier_batch_no": "6086", "custom_cone": 8, "t_warehouse": "FG - MC"},
            ],
            transaction_type="HTY",
            custom_glue="Glue-HIGH", custom_pulp="Pulp-Wood", custom_lusture="Lusture-Bright",
            custom_grade="Grade-A", custom_fsc="FSC-1", custom_merge_no="H9",
            custom_notes="received note", custom_cross_section="round",
            **header,
        )

    def test_creates_named_batch_with_received_identity_and_spec(self):
        from mhr.utilis import create_receive_batches
        doc = self._doc()
        create_receive_batches(doc)

        self.assertEqual(doc["items"][0].batch_no, "KEEP-ME", "Sent row untouched.")
        self.assertEqual(doc["items"][1].batch_no, self.EXPECTED)
        self.assertEqual(doc["items"][1].use_serial_batch_fields, 1)

        b = frappe.get_doc("Batch", self.EXPECTED)
        self.assertEqual(b.item, self.ITEM)
        # 2026-09-06: the master is NOT preset — ERPNext adds the posted qty on
        # submit (serial_batch_bundle.update_batch_qty is incremental), and a
        # preset landed underneath it: prod MCL-32-.-1 received 20, read 40.
        # This fake receipt never posts, so the master stays 0 here.
        self.assertEqual(float(b.batch_qty), 0.0)
        self.assertEqual(b.custom_container_no, "MC-JC-2222", "Received container, not the sent one.")
        self.assertEqual(b.custom_lot_no, "13042026", "Received lot.")
        self.assertEqual(b.custom_supplier_batch_no, "6086")
        self.assertEqual(int(b.custom_cone), 8)
        self.assertEqual(b.custom_transaction_type, "HTY", "Point 6: HTY parity via the entry's mode.")
        self.assertEqual(str(b.manufacturing_date), "2026-09-03", "Inward date feeds Stock Sheet Aging.")
        # Point 5: container information entered at receiving lands on the batch.
        self.assertEqual(b.custom_glue, "Glue-HIGH")
        self.assertEqual(b.custom_pulp, "Pulp-Wood")
        self.assertEqual(b.custom_lusture, "Lusture-Bright")
        self.assertEqual(b.custom_grade, "Grade-A")
        self.assertEqual(b.custom_fsc, "FSC-1")
        self.assertEqual(b.custom_merge_no, "H9")
        self.assertEqual(b.custom_notes, "received note")
        self.assertEqual(b.cross_section, "round")

    def test_duplicate_is_a_hard_block(self):
        from mhr.utilis import create_receive_batches
        create_receive_batches(self._doc())
        with self.assertRaises(frappe.ValidationError):
            create_receive_batches(self._doc())

    def test_missing_received_fields_block_the_new_row(self):
        from mhr.utilis import create_receive_batches
        with self.assertRaises(frappe.ValidationError):
            create_receive_batches(self._doc(custom_received_lot_no=""))
        self.assertFalse(frappe.db.exists("Batch", self.EXPECTED))


# ---------------------------------------------------------------------------
# 5. over-receipt check leaves new items alone
# ---------------------------------------------------------------------------


class TestOverReceiptExemptsNewItems(FrappeTestCase):

    def test_only_rows_matching_a_sent_row_are_checked(self):
        """Same rule apply_subcontract_receipt already uses: a receipt row
        that matches no source row by (item, container, lot, supplier batch)
        is a new / finished item — nothing pending to check against."""
        from mhr import utilis
        src = inspect.getsource(utilis.validate_subcontract_receipt)
        self.assertIn("if key not in pending:\n            continue", src,
                      "A new / finished item must not be rejected as an over-receipt.")

    def test_apply_still_skips_unmatched_rows(self):
        """New items never bump custom_received_qty on the Send."""
        from mhr import utilis
        src = inspect.getsource(utilis._apply_receipt_delta)
        self.assertIn("if not rows:\n                continue", src)
