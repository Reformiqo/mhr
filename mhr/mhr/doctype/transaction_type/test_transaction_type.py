# Copyright (c) 2026, reformiqo and contributors

import frappe
from frappe.tests.utils import FrappeTestCase


class TestTransactionType(FrappeTestCase):

    def test_doctype_exists(self):
        self.assertTrue(frappe.db.exists("DocType", "Transaction Type"))

    def test_seed_values_present(self):
        for name in ("VFY", "HTY"):
            self.assertTrue(
                frappe.db.exists("Transaction Type", name),
                f"Seed value {name!r} must exist after migrate.",
            )

    def test_autoname_is_field(self):
        meta = frappe.get_meta("Transaction Type")
        self.assertEqual(meta.autoname, "field:transaction_type_name",
            "autoname must be field:transaction_type_name so the doc's "
            "name equals the user-facing code — matches how the legacy "
            "Select stored its value.")
