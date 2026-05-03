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
            ["lot_no", "container_no", "supplier_batch_no"],
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
