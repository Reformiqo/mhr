"""MI1-I80 (Raj 2026-07-14): the Container form's 8 Item Specification
Link fields (glue / pulp / lusture / grade / fsc / product / type /
colour) all showed the full unfiltered list of Item Specifications.
Users trying to create HTY containers couldn't find newly-created
Product / Colour / Type records because Glue/Lusture/Pulp/etc entries
buried them.

Fix: setup handler adds a set_query per field that filters by the
matching specification_type — Product dropdown → only Product specs,
Colour → only Colour, and so on.
"""
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _container_js():
    path = os.path.join(
        frappe.get_app_path("mhr"),
        "mhr/doctype/container/container.js",
    )
    with open(path) as fh:
        return fh.read()


class TestContainerSetQueryFilters(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _container_js()

    def test_marker_present(self):
        self.assertIn("MI1-I80", self.src,
            "container.js must carry the MI1-I80 marker.")

    def test_all_eight_link_fields_mapped(self):
        """All 8 Container Link fields (Item Specification) must be
        listed in the spec_type_by_field mapping — otherwise their
        dropdowns fall back to unfiltered."""
        for field in ("glue", "pulp", "lusture", "grade", "fsc",
                      "product", "type", "colour"):
            self.assertIn(
                f'{field}:',
                self.src,
                f"Field {field!r} must be in the spec_type_by_field "
                "mapping in container.js setup.",
            )

    def test_mapping_uses_correct_specification_type(self):
        """Pin the string mapping so a rename of one side (e.g. field
        renamed 'product' → 'primary_product') doesn't silently break
        the filter."""
        mapping = {
            "glue":    "Glue",
            "pulp":    "Pulp",
            "lusture": "Lusture",
            "grade":   "Grade",
            "fsc":     "FSC",
            "product": "Product",
            "type":    "Type",
            "colour":  "Colour",
        }
        for field, spec in mapping.items():
            self.assertIn(f'"{spec}"', self.src,
                f"Specification type {spec!r} must appear in the mapping.")

    def test_set_query_installed_via_loop(self):
        """The set_query must be installed via a loop over the mapping,
        not one call per field — pin the code shape so adding a new
        Link field only requires a mapping entry."""
        self.assertIn(
            "for (const [field, spec_type] of Object.entries(spec_type_by_field))",
            self.src,
            "set_query must be installed via a loop over the mapping.",
        )
        self.assertIn(
            "frm.set_query(field,",
            self.src,
            "Loop body must call frm.set_query(field, …).",
        )

    def test_filter_shape_correct(self):
        """The filter must be { specification_type: spec_type }, keyed
        on the exact Custom Field name on Item Specification."""
        self.assertIn(
            "filters: { specification_type: spec_type }",
            self.src,
            "Filter shape must be { specification_type: spec_type }.",
        )


class TestItemSpecificationHasField(FrappeTestCase):
    """Regression pin — filter is useless if the field's fieldname
    is renamed on the Item Specification side."""

    def test_item_specification_has_specification_type_field(self):
        meta = frappe.get_meta("Item Specification", cached=False)
        fieldnames = {df.fieldname for df in meta.fields}
        self.assertIn(
            "specification_type",
            fieldnames,
            "Item Specification.specification_type must exist — the "
            "Container.js filter keys off it.",
        )


class TestContainerLinkFieldsStillTargetItemSpecification(FrappeTestCase):
    """Regression pin — if a Container Link field is retargeted to a
    different DocType, the filter would silently apply to the wrong
    dropdown."""

    def test_all_eight_fields_are_link_to_item_specification(self):
        meta = frappe.get_meta("Container", cached=False)
        by_field = {df.fieldname: df for df in meta.fields}
        for field in ("glue", "pulp", "lusture", "grade", "fsc",
                      "product", "type", "colour"):
            df = by_field.get(field)
            self.assertIsNotNone(
                df, f"Container.{field} must exist.",
            )
            self.assertEqual(
                df.fieldtype, "Link",
                f"Container.{field} must be Link (not renamed to Select "
                "or Data).",
            )
            self.assertEqual(
                df.options, "Item Specification",
                f"Container.{field} must target Item Specification.",
            )
