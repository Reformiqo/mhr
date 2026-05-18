"""MI1-I39 P2-F — HTY naming series presence tests.

Property Setter must extend each target DocType's naming_series options
with the HTY-prefixed value, and Client Scripts must wire the auto-switch
behavior so transaction_type=HTY → HTY naming series.
"""

import frappe
from frappe.tests.utils import FrappeTestCase


HTY_SERIES_BY_DOCTYPE = {
    "Sales Order":    ["HTY-SO-.YYYY.-"],
    "Delivery Note":  ["HTY-DN-.YYYY.-", "HTY-DN-RET-.YYYY.-"],
    "Stock Entry":    ["HTY-STE-.YYYY.-"],
    "Delivery Trip":  ["HTY-DT-.YYYY.-"],
}


class TestHTYNamingSeriesPropertySetters(FrappeTestCase):
    """The Property Setter for naming_series must list every HTY prefix
    and be tagged module=Mhr so it ships via fixtures."""

    def test_each_doctype_has_hty_series_in_options(self):
        for dt, hty_options in HTY_SERIES_BY_DOCTYPE.items():
            with self.subTest(doctype=dt):
                ps = frappe.db.get_value(
                    "Property Setter",
                    {"doc_type": dt, "field_name": "naming_series", "property": "options"},
                    ["value", "module"],
                    as_dict=True,
                )
                self.assertIsNotNone(
                    ps,
                    f"No naming_series Property Setter for {dt} — "
                    "HTY series options will not be available.",
                )
                self.assertEqual(
                    ps.module, "Mhr",
                    f"{dt} naming_series Property Setter must be in module=Mhr "
                    "so it exports via fixtures.",
                )
                opts = (ps.value or "").splitlines()
                for hty_opt in hty_options:
                    self.assertIn(
                        hty_opt, opts,
                        f"{dt} naming_series options must include {hty_opt!r} — "
                        "Client Script auto-switch reads from this list.",
                    )


class TestHTYNamingSeriesClientScripts(FrappeTestCase):
    """Each HTY-mode Client Script must wire the naming_series auto-switch."""

    EXPECTED_SCRIPTS = [
        "MI1-I39 — Sales Order HTY Mode",
        "MI1-I39 — Delivery Note HTY Mode",
        "MI1-I39 — Stock Entry HTY Mode",
        "MI1-I39 — Delivery Trip HTY Mode",
    ]

    def test_each_script_present_and_enabled(self):
        for name in self.EXPECTED_SCRIPTS:
            with self.subTest(name=name):
                self.assertTrue(
                    frappe.db.exists("Client Script", name),
                    f"Client Script {name!r} is missing — naming_series "
                    "auto-switch won't fire in that DocType.",
                )
                enabled = frappe.db.get_value("Client Script", name, "enabled")
                self.assertEqual(enabled, 1, f"{name} must be enabled.")

    def test_each_script_has_naming_series_handler(self):
        for name in self.EXPECTED_SCRIPTS:
            with self.subTest(name=name):
                script = frappe.db.get_value("Client Script", name, "script")
                self.assertIn(
                    "naming_series", script,
                    f"{name} must reference naming_series — that's the field it toggles.",
                )
                # Helper function name pattern.
                self.assertIn(
                    "mi1_i39_apply_", script,
                    f"{name} must contain the mi1_i39_apply_<slug>_naming_series helper.",
                )

    def test_helper_guards_docstatus(self):
        # All toggles must skip submitted docs (you can't change
        # naming_series after submit — Frappe will error).
        for name in self.EXPECTED_SCRIPTS:
            with self.subTest(name=name):
                script = frappe.db.get_value("Client Script", name, "script")
                self.assertIn(
                    "frm.doc.docstatus !== 0", script,
                    f"{name} must guard the toggle behind docstatus !== 0.",
                )
