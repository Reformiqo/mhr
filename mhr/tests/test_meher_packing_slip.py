"""Meher Packing Slip print format — structure tests.

Per Raj's design, the Delivery Note PDF should render as a "Packing
Slip (Delivery)" with:
  - MEHER CREATIONS header + address + GST/PAN
  - PS No / Date / Customer / Address block
  - Quality block (Denier / Lusture / Glue / Grade / Sales Person /
    Remarks on the left; Pulp / Merge No / Notes on the right)
  - Items table: Sr | Container | Lot | Box No | Net KG | Cone
  - Totals row + Total Items row
  - Transport / Driver / Created By + "For, MEHER CREATIONS" sign-off

This file pins the format's presence + the HTML scaffold so a
re-export of fixtures or an accidental edit doesn't regress the user's
layout.
"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestMeherPackingSlipExists(FrappeTestCase):

    def test_print_format_registered(self):
        self.assertTrue(
            frappe.db.exists("Print Format", "Meher Packing Slip"),
            "Meher Packing Slip print format must exist.",
        )

    def test_print_format_meta(self):
        d = frappe.db.get_value(
            "Print Format",
            "Meher Packing Slip",
            ["doc_type", "module", "standard", "print_format_type", "disabled"],
            as_dict=True,
        )
        self.assertEqual(d.doc_type, "Delivery Note",
            "Format must target the Delivery Note doctype.")
        self.assertEqual(d.module, "Mhr",
            "Format must be in module=Mhr so it ships via fixtures.")
        self.assertEqual(d.standard, "Yes")
        self.assertEqual(d.print_format_type, "Jinja")
        self.assertEqual(d.disabled, 0)


class TestMeherPackingSlipLayout(FrappeTestCase):
    """Pin the major sections of the template — regression here would
    visibly change Raj's PDF."""

    def setUp(self):
        self.html = frappe.db.get_value(
            "Print Format", "Meher Packing Slip", "html"
        )
        self.assertIsNotNone(self.html)

    def test_company_header(self):
        for marker in (
            "MEHER CREATIONS",
            "Plot No 30,31 Paikki",
            "GST: 24ABWFM0906C1ZF",
            "PAN: ABWFM0906C",
        ):
            self.assertIn(marker, self.html, f"Header missing: {marker!r}")

    def test_packing_slip_title(self):
        self.assertIn("Packing Slip (Delivery)", self.html)

    def test_columns_match_design(self):
        # The 6 column headers from Raj's screenshot.
        for col in ("Sr", "Container", "Lot", "Box No", "Net KG", "Cone"):
            self.assertIn(f">{col}<", self.html,
                f"Items table must have a {col!r} header.")

    def test_item_rendering_pulls_meher_fields(self):
        # Pin the fieldname mapping — Container/Lot/Box No/Cone all come
        # from Meher's custom_ fields on Delivery Note Item.
        for fld in (
            "it.custom_container_no",
            "it.custom_lot_no",
            "it.custom_supplier_batch_no",
            "it.qty",
            "it.custom_cone",
        ):
            self.assertIn(fld, self.html,
                f"Items row must read from {fld} — Raj's data lives there.")

    def test_quality_block_fields(self):
        for fld in (
            "doc.custom_denier",
            "doc.custom_lusture",
            "doc.custom_glue",
            "doc.custom_grade",
            "doc.custom_sales_person",
            "doc.custom_pulp",
            "doc.custom_merge_no",
            "doc.custom_notes",
        ):
            self.assertIn(fld, self.html,
                f"Quality block must read {fld} from the Delivery Note header.")

    def test_totals_row_present(self):
        self.assertIn("Sub Total:", self.html)
        self.assertIn("Total Items:", self.html)

    def test_footer_signature(self):
        self.assertIn("Transport:", self.html)
        self.assertIn("Driver:", self.html)
        self.assertIn("Created By:", self.html)
        self.assertIn("For, MEHER CREATIONS", self.html)
