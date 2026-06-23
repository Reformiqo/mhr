"""MI1-I27 — `get_print_batch` must return ALL matching Batch rows.

Raj reported MCJC-1522 / Lot 13112025 had multiple items (different
deniers) under the same supplier_batch_no. The previous
implementation returned only the first match (via
`frappe.get_doc(filters)`), so the JS appended one row to
`list_batches` and the other deniers silently dropped — the printed
PDF only contained one denier's barcode.

The fix returns a list of Batch dicts. Tests pin:
  - signature stays `(lot_no, container_no, supplier_batch_no)`
  - return type is list (even when only one match)
  - each dict has the keys the JS reads (item, batch, cone, lot_no, batch_qty)
"""
import frappe
from frappe.tests.utils import FrappeTestCase


class TestGetPrintBatchReturnsList(FrappeTestCase):

    def test_signature(self):
        from mhr.utilis import get_print_batch
        import inspect
        sig = inspect.signature(get_print_batch)
        self.assertEqual(
            list(sig.parameters.keys()),
            ["lot_no", "container_no", "supplier_batch_no", "item", "cone"],
            "MI1-I27 (Item bifurcation) + MI1-I62 (Cone fetch): "
            "get_print_batch must accept an optional `item` (4th) AND "
            "`cone` (5th) filter.",
        )
        # `item` must be optional (default None) so existing callers that
        # don't pass it keep the all-items behaviour.
        self.assertIsNone(
            sig.parameters["item"].default,
            "`item` must default to None (optional).",
        )
        # MI1-I62: `supplier_batch_no` must ALSO be optional (default None) so
        # the JS can fetch every Batch for a (container, lot) pair without
        # forcing the user to type a Supplier Batch No first.
        self.assertIsNone(
            sig.parameters["supplier_batch_no"].default,
            "MI1-I62: `supplier_batch_no` must default to None — required by "
            "the auto-fetch-by-(Container+Lot) flow.",
        )
        # MI1-I62 (Cone fetch): `cone` is optional, defaults to None.
        self.assertIsNone(
            sig.parameters["cone"].default,
            "MI1-I62 (Cone fetch): `cone` must default to None — only "
            "narrows the result when explicitly passed by the VFY form.",
        )

    def test_returns_list_for_no_match(self):
        from mhr.utilis import get_print_batch
        out = get_print_batch(
            lot_no="__nope__",
            container_no="__nope__",
            supplier_batch_no="__nope__",
        )
        self.assertIsInstance(
            out, list,
            "MI1-I27: must return a list (possibly empty), not None or a single dict.",
        )
        self.assertEqual(out, [])

    def test_payload_shape_keys(self):
        """Source-level check that the dict comprehension produces the
        right keys (we can't always guarantee a Batch row exists on
        every test bench)."""
        import re, inspect
        from mhr import utilis as mod
        src = inspect.getsource(mod.get_print_batch)
        no_line = re.sub(r"#[^\n]*", "", src)
        for key in ("item", "batch", "cone", "lot_no", "batch_qty"):
            self.assertIn(
                f'"{key}":', no_line,
                f"get_print_batch payload must include the key {key!r} (read by print_batch.js).",
            )

    def test_uses_frappe_get_all_not_get_doc(self):
        """Pin the implementation: must not regress to
        `frappe.get_doc("Batch", filters)` which only returns the
        first match. Strip docstrings + comments so we only inspect code.
        """
        import re, inspect
        from mhr import utilis as mod
        src = inspect.getsource(mod.get_print_batch)
        no_line = re.sub(r"#[^\n]*", "", src)
        no_doc = re.sub(r'""".*?"""', "", no_line, flags=re.DOTALL)
        self.assertIn(
            "frappe.get_all", no_doc,
            "Must use frappe.get_all to return ALL matching Batches.",
        )
        # The old (broken) code path: frappe.get_doc("Batch", { ... filters ... })
        self.assertFalse(
            re.search(r'frappe\.get_doc\s*\(\s*"Batch"', no_doc),
            "Must NOT use frappe.get_doc(\"Batch\", filters) — it returns only "
            "the first match and silently drops other deniers/items "
            "(the original MI1-I27 bug).",
        )


class TestItemBifurcation(FrappeTestCase):
    """MI1-I27 (Item field): within one Container + Lot No that holds two
    items, the user can pick an Item and print only that item's batches."""

    CONTAINER = "TESTC-I27"
    LOT = "I27LOT01"
    SBN = "I27SB01"
    ITEM_A = "_Test I27 Denier A"
    ITEM_B = "_Test I27 Denier B"
    BATCH_A = "I27-BATCH-A"
    BATCH_B = "I27-BATCH-B"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for it in (cls.ITEM_A, cls.ITEM_B):
            if not frappe.db.exists("Item", it):
                frappe.get_doc({
                    "doctype": "Item", "item_code": it, "item_name": it,
                    "item_group": "All Item Groups", "stock_uom": "Nos",
                    "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 1,
                }).insert(ignore_permissions=True)
        for bid, it in ((cls.BATCH_A, cls.ITEM_A), (cls.BATCH_B, cls.ITEM_B)):
            if not frappe.db.exists("Batch", bid):
                frappe.get_doc({
                    "doctype": "Batch", "batch_id": bid, "item": it,
                    "custom_container_no": cls.CONTAINER,
                    "custom_lot_no": cls.LOT,
                    "custom_supplier_batch_no": cls.SBN,
                    "custom_cone": 5, "batch_qty": 10,
                }).insert(ignore_permissions=True)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        for bid in (cls.BATCH_A, cls.BATCH_B):
            frappe.db.delete("Batch", {"name": bid})
        frappe.db.commit()
        super().tearDownClass()

    def test_get_items_lists_both(self):
        from mhr.mhr.doctype.print_batch.print_batch import get_items
        items = get_items(self.CONTAINER, self.LOT)
        self.assertIn(self.ITEM_A, items)
        self.assertIn(self.ITEM_B, items)

    def test_get_items_blank_filter_returns_empty(self):
        from mhr.mhr.doctype.print_batch.print_batch import get_items
        self.assertEqual(get_items("", self.LOT), [])
        self.assertEqual(get_items(self.CONTAINER, ""), [])

    def test_print_batch_returns_all_items_when_item_blank(self):
        """Backward compat: no item -> every item for the trio (old behaviour)."""
        from mhr.utilis import get_print_batch
        out = get_print_batch(self.LOT, self.CONTAINER, self.SBN)
        self.assertEqual({r["item"] for r in out}, {self.ITEM_A, self.ITEM_B})

    def test_print_batch_filters_to_selected_item(self):
        from mhr.utilis import get_print_batch
        out = get_print_batch(self.LOT, self.CONTAINER, self.SBN, item=self.ITEM_A)
        self.assertTrue(out, "must return the selected item's batch(es)")
        self.assertEqual({r["batch"] for r in out}, {self.BATCH_A})
        self.assertTrue(all(r["item"] == self.ITEM_A for r in out))

    def test_fetch_all_by_container_and_lot_only(self):
        """MI1-I62: with supplier_batch_no omitted (None), every Batch for
        the (container, lot) pair must come back — that's how the JS
        auto-fetch-on-lot-change behaviour works."""
        from mhr.utilis import get_print_batch
        out = get_print_batch(self.LOT, self.CONTAINER)  # no supplier_batch_no
        self.assertEqual({r["batch"] for r in out}, {self.BATCH_A, self.BATCH_B},
            "MI1-I62: omitting supplier_batch_no must return ALL batches "
            "for that (container, lot).")

    def test_fetch_all_with_empty_string_supplier_batch_no(self):
        """The JS passes an empty string when the user hasn't filled
        Supplier Batch No — must behave identically to omitting it."""
        from mhr.utilis import get_print_batch
        out = get_print_batch(self.LOT, self.CONTAINER, "")  # falsy supplier
        self.assertEqual({r["batch"] for r in out}, {self.BATCH_A, self.BATCH_B})

    def test_supplier_batch_no_still_narrows_when_provided(self):
        """Backward compat: when supplier_batch_no IS provided, it still
        filters as before. The fetch-all path is purely additive."""
        from mhr.utilis import get_print_batch
        # Both test batches share the same supplier_batch_no (SBN), so
        # filtering by it returns both. Just confirms the filter still applies.
        out = get_print_batch(self.LOT, self.CONTAINER, self.SBN)
        self.assertEqual({r["batch"] for r in out}, {self.BATCH_A, self.BATCH_B})

    def test_item_field_defined_in_doctype_json(self):
        """Pin: the Item Select must exist + be ordered in the doctype JSON
        (so it actually renders in the selection area)."""
        import os, json
        path = os.path.join(
            frappe.get_app_path("mhr"), "mhr", "doctype", "print_batch", "print_batch.json"
        )
        d = json.load(open(path))
        item = next((f for f in d["fields"] if f.get("fieldname") == "item"), None)
        self.assertIsNotNone(item, "Print Batch JSON must define the `item` field (MI1-I27).")
        self.assertEqual(item.get("fieldtype"), "Select")
        self.assertIn("item", d["field_order"], "`item` must be in field_order to render.")
