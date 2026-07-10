"""MI1-I72 P2 (Raj 2026-07-10) — the 'HTY & VFY' Client Script (the
big batch-picker dialog on Delivery Note) was copying
    frm.set_value('custom_product', last_batch.custom_glue    || '');
    frm.set_value('custom_type',    last_batch.custom_pulp    || '');
    frm.set_value('custom_colour',  last_batch.custom_lusture || '');

so Product/Type/Colour prefilled with Glue/Pulp/Lusture — visible on the
form as 'Product = Glue-HTY' etc. Batch has no custom_product/type/
colour fields, so this was just a wrong-source copy.

Fix: remove those three set_value calls. The fields stay blank on the
batch-picker path.

Companion pin: the script must now ship via fixtures (module='Mhr'),
otherwise a bench migrate on prod won't roll the fix out.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_fixture_scripts():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        return json.load(fh)


class TestHtyVfyScriptFixturized(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scripts = _load_fixture_scripts()

    def _hty_vfy(self):
        for cs in self.scripts:
            if cs.get("name") == "HTY & VFY":
                return cs
        return None

    def test_hty_vfy_script_shipped_via_fixtures(self):
        """A NULL-module version was living in the DB only. Migrating a
        fresh site would never install the script and hence never apply
        this fix. Force it into fixtures by requiring module='Mhr'."""
        cs = self._hty_vfy()
        self.assertIsNotNone(cs,
            "'HTY & VFY' Client Script must appear in fixtures/client_script.json "
            "so the fix propagates via bench migrate. Set its module to 'Mhr' if "
            "it's still missing.")
        self.assertEqual(cs.get("module"), "Mhr",
            "'HTY & VFY' Client Script must be in the Mhr module so the "
            "fixture export includes it.")
        self.assertEqual(cs.get("dt"), "Delivery Note")


class TestNoGlueToProductCopy(FrappeTestCase):
    """The three erroneous set_value lines must be gone."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        scripts = _load_fixture_scripts()
        for cs in scripts:
            if cs.get("name") == "HTY & VFY":
                cls.src = cs.get("script", "")
                return
        raise AssertionError("'HTY & VFY' Client Script missing from fixtures.")

    def test_custom_product_not_set_from_batch_glue(self):
        self.assertNotIn(
            "frm.set_value('custom_product', last_batch.custom_glue",
            self.src,
            "The batch-picker dialog must not copy last_batch.custom_glue "
            "into custom_product — MI1-I72 P2.",
        )

    def test_custom_type_not_set_from_batch_pulp(self):
        self.assertNotIn(
            "frm.set_value('custom_type',    last_batch.custom_pulp",
            self.src,
            "The batch-picker dialog must not copy last_batch.custom_pulp "
            "into custom_type — MI1-I72 P2.",
        )

    def test_custom_colour_not_set_from_batch_lusture(self):
        self.assertNotIn(
            "frm.set_value('custom_colour',  last_batch.custom_lusture",
            self.src,
            "The batch-picker dialog must not copy last_batch.custom_lusture "
            "into custom_colour — MI1-I72 P2.",
        )

    def test_marker_comment_present(self):
        """Keep a trail comment so a future reader knows why those three
        lines were removed."""
        self.assertIn("MI1-I72 P2", self.src,
            "Leave the marker comment where the erroneous copies used to be.")

    def test_prior_batch_metadata_copies_preserved(self):
        """Regression: the legitimate header copies from last_batch —
        custom_glue / custom_pulp / custom_lusture / custom_grade /
        custom_lot_no / custom_fsc / custom_cone / custom_denier — must
        survive."""
        for expected in (
            "frm.set_value('custom_glue',    last_batch.custom_glue",
            "frm.set_value('custom_pulp',    last_batch.custom_pulp",
            "frm.set_value('custom_lusture', last_batch.custom_lusture",
            "frm.set_value('custom_grade',   last_batch.custom_grade",
            "frm.set_value('custom_lot_no',  last_batch.custom_lot_no",
            "frm.set_value('custom_fsc',     last_batch.custom_fsc",
            "frm.set_value('custom_cone',    last_batch.custom_cone",
            "frm.set_value('custom_denier',  last_batch.item",
        ):
            self.assertIn(expected, self.src,
                f"Legitimate copy {expected!r} must remain — only the "
                "custom_product/type/colour trio was erroneous.")

    def test_clear_batch_fields_still_covers_new_fields(self):
        """When the container is cleared, the new fields should still
        be blanked. That happens via the clear_batch_fields helper — pin
        that it still lists them so residual values don't linger."""
        for fn in ("'custom_product'", "'custom_type'", "'custom_colour'"):
            self.assertIn(fn, self.src,
                f"clear_batch_fields helper must still list {fn} so a container "
                "reset blanks the field.")
