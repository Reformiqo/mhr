"""MI1-I72 P7 (Raj 2026-07-13): Colour, Product, Type are HTY-only
fields — they must be hidden on VFY Delivery Notes. Symmetric to the
existing MI1-I72 P1 rule that hides Lusture, Glue, Pulp on HTY
Delivery Notes.

The corresponding fields:
  * VFY-only (hide in HTY): custom_lusture, custom_glue, custom_pulp
  * HTY-only (hide in VFY): custom_colour, custom_product, custom_type

Delivered by the mi1_i39_apply_dn_hty() function in the
'MI1-I39 — Delivery Note HTY Mode' Client Script.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "MI1-I39 — Delivery Note HTY Mode":
            return cs.get("script", "")
    raise AssertionError("MI1-I39 script missing from fixtures.")


class TestHideInVfyBlockInstalled(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script()

    def test_marker_present(self):
        self.assertIn("MI1-I72 P7", self.src,
            "MI1-I39 script must carry the MI1-I72 P7 marker.")

    def test_hide_in_vfy_list_covers_all_three_hty_only_fields(self):
        self.assertIn("'custom_colour'", self.src,
            "custom_colour must be in the hide_in_vfy list.")
        self.assertIn("'custom_product'", self.src,
            "custom_product must be in the hide_in_vfy list.")
        self.assertIn("'custom_type'", self.src,
            "custom_type must be in the hide_in_vfy list.")

    def test_hide_flag_is_inverse_of_hty(self):
        """VFY DNs (hty=false) must HIDE these fields (hidden=1);
        HTY DNs (hty=true) must SHOW them (hidden=0). The set_df_property
        expression must evaluate hty ? 0 : 1 (opposite of hide_in_hty)."""
        self.assertIn(
            "hty ? 0 : 1",
            self.src,
            "The hide_in_vfy toggle must be `hty ? 0 : 1` — inverse of "
            "hide_in_hty. Any other polarity would leave the fields "
            "visible in VFY.",
        )


class TestSymmetryWithHideInHty(FrappeTestCase):
    """The three VFY-only fields (Lusture/Glue/Pulp) must still be
    hidden in HTY — pin that MI1-I72 P7 hasn't regressed P1."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script()

    def test_vfy_only_fields_still_in_hide_in_hty(self):
        for fn in ("custom_lusture", "custom_glue", "custom_pulp"):
            self.assertIn(
                f"'{fn}'",
                self.src,
                f"MI1-I72 P1 regression — {fn} must remain in hide_in_hty.",
            )


class TestApplyFunctionRunsOnRefresh(FrappeTestCase):
    """The MI1-I39 apply function is wired via `refresh:` on the form.
    Pin that so a future edit that unwires it doesn't silently break
    both P1 and P7 hides."""

    def test_wired_on_refresh(self):
        src = _script()
        # There are two frappe.ui.form.on blocks — the HTY-mode one and
        # the naming-series one. Pin the HTY-mode wire specifically.
        self.assertIn(
            "refresh: mi1_i39_apply_dn_hty",
            src,
            "mi1_i39_apply_dn_hty must be wired on refresh — otherwise "
            "the hide toggles never re-apply on doc load.",
        )

    def test_wired_on_transaction_type_change(self):
        src = _script()
        self.assertIn(
            "transaction_type: mi1_i39_apply_dn_hty",
            src,
            "mi1_i39_apply_dn_hty must fire when transaction_type "
            "changes — otherwise flipping the mode leaves stale "
            "hidden/shown state.",
        )
