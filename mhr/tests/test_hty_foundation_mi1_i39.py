"""MI1-I39 Phase 1 — HTY foundation guard tests.

Pins the Custom Fields + Client Scripts that the FRD's Phase 1
("Transaction Type" + HTY label-swap + Gross Wt + Sr.No.) requires.
A regression here would silently break the VFY/HTY toggle for every
DocType the FRD touches.

Scope: structural only — does the field exist on the right DocType with
the right type/options/default? Does each HTY Client Script point at its
DocType and is it enabled? Behavior (label swap, hide/show on
`transaction_type` change) is JS-side and verified manually / by chrome
walkthrough.

Phase 2 (lot-based DN flow, SO company-aware queries, HTY reports) will
add its own tests.
"""

import frappe
from frappe.tests.utils import FrappeTestCase


TRANSACTION_TYPE_DOCTYPES = [
    "Container",
    "Sales Order",
    "Delivery Note",
    "Stock Entry",
    "Print Batch",
    "Delivery Trip",
]

# (doctype, fieldname, expected_label, expected_fieldtype)
NEW_HTY_FIELDS = [
    ("Batch Items",        "custom_gross_weight", "Gross Weight", "Float"),
    ("Batch Items",        "custom_sr_no",        "Sr. No.",      "Data"),
    ("Delivery Note Item", "custom_gross_weight", "Gross Weight", "Float"),
    ("Delivery Note Item", "custom_sr_no",        "Sr. No.",      "Data"),
]

HTY_CLIENT_SCRIPTS = [
    ("MI1-I39 — Container HTY Mode",     "Container"),
    ("MI1-I39 — Sales Order HTY Mode",   "Sales Order"),
    ("MI1-I39 — Delivery Note HTY Mode", "Delivery Note"),
    ("MI1-I39 — Print Batch HTY Mode",   "Print Batch"),
]


class TestHTYTransactionTypeField(FrappeTestCase):
    """Every DocType in the FRD must carry a `transaction_type` field
    of type Select(VFY,HTY) defaulting to VFY."""

    def test_field_exists_on_every_doctype(self):
        for dt in TRANSACTION_TYPE_DOCTYPES:
            with self.subTest(doctype=dt):
                cf = frappe.db.get_value(
                    "Custom Field",
                    {"dt": dt, "fieldname": "transaction_type"},
                    ["fieldtype", "options", "default", "reqd", "module"],
                    as_dict=True,
                )
                self.assertIsNotNone(
                    cf,
                    f"MI1-I39: {dt}.transaction_type Custom Field is missing — "
                    f"the HTY toggle won't appear on the form.",
                )
                self.assertEqual(cf.fieldtype, "Select", f"{dt}.transaction_type must be Select")
                self.assertEqual(
                    set(cf.options.splitlines()),
                    {"VFY", "HTY"},
                    f"{dt}.transaction_type options must be exactly VFY+HTY",
                )
                self.assertEqual(cf.default, "VFY",
                    f"{dt}.transaction_type must default to VFY — the FRD's hard rule "
                    f"is that VFY mode = unchanged starter behavior.")
                self.assertEqual(cf.reqd, 1, f"{dt}.transaction_type must be mandatory")
                self.assertEqual(cf.module, "Mhr",
                    f"{dt}.transaction_type must be in module=Mhr so it ships via fixtures.")


class TestHTYNewFields(FrappeTestCase):
    """`Gross Weight` and `Sr. No.` exist on Batch Items + Delivery Note Item."""

    def test_new_fields_present(self):
        for dt, fname, label, ftype in NEW_HTY_FIELDS:
            with self.subTest(doctype=dt, fieldname=fname):
                cf = frappe.db.get_value(
                    "Custom Field",
                    {"dt": dt, "fieldname": fname},
                    ["label", "fieldtype", "module"],
                    as_dict=True,
                )
                self.assertIsNotNone(cf, f"MI1-I39: {dt}.{fname} missing")
                self.assertEqual(cf.label, label)
                self.assertEqual(cf.fieldtype, ftype)
                self.assertEqual(cf.module, "Mhr")


class TestHTYClientScripts(FrappeTestCase):
    """Each HTY-toggling Client Script is present, enabled, and targets
    the right DocType."""

    def test_scripts_present_and_enabled(self):
        for name, dt in HTY_CLIENT_SCRIPTS:
            with self.subTest(name=name):
                cs = frappe.db.get_value(
                    "Client Script",
                    name,
                    ["dt", "enabled", "view", "module"],
                    as_dict=True,
                )
                self.assertIsNotNone(cs, f"MI1-I39: Client Script {name!r} missing")
                self.assertEqual(cs.dt, dt, f"{name} must target DocType {dt!r}")
                self.assertEqual(cs.enabled, 1, f"{name} must be enabled")
                self.assertEqual(cs.view, "Form", f"{name} must be a Form-view script")
                self.assertEqual(cs.module, "Mhr",
                    f"{name} must be in module=Mhr so it ships via fixtures.")

    def test_scripts_reference_transaction_type_handler(self):
        """Each script must bind a handler to `transaction_type` so the
        HTY toggle re-fires when the user flips the field."""
        for name, dt in HTY_CLIENT_SCRIPTS:
            with self.subTest(name=name):
                script = frappe.db.get_value("Client Script", name, "script")
                self.assertIsNotNone(script)
                self.assertIn(
                    "transaction_type", script,
                    f"{name} must wire a handler on the `transaction_type` field.",
                )
                self.assertIn(
                    f"frappe.ui.form.on('{dt}'", script,
                    f"{name} must bind on {dt!r}.",
                )
