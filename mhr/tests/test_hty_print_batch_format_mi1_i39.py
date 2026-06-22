"""MI1-I39 Phase 2E — HTY Batch Label print format tests.

In HTY mode, Print Batch generates labels using a new "HTY Batch Label"
print format that relabels Lustre→Colour, Glue→Product, Pulp→Type.
VFY mode keeps the existing "NB" Print Designer format untouched
(FRD hard rule).
"""

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.doctype.print_batch import print_batch as pb_mod


class TestHTYBatchLabelFormatExists(FrappeTestCase):
    """The 'HTY Batch Label' Print Format must be present + enabled."""

    def test_print_format_exists(self):
        self.assertTrue(
            frappe.db.exists("Print Format", "HTY Batch Label"),
            "HTY Batch Label print format must exist for HTY-mode runs.",
        )

    def test_print_format_meta(self):
        pf = frappe.db.get_value(
            "Print Format", "HTY Batch Label",
            ["doc_type", "module", "disabled", "standard", "print_format_type"],
            as_dict=True,
        )
        self.assertEqual(pf.doc_type, "Batch",
            "HTY Batch Label must target the Batch DocType.")
        self.assertEqual(pf.module, "Mhr",
            "HTY Batch Label must be in module=Mhr so it ships via fixtures.")
        self.assertEqual(pf.disabled, 0)
        self.assertEqual(pf.print_format_type, "Jinja")

    def test_html_relabels_hty_fields(self):
        html = frappe.db.get_value("Print Format", "HTY Batch Label", "html")
        self.assertIsNotNone(html)
        # The HTY label must show Colour/Product/Type as labels (the
        # FRD's HTY renames), pulling from the Meher fieldnames in DB.
        for label in ("Colour:", "Product:", "Type:"):
            self.assertIn(
                label, html,
                f"HTY Batch Label must render {label!r} (HTY rename of Lusture/Glue/Pulp).",
            )
        # The Meher fieldnames must be the data source.
        for field in ("custom_lusture", "custom_glue", "custom_pulp"):
            self.assertIn(
                field, html,
                f"HTY Batch Label must read from doc.{field} — that's where the data lives.",
            )


class TestPrintBatchPicksHTYFormatInHTYMode(FrappeTestCase):
    """`PrintBatch.generate_multi_pdf_url` must pick HTY Batch Label when
    transaction_type=HTY, NB otherwise."""

    def test_format_selection_branches_on_transaction_type(self):
        src = inspect.getsource(pb_mod.PrintBatch.generate_multi_pdf_url)
        self.assertIn(
            "transaction_type", src,
            "Print Batch's PDF generator must consult transaction_type.",
        )
        self.assertIn(
            "render_hty_6up_pdf", src,
            "HTY-mode runs must render the 6-up HTY label PDF (MI1-I62) "
            "instead of the old HTY Batch Label download format.",
        )
        self.assertIn(
            '"NB"', src,
            "VFY-mode runs must keep the existing NB format (FRD hard rule).",
        )

    def test_default_is_normal_when_field_missing(self):
        """If a legacy Print Batch lacks transaction_type, the format
        defaults to NB — never crashes."""
        src = inspect.getsource(pb_mod.PrintBatch.generate_multi_pdf_url)
        self.assertIn(
            'or "VFY"', src,
            "Missing/None transaction_type must default to 'VFY' so legacy "
            "Print Batch docs continue to use NB.",
        )
