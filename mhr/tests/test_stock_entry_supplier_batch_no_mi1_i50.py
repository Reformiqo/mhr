"""MI1-I50 (reopen, Raj 2026-07-10): Stock Entry Detail's
custom_supplier_batch_no was never populating because the Custom Field
had no fetch_from. Users saw blank Supplier Batch No in every Stock
Entry Items row and in the Stock Entry print format.

Fix:
  1. Set fetch_from='batch_no.custom_supplier_batch_no' on the Custom
     Field so new entries auto-fill on batch_no change.
  2. Heal patch backfills historical rows in one shot.
  3. The Job Work Received flow already copies custom_supplier_batch_no
     from the source Send-to-Subcontractor entry (line 278 of utilis).
"""
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


class TestFetchFromInFixtures(FrappeTestCase):

    def test_stock_entry_detail_custom_supplier_batch_no_has_fetch_from(self):
        path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")
        with open(path) as fh:
            data = json.load(fh)
        cf = next(
            (f for f in data
             if f.get("dt") == "Stock Entry Detail"
             and f.get("fieldname") == "custom_supplier_batch_no"),
            None,
        )
        self.assertIsNotNone(cf,
            "Stock Entry Detail.custom_supplier_batch_no Custom Field must "
            "be in the mhr fixture.")
        self.assertEqual(
            cf.get("fetch_from"),
            "batch_no.custom_supplier_batch_no",
            "Custom Field must fetch from batch_no.custom_supplier_batch_no "
            "so the Supplier Batch No auto-fills on batch_no change.",
        )
        self.assertEqual(cf.get("module"), "Mhr")


class TestHealPatchRegisteredAndCorrect(FrappeTestCase):

    def test_patch_in_patches_txt(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.heal_stock_entry_supplier_batch_no",
            body,
            "Heal patch must be registered in patches.txt.",
        )

    def test_patch_module_loadable(self):
        from mhr.patches.v1_0 import heal_stock_entry_supplier_batch_no as p
        self.assertTrue(callable(getattr(p, "execute", None)))

    def test_patch_guards_and_writes(self):
        from mhr.patches.v1_0 import heal_stock_entry_supplier_batch_no as p
        src = inspect.getsource(p)
        self.assertIn("CHUNK_SIZE", src,
            "Patch must chunk to be safe on prod SE Detail scale.")
        self.assertIn(
            "sed.custom_supplier_batch_no IS NULL OR sed.custom_supplier_batch_no = ''",
            src,
            "Patch must only touch SE Detail rows where sbn is empty — "
            "do not clobber manual overrides.",
        )
        self.assertIn("update_modified=False", src,
            "Heal patch must not bump modified timestamps.")


class TestJobWorkReceivedFlowCopiesSbn(FrappeTestCase):
    """MI1-I50's Job Work Received flow (make_receive_from_subcontractor)
    creates the Draft SE from the source Send-to-Subcontractor entry.
    Pin that it copies custom_supplier_batch_no from the source item
    row — the fetch_from also covers this via batch_no, but explicit
    field-carry avoids relying on the ordering of set_missing_values."""

    def test_supplier_batch_no_in_item_custom_fields_tuple(self):
        from mhr import utilis
        src = inspect.getsource(utilis.make_receive_from_subcontractor)
        # The tuple lists the item-level custom fields carried onto
        # the Draft receive entry.
        self.assertIn(
            '"custom_supplier_batch_no"', src,
            "make_receive_from_subcontractor must carry "
            "custom_supplier_batch_no onto the Draft receive entry.",
        )
