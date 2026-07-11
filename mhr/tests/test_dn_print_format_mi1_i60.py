"""MI1-I60 (Raj 2026-06-27) — the 'Meher Packing Slip' Delivery Note
print format needed:

  * Column sequence: SN | Container No. | Denier | Lot No. | Pallet No.
    | Net KGS | Gross KGS | No. of Cones
  * Description block in the header area
  * Gross Weight fetching (from DN Item.custom_gross_weight, which now
    fetches from Batch via the MI1-I63 chain)
  * Full company address (dynamic) — replace hardcoded 'MEHER CREATIONS'
    branding so a multi-company install prints its own company.
"""
import json
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase


def _packing_slip_html():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "print_format.json")
    with open(path) as fh:
        data = json.load(fh)
    for pf in data:
        if pf.get("name") == "Meher Packing Slip":
            return pf.get("html", "")
    raise AssertionError("Meher Packing Slip missing from print_format.json fixture.")


class TestColumnOrderMatchesRajsSpec(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = _packing_slip_html()

    def test_thead_present(self):
        m = re.search(r"<thead>(.*?)</thead>", self.html, re.DOTALL)
        self.assertIsNotNone(m, "Print format must have a <thead> block.")

    def test_columns_in_rajs_order(self):
        m = re.search(r"<thead>(.*?)</thead>", self.html, re.DOTALL)
        head = m.group(1)
        # Extract <th> text nodes
        ths = re.findall(r"<th[^>]*>(.*?)</th>", head)
        ths = [t.strip() for t in ths]
        expected = [
            "SN",
            "Container No.",
            "Denier",
            "Lot No.",
            "Pallet No.",
            "Net KGS",
            "Gross KGS",
            "No. of Cones",
        ]
        self.assertEqual(
            ths, expected,
            f"Column order must match Raj's spec exactly. Got {ths!r}",
        )

    def test_gross_weight_column_reads_from_dn_item(self):
        """The Gross KGS column body must render item.custom_gross_weight."""
        self.assertIn(
            "it.custom_gross_weight",
            self.html,
            "Gross KGS column must render item.custom_gross_weight — the "
            "field that MI1-I63 backfills from Batch.custom_gross_weight.",
        )

    def test_denier_column_reads_item_code(self):
        self.assertIn(
            "it.item_code",
            self.html,
            "Denier column must render item.item_code (Meher's denier "
            "is the item code convention).",
        )

    def test_pallet_no_column_reads_supplier_batch_no(self):
        self.assertIn(
            "it.custom_supplier_batch_no",
            self.html,
            "Pallet No. column must render item.custom_supplier_batch_no "
            "(Pallet No. is stored on the Batch's custom_supplier_batch_no).",
        )


class TestDynamicCompanyAndAddress(FrappeTestCase):
    """The old template hardcoded 'MEHER CREATIONS' and Meher's address.
    Multi-company installs need the print to reflect doc.company."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = _packing_slip_html()

    def test_company_pulled_from_doc(self):
        self.assertIn(
            "doc.company",
            self.html,
            "Template must reference doc.company for the company name.",
        )

    def test_company_upper_display(self):
        self.assertIn(
            "{{ company | upper }}",
            self.html,
            "Company name must render in upper-case for the header block.",
        )

    def test_address_pulled_from_address_doctype(self):
        self.assertIn(
            "Dynamic Link",
            self.html,
            "Template must resolve the company address via Dynamic Link "
            "(the standard Frappe pattern for Address ↔ Company).",
        )
        for f in ("address_line1", "city", "state", "pincode"):
            self.assertIn(
                f, self.html,
                f"Company address block must include {f}.",
            )

    def test_hardcoded_meher_creations_replaced_in_footer(self):
        """The old 'For, MEHER CREATIONS' signature line must be dynamic."""
        self.assertNotIn(
            "For, MEHER CREATIONS",
            self.html,
            "Hardcoded 'MEHER CREATIONS' signature must be replaced with "
            "the dynamic {{ company | upper }} expression.",
        )
        self.assertIn(
            "For, {{ company | upper }}",
            self.html,
            "Footer signature must read 'For, {{ company | upper }}'.",
        )


class TestDescriptionBlock(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = _packing_slip_html()

    def test_description_label_present(self):
        self.assertIn(
            "Description",
            self.html,
            "A Description block must appear in the header area — Raj's "
            "ticket asks for it explicitly.",
        )


class TestPrintFormatShippedViaFixtures(FrappeTestCase):
    """Regression pin — the update must survive a fresh migrate."""

    def test_meher_packing_slip_in_fixture(self):
        path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "print_format.json")
        with open(path) as fh:
            data = json.load(fh)
        pf = next((x for x in data if x.get("name") == "Meher Packing Slip"), None)
        self.assertIsNotNone(pf,
            "Meher Packing Slip must be exported by mhr fixtures.")
        self.assertEqual(pf.get("module"), "Mhr")
        self.assertEqual(pf.get("doc_type"), "Delivery Note")
        self.assertEqual(pf.get("disabled"), 0,
            "Meher Packing Slip must remain enabled.")
