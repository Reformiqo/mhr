"""MI1-I79 (Raj 2026-07-14): the Item Specification.specification_type
Select field was missing Product, Type, and Colour — so users couldn't
create Item Specification records of those types, and the Container
form's Link fields (product / type / colour → Item Specification) had
no valid values to pick. Downstream the DN Container Info Product /
Type / Colour fields stayed empty.

Fix: extend the Select's options to include the three HTY-only spec
types.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _doctype_json():
    path = os.path.join(
        frappe.get_app_path("mhr"),
        "mhr/doctype/item_specification/item_specification.json",
    )
    with open(path) as fh:
        return json.load(fh)


class TestSpecificationTypeOptions(FrappeTestCase):

    def _options(self):
        for f in _doctype_json()["fields"]:
            if f["fieldname"] == "specification_type":
                return (f.get("options") or "").split("\n")
        raise AssertionError("specification_type field missing from DocType JSON.")

    def test_original_six_types_preserved(self):
        """Regression pin — the original six types must stay."""
        opts = self._options()
        for st in ("Glue", "Lusture", "Grade", "Pulp", "FSC", "Cross Section"):
            self.assertIn(st, opts,
                f"Original specification_type {st!r} must remain in the options list.")

    def test_new_three_types_added(self):
        opts = self._options()
        for st in ("Product", "Type", "Colour"):
            self.assertIn(st, opts,
                f"MI1-I79 — {st!r} must be in the specification_type options "
                "so users can create Item Specification records of that type.")

    def test_options_loaded_in_meta(self):
        """The DocType JSON change must actually reach the live meta."""
        meta = frappe.get_meta("Item Specification", cached=False)
        for df in meta.fields:
            if df.fieldname == "specification_type":
                opts = (df.options or "").split("\n")
                for st in ("Product", "Type", "Colour"):
                    self.assertIn(st, opts,
                        f"Runtime meta must expose {st!r} in options — "
                        "run `bench --site <site> reload-doctype "
                        "'Item Specification'` if this fails.")
                return
        self.fail("specification_type field missing from meta.")


class TestCreatingNewSpecTypesWorks(FrappeTestCase):
    """End-to-end: create → save → delete for each new type."""

    NEW_TYPES = ("Product", "Type", "Colour")
    TEST_VALUE = "MI1-I79-TESTVALUE"

    def tearDown(self):
        for st in self.NEW_TYPES:
            name = f"{st}-{self.TEST_VALUE}"
            if frappe.db.exists("Item Specification", name):
                frappe.delete_doc(
                    "Item Specification", name,
                    ignore_permissions=True, force=1,
                )

    def test_can_create_and_delete_each_new_type(self):
        for st in self.NEW_TYPES:
            doc = frappe.new_doc("Item Specification")
            doc.specification_type = st
            doc.value = self.TEST_VALUE
            doc.insert(ignore_permissions=True)
            self.assertTrue(
                frappe.db.exists("Item Specification", doc.name),
                f"Creating an Item Specification of type {st!r} must succeed.",
            )
            frappe.delete_doc(
                "Item Specification", doc.name,
                ignore_permissions=True, force=1,
            )
            self.assertFalse(
                frappe.db.exists("Item Specification", doc.name),
                f"Deleting {doc.name!r} must succeed.",
            )
