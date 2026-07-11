"""MI1-I71 (Raj 2026-06-24, comments through 2026-06-27) — HTY popup
Client Script requirements checklist. All items are actually delivered
by the MI1-I72 P1..P6 chain in the 'HTY & VFY' script's
show_hty_batch_dialog. This test file exists to explicitly pin each
line-item from Raj's ticket comment so a future regression that
partially undoes MI1-I72 also flags MI1-I71.

Raj's requirements (2026-06-27):
  1. If transaction_type is HTY, a popup is created.
  2. Rename column headers: Lusture → Colour, Pulp → Type, Glue → Product.
  3. Data should be simply CENT (not "Glue-CENT") — strip label prefix.
  4. On Select, custom_colour, custom_product, custom_type are updated
     on the Delivery Note form.
  5. Remove columns from popup: FSC, Cross Section, Merge No, Supplier,
     Expiry Date.
  6. Only one Cone column should be displayed.
  7. Existing functionality must not be affected.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _hty_vfy_script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "HTY & VFY":
            return cs.get("script", "")
    raise AssertionError("'HTY & VFY' Client Script missing from fixtures.")


class TestI71RaqsRequirements(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_popup_fires_only_for_hty(self):
        """Requirement 1: popup only fires when transaction_type=HTY."""
        self.assertIn(
            'if (frm.doc.transaction_type === "HTY")',
            self.src,
            "custom_container_no handler must gate the HTY popup on "
            "transaction_type='HTY'.",
        )

    def test_rename_lusture_pulp_glue_headers(self):
        """Requirement 2: column headers renamed."""
        # The popup <thead> lists: Colour, Type, Product (not
        # Lusture / Pulp / Glue).
        # Look for the specific <th> texts in show_hty_batch_dialog.
        header_block = self.src[self.src.find("<thead>"):self.src.find("</thead>", self.src.find("<thead>"))]
        self.assertIn("<th>Colour</th>", header_block,
            "Popup header must read Colour (was Lusture).")
        self.assertIn("<th>Type</th>", header_block,
            "Popup header must read Type (was Pulp).")
        self.assertIn("<th>Product</th>", header_block,
            "Popup header must read Product (was Glue).")

    def test_data_strips_label_prefix(self):
        """Requirement 3: data shows CENT not Glue-CENT — strip prefix."""
        self.assertIn(
            "strip_label_prefix(batch.custom_glue)",
            self.src,
            "Product column value must run through strip_label_prefix "
            "so 'Glue-CENT' renders as 'CENT'.",
        )
        self.assertIn(
            "strip_label_prefix(batch.custom_pulp)",
            self.src,
            "Type column value must run through strip_label_prefix.",
        )
        self.assertIn(
            "strip_label_prefix(batch.custom_lusture)",
            self.src,
            "Colour column value must run through strip_label_prefix.",
        )
        self.assertIn(
            "function strip_label_prefix",
            self.src,
            "strip_label_prefix helper must exist.",
        )

    def test_select_populates_dn_product_type_colour(self):
        """Requirement 4: custom_product/type/colour updated on Select.
        Delivered by MI1-I72 P6 which fetches from Container."""
        for line in (
            "frm.set_value('custom_product', c.product || '')",
            "frm.set_value('custom_type',    c.type    || '')",
            "frm.set_value('custom_colour',  c.colour  || '')",
        ):
            self.assertIn(line, self.src,
                f"Missing set_value: {line!r}")

    def test_removed_columns_absent_from_popup_header(self):
        """Requirement 5: no FSC, Cross Section, Merge No, Supplier,
        Expiry Date."""
        header_block = self.src[self.src.find("<thead>"):self.src.find("</thead>", self.src.find("<thead>"))]
        for banned in ("FSC", "Cross Section", "Merge No", "Supplier</th>", "Expiry Date"):
            self.assertNotIn(banned, header_block,
                f"Popup header must NOT include {banned!r} — MI1-I71 requirement.")

    def test_single_cone_column(self):
        """Requirement 6: only one Cone column visible in the popup."""
        header_block = self.src[self.src.find("<thead>"):self.src.find("</thead>", self.src.find("<thead>"))]
        # Old duplicate would have two <th>Cone</th> in the same <thead>.
        cone_count = header_block.count("<th>Cone</th>")
        self.assertEqual(
            cone_count, 1,
            f"Popup header must have exactly ONE <th>Cone</th>. Got {cone_count}.",
        )

    def test_batch_fields_still_populated_on_select(self):
        """Requirement 7: existing functionality preserved — the set_value
        chain for glue/pulp/lusture/grade/lot_no/fsc/cone must survive."""
        for expected in (
            "frm.set_value('custom_glue',    last_batch.custom_glue",
            "frm.set_value('custom_pulp',    last_batch.custom_pulp",
            "frm.set_value('custom_lusture', last_batch.custom_lusture",
            "frm.set_value('custom_grade',   last_batch.custom_grade",
            "frm.set_value('custom_lot_no',  last_batch.custom_lot_no",
            "frm.set_value('custom_cone',    last_batch.custom_cone",
        ):
            self.assertIn(expected, self.src,
                f"MI1-I71 requirement 7 — existing set_value {expected!r} "
                "must survive.")


class TestI71ScriptEnabledAndInFixtures(FrappeTestCase):
    """Original I71 report: 'The Client Script seems to have been
    deleted.' Pin that it's shipped via fixtures + enabled."""

    def test_script_present_in_fixtures(self):
        path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
        with open(path) as fh:
            data = json.load(fh)
        cs = next((x for x in data if x.get("name") == "HTY & VFY"), None)
        self.assertIsNotNone(cs,
            "'HTY & VFY' Client Script (the HTY popup) must live in "
            "fixtures — otherwise a fresh site loses it on migrate.")
        self.assertEqual(cs.get("enabled"), 1,
            "'HTY & VFY' Client Script must be enabled.")
        self.assertEqual(cs.get("dt"), "Delivery Note",
            "'HTY & VFY' Client Script must target Delivery Note.")
        self.assertEqual(cs.get("module"), "Mhr",
            "'HTY & VFY' Client Script must live in the Mhr module.")
