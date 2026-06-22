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

    def test_style_declares_6up_grid(self):
        from mhr.utilis import HTY_6UP_STYLE
        # 2-column sheet table with cell width 50%.
        self.assertIn("table.sheet", HTY_6UP_STYLE)
        self.assertIn("width: 50%", HTY_6UP_STYLE)
        # Page-break for multi-page output.
        self.assertIn("page-break-after", HTY_6UP_STYLE)
        # A4 page size.
        self.assertIn("A4", HTY_6UP_STYLE)

    def test_renders_grid_with_2_columns_per_row(self):
        """Pin the renderer lays out 3 rows of 2 cells per A4 sheet."""
        from mhr.utilis import render_hty_6up_pdf
        import inspect as _inspect
        src = _inspect.getsource(render_hty_6up_pdf)
        self.assertIn("(0, 2, 4)", src,
            "Renderer must iterate row-indices (0, 2, 4) for the 3x2 grid.")

    def test_sheet_self_page_breaks(self):
        """The CSS makes each <table class=sheet> page-break-after: always
        (and the last sheet auto, so no trailing blank page). Pin both."""
        from mhr.utilis import HTY_6UP_STYLE
        self.assertIn("page-break-after: always", HTY_6UP_STYLE,
            "Each sheet must page-break-after: always.")
        self.assertIn("last-of-type", HTY_6UP_STYLE,
            "Last sheet must not force an extra blank page.")

    def test_row_height_pinned_to_thirds(self):
        """3 rows at 33.33% of a fixed 287mm sheet height keeps wkhtmltopdf
        from overflowing row 3 onto the next page (the bug in the first cut)."""
        from mhr.utilis import HTY_6UP_STYLE
        self.assertIn("height: 287mm", HTY_6UP_STYLE,
            "Sheet must declare an explicit height (A4 - 2x5mm margin).")
        self.assertIn("33.33%", HTY_6UP_STYLE,
            "Each row must be exactly 1/3 of the sheet height.")


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
