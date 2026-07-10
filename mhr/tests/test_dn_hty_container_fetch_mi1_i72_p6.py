"""MI1-I72 P6 (Raj 2026-07-10) — after picking a batch in the HTY
'Select Batch' modal, Product / Type / Colour stayed blank because
Batch has no custom_product / custom_type / custom_colour fields.

MI1-I72 P2 removed the erroneous copies from batch.custom_glue → DN
custom_product etc. (Batch was the wrong source). The right source is
CONTAINER — Container.product / Container.type / Container.colour are
Link fields (→ Item Specification) that hold the canonical HTY spec.

Fix (P6): after the picker sets the last_batch header fields, look up
the Container by (container_no + transaction_type='HTY') and populate
the three DN fields from the Container's product/type/colour.
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


class TestContainerFetchPopulatesProductTypeColour(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_marker_present(self):
        self.assertIn("MI1-I72 P6", self.src,
            "The Container fetch block must carry the MI1-I72 P6 marker "
            "so a future reader knows why the picker fires a second RPC.")

    def test_fetches_from_container_doctype(self):
        self.assertIn("frappe.db.get_list('Container'", self.src,
            "The fetch must go to the Container DocType — that's where "
            "Product / Type / Colour live for HTY.")

    def test_filters_on_container_no_from_last_batch(self):
        """last_batch.custom_container_no is what the picker already has
        in scope — cheaper than another Batch lookup."""
        self.assertIn(
            "container_no: last_batch.custom_container_no",
            self.src,
            "Filter must use last_batch.custom_container_no.",
        )

    def test_filters_on_hty_transaction_type(self):
        """container_no isn't unique across VFY/HTY; pin the HTY filter
        so we don't accidentally read a VFY container's spec fields."""
        self.assertIn(
            "transaction_type: 'HTY'",
            self.src,
            "Container fetch must filter transaction_type = 'HTY' to "
            "avoid picking a VFY container that shares the same "
            "container_no.",
        )

    def test_fetches_product_type_colour_fields(self):
        for f in ("'product'", "'type'", "'colour'"):
            self.assertIn(f, self.src,
                f"Container fetch must request the {f} field.")

    def test_sets_custom_product_from_container_product(self):
        self.assertIn(
            "frm.set_value('custom_product', c.product || '')",
            self.src,
            "DN.custom_product must be set from Container.product.",
        )

    def test_sets_custom_type_from_container_type(self):
        self.assertIn(
            "frm.set_value('custom_type',    c.type    || '')",
            self.src,
            "DN.custom_type must be set from Container.type.",
        )

    def test_sets_custom_colour_from_container_colour(self):
        self.assertIn(
            "frm.set_value('custom_colour',  c.colour  || '')",
            self.src,
            "DN.custom_colour must be set from Container.colour.",
        )

    def test_no_regression_from_p2(self):
        """MI1-I72 P2 removed the wrong-source copies. P6 must not
        resurrect them under a different guise."""
        self.assertNotIn(
            "frm.set_value('custom_product', last_batch.custom_glue",
            self.src,
            "P2 regression — the wrong-source Glue → Product copy must "
            "stay gone; P6 sources these fields from Container instead.",
        )
        self.assertNotIn(
            "frm.set_value('custom_type',    last_batch.custom_pulp",
            self.src,
        )
        self.assertNotIn(
            "frm.set_value('custom_colour',  last_batch.custom_lusture",
            self.src,
        )


class TestContainerFetchLayeredWithPriorFixes(FrappeTestCase):
    """P6 layers on top of P1, P2, P3, P4, P5, MI1-I75. Pin that the
    prior fixes still hold."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_p3_direct_denier_assignment_still_present(self):
        """P3 replaced set_value on custom_denier with direct doc write
        to avoid the double-modal cascade — pin it survived the P6 edit."""
        self.assertIn(
            "frm.doc.custom_denier = last_batch.item || '';",
            self.src,
            "P3 regression — custom_denier must still be assigned via "
            "frm.doc directly (not set_value) to avoid re-firing the "
            "custom_denier handler and opening a second modal.",
        )

    def test_p4_get_all_batches_full_fields_still_present(self):
        self.assertIn(
            "'custom_supplier_batch_no'",
            self.src,
            "P4 regression — get_all_batches must still request "
            "custom_supplier_batch_no.",
        )
        self.assertIn(
            "'manufacturing_date'",
            self.src,
            "P4 regression — get_all_batches must still request "
            "manufacturing_date.",
        )

    def test_i75_sort_still_wired(self):
        """MI1-I75 sort after fetch — pin it stayed."""
        self.assertIn("MI1-I75", self.src,
            "MI1-I75 sort marker must still be present in HTY & VFY.")
        self.assertIn(
            "String(a.custom_supplier_batch_no || '').localeCompare(",
            self.src,
            "MI1-I75 sort key must remain custom_supplier_batch_no.",
        )
