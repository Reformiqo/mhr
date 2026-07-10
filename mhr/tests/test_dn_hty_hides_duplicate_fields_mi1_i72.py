"""MI1-I72 (Raj 2026-07-10) — DN HTY view was showing DUPLICATE fields:

    Colour   ← custom_lusture (relabeled)   / Colour ← custom_colour
    Product  ← custom_glue    (relabeled)   / Product ← custom_product
    Type     ← custom_pulp    (relabeled)   / Type   ← custom_type

The originals should hide entirely in HTY; the new fields are the
HTY-visible ones going forward. Enforced by the
'MI1-I39 — Delivery Note HTY Mode' Client Script.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_dn_hty_client_script_source():
    """Read the script text as-shipped in fixtures — that's the source
    of truth for what gets installed on migrate. Avoids test-env DB
    drift on developer machines."""
    fixtures_path = os.path.join(
        frappe.get_app_path("mhr"), "fixtures", "client_script.json"
    )
    with open(fixtures_path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "MI1-I39 — Delivery Note HTY Mode":
            return cs.get("script", "")
    raise AssertionError(
        "Client Script 'MI1-I39 — Delivery Note HTY Mode' missing from fixtures — "
        "did you forget to run 'bench export-fixtures --app mhr'?"
    )


class TestHtyHidesDuplicateFields(FrappeTestCase):
    """The three VFY-only fields must appear in the hide_in_hty list."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _load_dn_hty_client_script_source()

    def _hide_list_covers(self, fieldname):
        """Extract the hide_in_hty array (roughly) and check membership.
        Uses a substring match against the array literal — script is
        canonical enough that this is stable."""
        # Snap the array bounds so we don't accidentally match the
        # fieldname elsewhere (e.g. in a relabel map that shouldn't
        # exist any more).
        start = self.src.find("const hide_in_hty = [")
        self.assertGreater(start, 0,
            "hide_in_hty array must be present in the client script.")
        end = self.src.find("]", start)
        self.assertGreater(end, start, "Malformed hide_in_hty array.")
        block = self.src[start:end]
        return f"'{fieldname}'" in block

    def test_custom_lusture_hidden_in_hty(self):
        self.assertTrue(
            self._hide_list_covers("custom_lusture"),
            "custom_lusture (label: 'Lusture' / 'Colour') must be in "
            "hide_in_hty — MI1-I72 duplicate-field cleanup.",
        )

    def test_custom_glue_hidden_in_hty(self):
        self.assertTrue(
            self._hide_list_covers("custom_glue"),
            "custom_glue (label: 'Glue' / 'Product') must be in "
            "hide_in_hty — MI1-I72 duplicate-field cleanup.",
        )

    def test_custom_pulp_hidden_in_hty(self):
        self.assertTrue(
            self._hide_list_covers("custom_pulp"),
            "custom_pulp (label: 'Pulp' / 'Type') must be in "
            "hide_in_hty — MI1-I72 duplicate-field cleanup.",
        )

    def test_relabel_block_removed(self):
        """The old relabel map (custom_lusture -> 'Colour', etc.) must
        be gone — hiding + native labels on the new fields replaces
        the label swap."""
        # The clearest signature of the OLD block: the literal
        # "custom_lusture': hty ? 'Colour'" pairing.
        self.assertNotIn(
            "custom_lusture': hty ? 'Colour'", self.src,
            "The old label-swap for custom_lusture must be removed — "
            "the field is now hidden in HTY, not relabeled.",
        )
        self.assertNotIn(
            "custom_glue':    hty ? 'Product'", self.src,
            "The old label-swap for custom_glue must be removed.",
        )
        self.assertNotIn(
            "custom_pulp':    hty ? 'Type'", self.src,
            "The old label-swap for custom_pulp must be removed.",
        )

    def test_new_fields_untouched_by_script(self):
        """Regression pin: the client script must NOT hide the new
        custom_colour / custom_product / custom_type fields — they're
        the HTY-visible ones by design."""
        for fn in ("custom_colour", "custom_product", "custom_type"):
            self.assertNotIn(
                f"'{fn}'", self.src,
                f"{fn} must NOT appear in the client script — it's a "
                "native field always visible on the DN form.",
            )

    def test_pre_existing_hides_preserved(self):
        """Regression: the four fields the earlier revision hid in HTY
        (custom_fsc, custom_merge_no, custom_cross_section,
        custom_production_date) must still be in hide_in_hty."""
        for fn in (
            "custom_fsc", "custom_merge_no",
            "custom_cross_section", "custom_production_date",
        ):
            self.assertTrue(
                self._hide_list_covers(fn),
                f"{fn} must remain in hide_in_hty — regression from an "
                "earlier MI1-I39 fix.",
            )

    def test_lot_picker_and_naming_series_still_wired(self):
        """The MI1-I39 / MI1-I39 P2-F wiring must survive the top-of-
        function rewrite."""
        self.assertIn("mi1_i39_add_pick_by_lot_button", self.src,
            "Lot picker call must survive.")
        self.assertIn("mi1_i39_apply_dn_naming_series", self.src,
            "Naming-series handler must survive.")


class TestNewParallelFieldsExist(FrappeTestCase):
    """The MI1-I72 cleanup only makes sense if the NEW parallel fields
    actually exist on the Delivery Note. Verify they're present in the
    Custom Field fixture."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(
            frappe.get_app_path("mhr"), "fixtures", "custom_field.json"
        )
        with open(path) as fh:
            data = json.load(fh)
        cls.dn_fields = {
            f.get("fieldname"): f
            for f in data
            if f.get("dt") == "Delivery Note"
        }

    def test_custom_colour_present(self):
        self.assertIn("custom_colour", self.dn_fields,
            "Delivery Note.custom_colour must exist — the visible "
            "'Colour' field in HTY.")
        self.assertEqual(self.dn_fields["custom_colour"].get("label"), "Colour")

    def test_custom_product_present(self):
        self.assertIn("custom_product", self.dn_fields,
            "Delivery Note.custom_product must exist.")
        self.assertEqual(self.dn_fields["custom_product"].get("label"), "Product")

    def test_custom_type_present(self):
        self.assertIn("custom_type", self.dn_fields,
            "Delivery Note.custom_type must exist.")
        self.assertEqual(self.dn_fields["custom_type"].get("label"), "Type")
