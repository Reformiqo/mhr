"""MI1-I91 reopen (Raj 2026-09-03) — the VFY booking flow on HTY Sales Orders.

Raj's ask, verbatim points pinned below:
  * Container No entered -> popup with ONLY Lot No + Item, and ONLY lots
    whose batches still have available stock (> 0).
  * Picking a lot fills Lot No + Denier.
  * Fetch By offers exactly "Cone & Pallet" and "Weight" in HTY.
  * Cone & Pallet behaves like VFY's Cone & Boxes with Boxes -> Pallet.
  * Weight behaves like VFY's Weight (complete batches only).
  * Reuse the VFY logic; do NOT change VFY.

Implementation surface:
  * fields (fixture): Sales Order.custom_no_of_pallet; custom_fetch_by
    options widened to the union of both modes.
  * server (mhr/sales_order.py): get_container_details(transaction_type,
    with_stock) and get_so_batches(pallets, transaction_type) — additive,
    default-off params.
  * client (public/js/sales_order_hty.js): lot popup, fetch-by toggle,
    pallet/weight allocation, all HTY-gated.
"""
import hashlib
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime


JS_PATH = os.path.join(frappe.get_app_path("mhr"), "public", "js", "sales_order_hty.js")
CF_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")
CS_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")


def _js():
    with open(JS_PATH, encoding="utf-8") as f:
        return f.read()


def _so_fixture_fields():
    with open(CF_FIXTURE, encoding="utf-8") as f:
        return {r["fieldname"]: r for r in json.load(f) if r.get("dt") == "Sales Order"}


# ---------------------------------------------------------------------------
# fields
# ---------------------------------------------------------------------------


class TestFields(FrappeTestCase):

    def test_pallet_field_exists_and_is_hidden_by_default(self):
        f = frappe.get_meta("Sales Order").get_field("custom_no_of_pallet")
        self.assertIsNotNone(f, "Sales Order.custom_no_of_pallet missing.")
        self.assertEqual(f.fieldtype, "Int")
        self.assertTrue(f.hidden, "Pallet must be hidden by default — only the HTY "
                        "JS reveals it, so VFY never sees it.")

    def test_pallet_field_is_in_the_fixture_with_mhr_module(self):
        fx = _so_fixture_fields()
        self.assertIn("custom_no_of_pallet", fx, "Field must ship via fixture, not a patch.")
        self.assertEqual(fx["custom_no_of_pallet"]["module"], "Mhr")
        self.assertEqual(fx["custom_no_of_pallet"]["insert_after"], "custom_no_of_boxes")

    def test_fetch_by_options_are_the_union(self):
        """frappe validates a Select against its options on save, so the HTY
        option must live in the DocField; the JS narrows what each mode sees."""
        opts = (frappe.get_meta("Sales Order").get_field("custom_fetch_by").options or "").split("\n")
        for o in ("Cone and Boxes", "Cone & Pallet", "Weight"):
            self.assertIn(o, opts, f"custom_fetch_by must offer {o!r}.")
        fx = _so_fixture_fields()["custom_fetch_by"]["options"]
        self.assertIn("Cone & Pallet", fx, "Union must also be in the fixture.")


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


class TestServerLotAndAllocation(FrappeTestCase):
    """Seed one HTY container_no with two lots — L1 has a batch with SBB
    stock, L2 has a batch with none — plus a VFY batch sharing the same
    container_no + lot, then exercise both endpoints."""

    ITEM = "MI1I91-ITEM"
    CONT = "MI1I91-CONT"
    B_L1 = "MI1I91-B-L1"          # HTY, lot L1, has SBB stock
    B_L2 = "MI1I91-B-L2"          # HTY, lot L2, NO SBB stock
    B_VFY = "MI1I91-B-VFY"        # VFY, lot L1 — must never leak into HTY
    SBB = "MI1I91-SBB-1"
    SBE = "MI1I91-SBE-1"
    C_L1 = "MI1I91-C-L1"
    C_L2 = "MI1I91-C-L2"
    C_VFY = "MI1I91-C-VFY"

    def setUp(self):
        # Warehouse first, then its company: the alphabetically-first Company
        # on this bench has no leaf warehouse, which silently skipped the class.
        wh = frappe.db.get_value("Warehouse", {"is_group": 0, "company": ["!=", ""]},
                                 ["name", "company"], as_dict=True)
        if not wh:
            self.skipTest("Need a leaf Warehouse on this bench.")
        self.warehouse, self.company = wh.name, wh.company
        self.tearDown()  # clear leftovers from a crashed run

        if not frappe.db.exists("Item", self.ITEM):
            item = frappe.new_doc("Item")
            item.update({"item_code": self.ITEM, "item_name": self.ITEM,
                         "item_group": frappe.db.get_value("Item Group", {}, "name"),
                         "stock_uom": "Nos", "has_batch_no": 1, "create_new_batch": 0})
            item.insert(ignore_permissions=True)

        for name, lot, tx in ((self.B_L1, "L1", "HTY"), (self.B_L2, "L2", "HTY"), (self.B_VFY, "L1", "VFY")):
            b = frappe.new_doc("Batch")
            b.update({"batch_id": name, "item": self.ITEM, "batch_qty": 100,
                      "custom_container_no": self.CONT, "custom_lot_no": lot,
                      "custom_supplier_batch_no": name[-3:], "custom_cone": 6,
                      "custom_transaction_type": tx})
            b.flags.ignore_validate = True
            b.insert(ignore_permissions=True)

        # Container docs (submitted) — one per (lot, mode). Raw rows: the
        # Container controller pulls in Purchase Receipt creation on insert.
        ts = now_datetime()
        for name, lot, tx in ((self.C_L1, "L1", "HTY"), (self.C_L2, "L2", "HTY"), (self.C_VFY, "L1", "VFY")):
            frappe.db.sql(
                """INSERT INTO `tabContainer`
                   (name, creation, modified, modified_by, owner, docstatus, idx,
                    container_no, lot_no, item, transaction_type)
                   VALUES (%s, %s, %s, 'Administrator', 'Administrator', 1, 0, %s, %s, %s, %s)""",
                (name, ts, ts, self.CONT, lot, self.ITEM, tx),
            )

        # Positive SBB balance for B_L1 only.
        frappe.db.sql(
            """INSERT INTO `tabSerial and Batch Bundle`
               (name, creation, modified, modified_by, owner, docstatus, idx, is_cancelled,
                type_of_transaction, warehouse, company, item_code, voucher_type)
               VALUES (%s, %s, %s, 'Administrator', 'Administrator', 1, 0, 0,
                       'Inward', %s, %s, %s, 'Stock Entry')""",
            (self.SBB, ts, ts, self.warehouse, self.company, self.ITEM),
        )
        frappe.db.sql(
            """INSERT INTO `tabSerial and Batch Entry`
               (name, creation, modified, modified_by, owner, docstatus, idx,
                parent, parentfield, parenttype, batch_no, qty, warehouse)
               VALUES (%s, %s, %s, 'Administrator', 'Administrator', 1, 1,
                       %s, 'entries', 'Serial and Batch Bundle', %s, 40, %s)""",
            (self.SBE, ts, ts, self.SBB, self.B_L1, self.warehouse),
        )
        frappe.db.commit()

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabSerial and Batch Entry` WHERE name LIKE 'MI1I91-%%'")
        frappe.db.sql("DELETE FROM `tabSerial and Batch Bundle` WHERE name LIKE 'MI1I91-%%'")
        frappe.db.sql("DELETE FROM `tabContainer` WHERE name LIKE 'MI1I91-%%'")
        for n in (self.B_L1, self.B_L2, self.B_VFY):
            if frappe.db.exists("Batch", n):
                frappe.delete_doc("Batch", n, ignore_permissions=True, force=1)
        if frappe.db.exists("Item", self.ITEM):
            frappe.delete_doc("Item", self.ITEM, ignore_permissions=True, force=1)
        frappe.db.commit()

    # --- get_container_details ---

    def test_default_call_is_unchanged_for_vfy(self):
        """No new args -> every submitted Container, any mode, stock or not."""
        from mhr.sales_order import get_container_details
        rows = get_container_details(self.CONT)
        lots = sorted(r["lot_no"] for r in rows)
        self.assertEqual(lots, ["L1", "L2"], "Default call must return both lots (L1 dedupes across modes).")

    def test_hty_lot_popup_drops_zero_stock_lots(self):
        from mhr.sales_order import get_container_details
        rows = get_container_details(self.CONT, transaction_type="HTY", with_stock=1)
        self.assertEqual([r["lot_no"] for r in rows], ["L1"],
                         "Only L1 has a batch with SBB balance > 0; L2 must be dropped.")
        self.assertEqual(rows[0]["item"], self.ITEM)

    def test_transaction_type_filters_containers(self):
        from mhr.sales_order import get_container_details
        rows = get_container_details(self.CONT, transaction_type="VFY")
        self.assertEqual([r["lot_no"] for r in rows], ["L1"], "VFY has only the L1 container.")

    # --- get_so_batches ---

    def test_pallets_alias_allocates_one_batch_per_pallet(self):
        from mhr.sales_order import get_so_batches
        r = get_so_batches(self.ITEM, self.CONT, "L1", pallets=1, transaction_type="HTY")
        self.assertEqual([b["name"] for b in r], [self.B_L1])
        self.assertEqual(float(r[0]["allotted_qty"]), 100.0, "Whole batch, like 1 box in VFY.")

    def test_transaction_type_excludes_other_modes_batches(self):
        from mhr.sales_order import get_so_batches
        # Same container_no + lot exists in VFY; an HTY call must not see it.
        names = {b["name"] for b in get_so_batches(self.ITEM, self.CONT, "L1", pallets=5, transaction_type="HTY")}
        self.assertIn(self.B_L1, names)
        self.assertNotIn(self.B_VFY, names, "VFY batch leaked into an HTY allocation.")
        # And without the arg (the VFY call shape) both are visible — unchanged.
        names_all = {b["name"] for b in get_so_batches(self.ITEM, self.CONT, "L1", boxes=5)}
        self.assertEqual(names_all, {self.B_L1, self.B_VFY})

    def test_weight_mode_never_partial_fetches_in_hty(self):
        from mhr.sales_order import get_so_batches
        r = get_so_batches(self.ITEM, self.CONT, "L1", qty=60, transaction_type="HTY")
        self.assertEqual(r, [], "60 < the only HTY batch (100): nothing may be fetched.")
        r = get_so_batches(self.ITEM, self.CONT, "L1", qty=100, transaction_type="HTY")
        self.assertEqual([b["name"] for b in r], [self.B_L1])


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class TestClientWiring(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _js()

    def _handler(self, key):
        idx = self.src.find("frappe.ui.form.on('Sales Order', {")
        block = self.src[idx:self.src.find("frappe.ui.form.on('Sales Order Item', {")]
        start = block.find(f"    {key}: ")
        self.assertGreater(start, -1, f"handler {key} missing on Sales Order.")
        end = block.find("\n    },", start)
        return block[start:end]

    def test_container_entry_opens_the_lot_popup_not_the_batch_popup(self):
        body = self._handler("custom_container_no")
        self.assertIn("so_hty_open_lot_popup(frm)", body)
        self.assertNotIn("get_hty_batches_for_container_no", body,
                         "Container entry must open the LOT popup now (Raj 2026-09-03).")
        self.assertIn("if (!so_hty_is_hty(frm)) return;", body)

    def test_lot_popup_asks_for_stocked_hty_lots_only(self):
        idx = self.src.find("function so_hty_open_lot_popup(")
        body = self.src[idx:idx + 1200]
        self.assertIn("mhr.sales_order.get_container_details", body)
        self.assertIn("transaction_type: 'HTY'", body)
        self.assertIn("with_stock: 1", body)
        # exactly the two columns Raj asked for
        self.assertIn("<th>Lot No</th><th>Item</th>", body)

    def test_lot_pick_fills_lot_and_denier(self):
        idx = self.src.find("function so_hty_apply_lot_pick(")
        body = self.src[idx:idx + 600]
        self.assertIn("frm.set_value('custom_lot_no'", body)
        self.assertIn("frm.doc.custom_denier = row.item", body)

    def test_fetch_by_handlers_are_hty_gated(self):
        for key in ("custom_fetch_by", "custom_no_of_pallet", "custom_quantity_weight"):
            with self.subTest(key=key):
                self.assertIn("if (!so_hty_is_hty(frm)) return;", self._handler(key))

    def test_pallet_and_weight_route_to_the_vfy_allocator(self):
        self.assertIn("frm.doc.custom_fetch_by !== 'Cone & Pallet'", self._handler("custom_no_of_pallet"))
        self.assertIn("frm.doc.custom_fetch_by !== 'Weight'", self._handler("custom_quantity_weight"))
        idx = self.src.find("function so_hty_fetch_by_allocation(")
        body = self.src[idx:idx + 1500]
        self.assertIn("mhr.sales_order.get_so_batches", body)
        self.assertIn("args.pallets = frm.doc.custom_no_of_pallet", body)
        self.assertIn("transaction_type: 'HTY'", body)

    def test_hty_fetch_by_options_are_pallet_and_weight(self):
        self.assertIn("const SO_HTY_FETCH_BY_OPTIONS = ['', 'Cone & Pallet', 'Weight'];", self.src)
        self.assertIn("const SO_VFY_FETCH_BY_OPTIONS = ['', 'Cone and Boxes', 'Weight'];", self.src)

    def test_boxes_hidden_in_hty_and_booking_controls_no_longer_blanket_hidden(self):
        idx = self.src.find("function so_hty_apply_mode(")
        body = self.src[idx:self.src.find("function so_hty_add_lot_picker_button(")]
        self.assertIn("frm.toggle_display('custom_no_of_boxes', false)", body)
        self.assertNotIn("['custom_fetch_by', 'custom_no_of_boxes', 'custom_quantity_weight']", body,
                         "The MI1-I90 blanket hide of the booking controls must be gone.")

    def test_allocation_rows_seed_cone_copy_for_the_cone_qty_rule(self):
        idx = self.src.find("function so_hty_fetch_by_allocation(")
        self.assertIn("custom_cone_copy: b.custom_cone,", self.src[idx:idx + 3000])

    def test_mi1_i90_soi_gate_count_is_untouched(self):
        """Mirror of MI1-I90's own pin: the Sales Order Item block still has
        exactly two early returns — nothing here leaked into it."""
        idx = self.src.find("frappe.ui.form.on('Sales Order Item', {")
        self.assertEqual(self.src[idx:].count("if (!so_hty_is_hty(frm)) return;"), 2)


class TestVfyUntouched(FrappeTestCase):

    def test_vfy_booking_client_script_knows_nothing_about_pallets(self):
        with open(CS_FIXTURE, encoding="utf-8") as f:
            cs = {r["name"]: r for r in json.load(f)}["Sales Order Booking"]
        self.assertEqual(cs["enabled"], 1)
        self.assertIn("mi1_so_is_vfy", cs["script"])
        self.assertIn("'Cone and Boxes'", cs["script"])
        self.assertNotIn("Pallet", cs["script"], "MI1-I91 must not touch the VFY Client Script.")

    def test_server_defaults_are_off(self):
        from mhr import sales_order
        sig = inspect.signature(sales_order.get_so_batches)
        self.assertEqual(sig.parameters["pallets"].default, 0)
        self.assertIsNone(sig.parameters["transaction_type"].default)
        sig = inspect.signature(sales_order.get_container_details)
        self.assertIsNone(sig.parameters["transaction_type"].default)
        self.assertEqual(sig.parameters["with_stock"].default, 0)
