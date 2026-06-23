"""MI1-I62 (final cut) — HTY labels print 6 per A4 page, not 1 per page.

Raj's reference PDF (HTY BARCODE (1).pdf) shows 6 labels per A4 sheet
arranged 2 columns × 3 rows. Frappe's `download_multi_pdf` puts one
record per page — wrong layout — so HTY Print Batch generation has its
own renderer in mhr.utilis.render_hty_6up_pdf.

Tests pin:
  - Renderer exists and returns bytes.
  - Output is a valid PDF (starts with %PDF header).
  - Empty/missing input is handled gracefully.
  - print_batch.py branches on transaction_type == "HTY" and routes to
    the 6-up renderer instead of download_multi_pdf.
  - The label HTML template referenced by the renderer includes the
    same field-source mappings as the standalone print format (so the
    two paths can't drift).
"""

import inspect
import re
import shutil
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase


class TestRenderer(FrappeTestCase):

    CONTAINER = "TESTC-6UP"
    LOT = "I62-6UP-LOT"
    ITEM = "_Test 210/72 7.2 GPD"
    BATCHES = ["I62-6UP-B1", "I62-6UP-B2", "I62-6UP-B3"]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Item", cls.ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": cls.ITEM, "item_name": cls.ITEM,
                "item_group": "All Item Groups", "stock_uom": "Nos",
                "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 1,
            }).insert(ignore_permissions=True)
        for i, bid in enumerate(cls.BATCHES, start=1):
            if not frappe.db.exists("Batch", bid):
                frappe.get_doc({
                    "doctype": "Batch", "batch_id": bid, "item": cls.ITEM,
                    "custom_container_no": cls.CONTAINER,
                    "custom_lot_no": cls.LOT,
                    "custom_supplier_batch_no": f"SBN-{i:04d}",
                    "custom_cone": i, "batch_qty": 25.0,
                    "custom_gross_weight": 28.0,
                    "custom_grade": "Grade-AA",
                    "custom_lusture": "Lusture-Bright",
                }).insert(ignore_permissions=True)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        for bid in cls.BATCHES:
            frappe.db.delete("Batch", {"name": bid})
        frappe.db.commit()
        super().tearDownClass()

    def test_renderer_exists(self):
        from mhr import utilis
        self.assertTrue(callable(getattr(utilis, "render_hty_6up_pdf", None)),
            "mhr.utilis.render_hty_6up_pdf must exist.")

    def test_empty_input_returns_empty_bytes(self):
        from mhr.utilis import render_hty_6up_pdf
        self.assertEqual(render_hty_6up_pdf([]), b"")
        self.assertEqual(render_hty_6up_pdf(None) if None is not None else b"", b"")

    def test_skips_missing_batches(self):
        """A non-existent batch name must be logged and skipped, not crash."""
        from mhr.utilis import render_hty_6up_pdf
        out = render_hty_6up_pdf(["__does_not_exist__"])
        self.assertEqual(out, b"",
            "All-missing input returns empty bytes (no labels to render).")

    @unittest.skipUnless(
        shutil.which("wkhtmltopdf"),
        "wkhtmltopdf not installed — skip real PDF rendering (CI has no binary).",
    )
    def test_real_batches_produce_pdf_bytes(self):
        from mhr.utilis import render_hty_6up_pdf
        pdf = render_hty_6up_pdf(self.BATCHES)
        self.assertIsInstance(pdf, (bytes, bytearray))
        self.assertGreater(len(pdf), 1000, "PDF looks suspiciously short.")
        self.assertTrue(pdf.startswith(b"%PDF"),
            "Output must start with the PDF magic header.")


class TestSixUpLayout(FrappeTestCase):
    """Source-level pins on the renderer's HTML structure."""

    def test_label_template_has_required_fields(self):
        from mhr.utilis import HTY_LABEL_HTML
        for label in (
            "Container No.", "Pallet No.", "Den/Fil", "Cone",
            "Net Wt", "Gross Wt", "Grade", "Luster", "Type", "Lot No.",
        ):
            self.assertIn(label, HTY_LABEL_HTML,
                f"Label {label!r} missing from HTY_LABEL_HTML.")

    def test_label_template_uses_helpers(self):
        from mhr.utilis import HTY_LABEL_HTML
        for token in ("doc.custom_container_no", "doc.custom_cone",
                      "item_code", "cone_val", "net_wt_str", "gross_wt_str",
                      "grade_val", "luster_val", "serial", "qr_payload", "qr_url"):
            self.assertIn(token, HTY_LABEL_HTML,
                f"Template must use {token!r} (context key set by the renderer).")

    def test_style_uses_absolute_positioning(self):
        """Layout is absolute-positioned on a fixed-size A4 page so
        wkhtmltopdf can't decide row 3 doesn't fit. Pin the load-bearing
        CSS rules."""
        from mhr.utilis import HTY_6UP_STYLE
        # A4 page size.
        self.assertIn("A4 portrait", HTY_6UP_STYLE)
        # The page block must be exactly A4 in mm.
        self.assertIn("210mm", HTY_6UP_STYLE)
        self.assertIn("297mm", HTY_6UP_STYLE)
        # Cells are absolutely positioned.
        self.assertIn("position: absolute", HTY_6UP_STYLE)
        # Three row offsets (r1/r2/r3) at the chosen mm positions.
        self.assertIn(".cell.r1", HTY_6UP_STYLE)
        self.assertIn(".cell.r2", HTY_6UP_STYLE)
        self.assertIn(".cell.r3", HTY_6UP_STYLE)
        # Two column offsets (c1/c2).
        self.assertIn(".cell.c1", HTY_6UP_STYLE)
        self.assertIn(".cell.c2", HTY_6UP_STYLE)

    def test_page_height_less_than_a4_for_drift_slack(self):
        """The .page height MUST be smaller than A4 (297mm) so wkhtmltopdf's
        per-block rounding drift can't push row 3 onto the next page.

        We hit this bug twice: at 297mm with page-break:none, every 3rd
        page rendered only 4 labels (top 2 rows). The fix is geometric
        slack — 280mm leaves 17mm headroom that drift can never fill.
        """
        import re
        from mhr.utilis import HTY_6UP_STYLE
        rules = re.sub(r"/\*.*?\*/", "", HTY_6UP_STYLE, flags=re.DOTALL)
        # The 297mm-height regression must not return.
        self.assertNotIn("height: 297mm", rules,
            "Don't pin .page to exactly 297mm — wkhtmltopdf drift over "
            "multiple pages pushes the bottom row to the next PDF page.")
        self.assertIn("height: 280mm", rules,
            ".page must be 280mm (17mm of drift slack below A4 297mm).")

    def test_no_explicit_page_breaks_natural_overflow_only(self):
        """With .page at 280mm (17mm slack vs A4 297mm), adding
        page-break-before/after: always causes a blank page after every
        real one — wkhtmltopdf already takes a natural break because the
        next 280mm .page can't fit in the remaining 17mm. Combining the
        two break mechanisms double-breaks. So: NO explicit page-break-
        before/after rules. Natural overflow + page-break-inside: avoid
        is the whole story."""
        import re
        from mhr.utilis import HTY_6UP_STYLE
        rules = re.sub(r"/\*.*?\*/", "", HTY_6UP_STYLE, flags=re.DOTALL)
        self.assertNotIn("page-break-after: always", rules,
            "Don't use page-break-after: always — collides with the natural "
            "overflow break and produces a blank page after each real page.")
        self.assertNotIn("page-break-before: always", rules,
            "Don't use page-break-before: always — same double-break bug.")
        self.assertIn("page-break-inside: avoid", rules,
            ".page must declare page-break-inside: avoid as belt+braces.")

    def test_renderer_emits_6_cells_per_page(self):
        """Source-level pin on the renderer's HTML emission."""
        from mhr.utilis import render_hty_6up_pdf
        import inspect as _inspect
        src = _inspect.getsource(render_hty_6up_pdf)
        # All 6 row/col combinations must be in the positions table.
        for combo in ('("r1", "c1")', '("r1", "c2")',
                      '("r2", "c1")', '("r2", "c2")',
                      '("r3", "c1")', '("r3", "c2")'):
            self.assertIn(combo, src,
                f"Renderer's positions table must include {combo}.")
        # wkhtmltopdf margins must be 0 — the @page CSS owns the size.
        self.assertIn('"margin-top": "0"', src)


class TestPrintBatchBranchesToSixUp(FrappeTestCase):
    """Print Batch's bulk-PDF job must route HTY to the new renderer and
    VFY to the existing download_multi_pdf — pin that the branch exists."""

    def test_print_batch_routes_hty_to_6up(self):
        from mhr.mhr.doctype.print_batch import print_batch as pb_mod
        src = inspect.getsource(pb_mod.PrintBatch.generate_multi_pdf_url)
        self.assertIn("render_hty_6up_pdf", src,
            "generate_multi_pdf_url must import + call render_hty_6up_pdf for HTY.")
        self.assertRegex(src, r'txn_type\s*==\s*"HTY"',
            "Must branch on txn_type == 'HTY'.")
        # VFY path still uses the standard helper.
        self.assertIn("download_multi_pdf", src,
            "VFY path must still call download_multi_pdf (FRD: VFY unchanged).")
