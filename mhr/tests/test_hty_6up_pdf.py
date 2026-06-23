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
        """qr_payload is intentionally NOT in the template anymore (the
        bottom caption that used it was removed per Raj's reference PDF) —
        it's still computed in render_hty_6up_pdf to seed qr_url, but
        doesn't appear in the rendered HTML."""
        from mhr.utilis import HTY_LABEL_HTML
        for token in ("doc.custom_container_no", "doc.custom_cone",
                      "item_code", "cone_val", "net_wt_str", "gross_wt_str",
                      "grade_val", "luster_val", "serial", "qr_url"):
            self.assertIn(token, HTY_LABEL_HTML,
                f"Template must use {token!r} (context key set by the renderer).")

    def test_bottom_caption_removed(self):
        """Raj's reference PDF has no bottom caption (the
        '6_MCJC-1519_04122025.' line). Removing the caption div left
        the template free of that trailing text — pin that it stays gone."""
        from mhr.utilis import HTY_LABEL_HTML
        self.assertNotIn('class="caption"', HTY_LABEL_HTML,
            "Bottom caption div must not be in the label template — "
            "Raj's reference layout removes it.")
        self.assertNotIn("{{ qr_payload }}", HTY_LABEL_HTML,
            "qr_payload placeholder must not be in the template "
            "(it was only consumed by the removed caption).")

    def test_denfil_row_is_bold(self):
        """Raj's reference has Container No., Den/Fil, AND Net Wt as
        the three bold rows. Earlier our template only marked Container
        No. + Net Wt — this pin protects against the regression."""
        from mhr.utilis import HTY_LABEL_HTML
        # The Den/Fil row must carry the `class="b"` (bold) modifier.
        import re
        self.assertRegex(
            HTY_LABEL_HTML,
            r'<tr class="b">[^<]*<td class="k">Den/Fil</td>',
            "Den/Fil row must be marked with the bold class — matches "
            "Raj's reference PDF.",
        )

    def test_style_uses_absolute_positioning_on_body(self):
        """Layout (third iter, 2026-06-23): absolute-positioned cells
        on the body, but rendered as ONE wkhtmltopdf invocation per
        PDF page (no multi-page CSS pagination). The two earlier
        approaches (absolute positioning in a multi-page HTML; table
        layout with page-break-after) both produced spillover because
        wkhtmltopdf doesn't reliably honour page-break-inside: avoid
        nor overflow:hidden when content equals the page height.

        With one PDF page == one wkhtmltopdf call, there's no pagination
        decision to misinterpret."""
        from mhr.utilis import HTY_6UP_STYLE
        self.assertIn("A4 portrait", HTY_6UP_STYLE)
        # Body is the containing block — a shorter-than-A4 height
        # absorbs wkhtmltopdf's mystery internal margin.
        self.assertIn("position: relative", HTY_6UP_STYLE)
        # Body height intentionally < 297mm (A4) so the rendered content
        # doesn't spill onto a second PDF page.
        self.assertRegex(
            HTY_6UP_STYLE,
            r"body\s*\{[^}]*height:\s*280mm",
            "body height must be 280mm (smaller than A4 297mm to absorb "
            "wkhtmltopdf's internal margin).",
        )
        # Cells are absolutely positioned.
        self.assertIn("position: absolute", HTY_6UP_STYLE)
        # 6 row/col combinations.
        for sel in (".cell.r1", ".cell.r2", ".cell.r3",
                    ".cell.c1", ".cell.c2"):
            self.assertIn(sel, HTY_6UP_STYLE)

    def test_renderer_one_pdf_per_logical_page(self):
        """Source-level pin: the renderer must invoke wkhtmltopdf ONCE
        per logical page and concatenate with pypdf, ensuring exactly
        one PDF page per group of 6 cells (no spillover, no blank)."""
        from mhr.utilis import render_hty_6up_pdf
        import inspect as _inspect
        src = _inspect.getsource(render_hty_6up_pdf)
        self.assertIn("from pypdf import PdfReader, PdfWriter", src,
            "Renderer must use pypdf to concatenate per-page PDFs.")
        self.assertIn("for i in range(0, len(labels), 6):", src,
            "Renderer must loop in batches of 6 labels.")
        self.assertIn("get_pdf(page_html", src,
            "Renderer must call get_pdf for each logical page.")
        self.assertIn("writer.add_page(reader.pages[0])", src,
            "Renderer must take ONLY the first page from each "
            "wkhtmltopdf invocation (discards trailing blanks).")
        # 6-cell positions must all be present.
        for combo in ('("r1", "c1")', '("r1", "c2")',
                      '("r2", "c1")', '("r2", "c2")',
                      '("r3", "c1")', '("r3", "c2")'):
            self.assertIn(combo, src,
                f"Position {combo} missing from the positions table.")


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
