"""MI1-I118 (Raj 2026-09-02) — HTY Delivery Note: Batch dropdown for Chips.

  * HTY + Chips: batches offered on Net Weight (stock) > 0; cone not required.
  * Other HTY products: cone > 0 stays.
  * Only batches with available stock are offered.
  * Picking a batch fills Container No / Lot No / Supplier Batch No / Colour /
    Grade / Product / Type and lands the batch as a row carrying its Net Weight.
  * VFY dropdowns untouched.

Prod facts this is built on (MCGPPC-117-1-24642, read 2026-09-05): 1000 bags of
25 kg, custom_cone 0, custom_product 'Chips', custom_glue 'Product-Chips', and
master batch_qty 0 while the Serial and Batch Bundle balance is 25 — so every
quantity here comes from the bundle balance, never the master.

The local replica is Container MCGPPC-117-1 (HTY, Product-chips, three bags
of 25, cone 0); tests skip where it is absent.
"""
import inspect
import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import note

FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")
CS_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
REPLICA = "MCGPPC-117-1"
SCRIPT = "MI1-I39 — Delivery Note HTY Mode"


def _replica_batches():
    return frappe.get_all("Batch", filters={"custom_container_no": REPLICA, "custom_transaction_type": "HTY"},
                          fields=["name", "custom_supplier_batch_no", "batch_qty", "custom_cone"], order_by="name")


def _script():
    with open(CS_FIXTURE, encoding="utf-8") as fh:
        return next(cs for cs in json.load(fh) if cs["name"] == SCRIPT)


def _query(txt, start=0, page_len=50):
    return note.hty_batch_query("Batch", txt, "name", start, page_len, {"transaction_type": "HTY"})


class TestChipsRule(FrappeTestCase):

    def test_is_chips_batch_reads_plain_product_or_canonical_glue(self):
        yes = [{"custom_product": "Chips"}, {"custom_product": "chips"}, {"custom_glue": "Product-Chips"},
               {"custom_glue": "Glue-CHIPS"}, {"custom_product": "", "custom_glue": "Product-chips"}]
        no = [{"custom_product": "HTY", "custom_glue": "Product-HTY"}, {"custom_product": "Textile"},
              {"custom_glue": "Glue-CENT"}, {}, {"custom_product": None, "custom_glue": None}]
        for b in yes:
            self.assertTrue(note.is_chips_batch(frappe._dict(b)), b)
        for b in no:
            self.assertFalse(note.is_chips_batch(frappe._dict(b)), b)

    def test_sql_twin_agrees(self):
        self.assertIn("LOWER(TRIM(IFNULL(b.custom_product, ''))) = 'chips'", note.CHIPS_SQL)
        self.assertIn("SUBSTRING_INDEX(IFNULL(b.custom_glue, ''), '-', -1)", note.CHIPS_SQL)


class TestHtyBatchQuery(FrappeTestCase):

    def setUp(self):
        if not _replica_batches():
            self.skipTest(f"Replica container {REPLICA} not on this bench.")

    def test_whitelisted_link_query_signature(self):
        self.assertIn(note.hty_batch_query, frappe.whitelisted)
        self.assertEqual(list(inspect.signature(note.hty_batch_query).parameters),
                         ["doctype", "txt", "searchfield", "start", "page_len", "filters"])

    def test_chips_bags_with_stock_are_offered_without_cone(self):
        rows = _query(REPLICA)
        names = [r[0] for r in rows]
        self.assertEqual(names, [b.name for b in _replica_batches()], "All three 25-kg bags, cone 0.")
        for _, desc in rows:
            self.assertIn("Cone 0", desc)
            self.assertIn("25.000", desc, "Net Weight shown is the bundle balance.")

    def test_search_matches_supplier_batch_no_and_container(self):
        self.assertTrue(any(r[0].endswith("-2") for r in _query("2")))
        self.assertTrue(_query("MCGPPC"))

    def test_frappes_own_link_search_path_accepts_the_query(self):
        """What the browser calls: frappe.desk.search.search_link with query=."""
        from frappe.desk.search import search_link
        results = search_link("Batch", REPLICA, query="mhr.note.hty_batch_query",
                              filters=json.dumps({"transaction_type": "HTY"}), page_length=20)
        values = [r["value"] for r in (results or [])]
        self.assertEqual(values, [b.name for b in _replica_batches()])
        self.assertIn("Cone 0", results[0]["description"])

    def test_non_chips_batches_still_need_a_cone(self):
        offered = {r[0] for r in _query("", 0, 5000)}
        stocked_cone0_non_chips = frappe.db.sql("""
            SELECT b.name FROM `tabBatch` b
            JOIN (SELECT sbe.batch_no, SUM(sbe.qty) bal FROM `tabSerial and Batch Entry` sbe
                  JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
                  WHERE sbb.docstatus = 1 AND sbb.is_cancelled = 0 GROUP BY sbe.batch_no HAVING bal > 0) s ON s.batch_no = b.name
            WHERE b.custom_transaction_type = 'HTY' AND IFNULL(b.custom_cone, 0) = 0 AND b.disabled = 0
              AND NOT %s""" % note.CHIPS_SQL, as_dict=False)
        for (name,) in stocked_cone0_non_chips:
            self.assertNotIn(name, offered, f"{name}: cone 0 and not Chips must stay out.")
        for r in _query("", 0, 5000):
            cone = int(r[1].rsplit("Cone ", 1)[1])
            if cone == 0:
                b = frappe.db.get_value("Batch", r[0], ["custom_product", "custom_glue"], as_dict=True)
                self.assertTrue(note.is_chips_batch(b), f"{r[0]} offered with cone 0 but is not Chips.")

    def test_only_hty_batches_and_only_with_stock(self):
        names = [r[0] for r in _query("", 0, 5000)]
        self.assertTrue(names)
        modes = set(frappe.get_all("Batch", filters={"name": ["in", names]}, pluck="custom_transaction_type"))
        self.assertEqual(modes, {"HTY"})
        bal = dict(frappe.db.sql("""SELECT sbe.batch_no, SUM(sbe.qty) FROM `tabSerial and Batch Entry` sbe
            JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
            WHERE sbe.batch_no IN %s AND sbb.docstatus = 1 AND sbb.is_cancelled = 0 GROUP BY sbe.batch_no""", (tuple(names),)))
        self.assertTrue(all(float(bal.get(n, 0)) > 0 for n in names))

    def test_pagination(self):
        p0 = _query("", 0, 2); p1 = _query("", 2, 2)
        self.assertEqual(len(p0), 2)
        self.assertFalse({r[0] for r in p0} & {r[0] for r in p1})


class TestGetItemBatchWithAvailable(FrappeTestCase):

    def setUp(self):
        bs = _replica_batches()
        if not bs:
            self.skipTest(f"Replica container {REPLICA} not on this bench.")
        self.batch = bs[0].name

    def test_returns_bundle_balance_and_warehouse_plus_plain_specs(self):
        from mhr.utilis import get_item_batch
        d = get_item_batch(self.batch, with_available=1)
        self.assertEqual(d["available_qty"], 25.0)
        self.assertEqual(d["warehouse"], "Finished Goods - MC")
        self.assertEqual(d["cone"], 0)
        self.assertEqual((d["product"], d["type"]), ("chips", "bag"))
        self.assertEqual(d["supplier_batch_no"], "1")

    def test_default_call_unchanged_for_the_scan_flow(self):
        from mhr.utilis import get_item_batch
        d = get_item_batch(self.batch)
        self.assertNotIn("available_qty", d)
        self.assertNotIn("warehouse", d)
        self.assertEqual(d["batch_no"], self.batch)

    def test_available_comes_from_the_bundle_when_master_is_zero(self):
        """Prod MCGPPC-117 batches carry batch_qty 0 in the master while the
        bundle holds 25 — the row qty must be 25."""
        from mhr.utilis import get_item_batch
        with patch.object(frappe.db, "get_value", wraps=frappe.db.get_value):
            pass
        doc = frappe.get_doc("Batch", self.batch)
        original = doc.batch_qty
        frappe.db.set_value("Batch", self.batch, "batch_qty", 0, update_modified=False)
        try:
            d = get_item_batch(self.batch, with_available=1)
            self.assertEqual(d["qty"], 0.0)
            self.assertEqual(d["available_qty"], 25.0)
        finally:
            frappe.db.set_value("Batch", self.batch, "batch_qty", original, update_modified=False)


class TestFetchBatchesChipsExemption(FrappeTestCase):

    def setUp(self):
        if not _replica_batches():
            self.skipTest(f"Replica container {REPLICA} not on this bench.")

    def test_fetch_batches_returns_chips_bags(self):
        rows = note.fetch_batches(2, lot_no="L1", container_no=REPLICA)
        self.assertEqual([r["custom_supplier_batch_no"] for r in rows], ["1", "2"])
        for r in rows:
            self.assertEqual(r["custom_cone"], 0)
            self.assertEqual(float(r["batch_qty"]), 25.0, "Qty is the bundle balance.")

    def test_non_chips_cone_zero_batches_still_excluded(self):
        rows = note.fetch_batches(500, container_no="MCZFT-01")
        if not rows:
            self.skipTest("MCZFT-01 not on this bench.")
        self.assertTrue(all(int(r["custom_cone"] or 0) > 0 for r in rows))

    def test_explicit_cone_still_filters_exactly(self):
        src = inspect.getsource(note.fetch_batches)
        self.assertIn('if is_return is False and not filters.get("custom_cone"):', src)
        self.assertIn('["custom_glue", "like", "%-chips"]', src)
        self.assertIn('or_filters=or_filters,', src)


class TestClientScript(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cs = _script()
        cls.src = cls.cs["script"].replace("\r\n", "\n")

    def test_hty_dropdowns_use_the_server_query(self):
        i = self.src.find("function mi1_i76_apply_batch_query_filters(frm)")
        body = self.src[i:i + 1600]
        self.assertIn("if (tt === 'HTY') {", body)
        self.assertIn("query: 'mhr.note.hty_batch_query'", body)
        self.assertIn("frm.set_query('custom_batch', hty_query);", body)
        self.assertIn("frm.set_query('batch_no', 'items', hty_query);", body)

    def test_vfy_dropdowns_unchanged(self):
        self.assertIn("const filters = tt ? { custom_transaction_type: tt } : {};", self.src)
        self.assertIn("Object.assign(filters, { custom_cone: ['>', 0] });", self.src)
        self.assertIn("frm.set_query('custom_batch', () => ({ filters }));", self.src)
        self.assertIn("frm.set_query('batch_no', 'items', () => ({ filters }));", self.src)

    def test_batch_pick_fills_header_and_leaves_rows_to_the_popup(self):
        i = self.src.find("function mi1_i118_on_hty_batch_pick(frm)")
        self.assertGreater(i, -1)
        body = self.src[i:i + 2600]
        self.assertIn("if (frm.doc.is_return) return;", body)
        self.assertIn("if ((frm.doc.transaction_type || '') !== 'HTY') return;", body)
        self.assertIn("args: { batch: frm.doc.custom_batch, with_available: 1 },", body)
        for f in ("custom_colour", "custom_product", "custom_type", "custom_supplier_batch_no"):
            self.assertIn(f"frm.doc.{f} = d.", body)
        self.assertIn("Batch {0} has no stock left to deliver.", body)
        # Rows come from the existing "HTY & VFY" Select Batch popup, which the
        # fetch_from write of Container No opens — no second row path here.
        self.assertNotIn("frm.add_child(", body)
        self.assertIn("custom_batch: mi1_i118_on_hty_batch_pick,", self.src)

    def test_fixture_bumped_and_matches_db(self):
        self.assertGreater(str(self.cs["modified"]), "2026-09-05")
        db = (frappe.db.get_value("Client Script", SCRIPT, "script") or "").replace("\r\n", "\n")
        self.assertEqual(db, self.src)


class TestSupplierBatchNoPathReadsBundleWhenMasterIsZero(FrappeTestCase):
    """Prod MCGPPC-117 bags: master batch_qty 0, bundle 25. The Supplier Batch
    No lookup (Delivery Note V2 -> get_delivery_note_batch) must not land 0."""

    def setUp(self):
        bs = _replica_batches()
        if not bs:
            self.skipTest(f"Replica container {REPLICA} not on this bench.")
        self.batch = bs[2].name

    def test_zero_master_falls_back_to_the_warehouse_balance(self):
        from mhr.utilis import get_delivery_note_batch
        original = frappe.db.get_value("Batch", self.batch, "batch_qty")
        frappe.db.set_value("Batch", self.batch, "batch_qty", 0, update_modified=False)
        try:
            d = get_delivery_note_batch(lot_no="L1", container_no=REPLICA, supplier_batch_no="3", cone=0)
            self.assertEqual(d["batch_no"], self.batch)
            self.assertEqual(float(d["qty"]), 25.0)
            self.assertEqual(d["warehouse"], "Finished Goods - MC")
            self.assertEqual(d["cone"], 0)
        finally:
            frappe.db.set_value("Batch", self.batch, "batch_qty", original, update_modified=False)

    def test_positive_master_is_left_alone(self):
        from mhr.utilis import get_delivery_note_batch
        d = get_delivery_note_batch(lot_no="L1", container_no=REPLICA, supplier_batch_no="3", cone=0)
        self.assertEqual(float(d["qty"]), float(frappe.db.get_value("Batch", self.batch, "batch_qty")))
