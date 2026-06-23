"""MI1-I62 (VFY Cone fetch, 2026-06-23) — pin the VFY-only Cone fetch path.

The form shows a Cone field when transaction_type=VFY; typing a cone there
fetches every Batch matching (container, lot, cone) and appends them to
List Batches. Pins:
  - Custom Field 'Print Batch-cone' exists with the right depends_on
  - get_print_batch accepts a cone kwarg and filters by custom_cone
  - JS has a cone change handler that calls fetch_and_append_batch_by_cone
  - The new fetch helper guards on container + lot + cone
  - It passes cone through to the server in the call args
"""

import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_print_batch_js():
    path = os.path.join(
        frappe.get_app_path("mhr"),
        "mhr", "doctype", "print_batch", "print_batch.js",
    )
    return open(path).read()


class TestCustomFieldShape(FrappeTestCase):

    def test_cone_field_exists(self):
        self.assertTrue(
            frappe.db.exists("Custom Field", "Print Batch-cone"),
            "Custom Field 'Print Batch-cone' must exist after migrate.",
        )

    def test_cone_field_depends_on_transaction_type_vfy(self):
        depends_on = frappe.db.get_value(
            "Custom Field", "Print Batch-cone", "depends_on"
        )
        self.assertIn("VFY", depends_on or "",
            "Cone field must only appear when transaction_type === 'VFY'.")
        self.assertIn("container_no", depends_on or "",
            "Cone field must require container_no to be set.")
        self.assertIn("lot_no", depends_on or "",
            "Cone field must require lot_no to be set.")

    def test_cone_field_shape(self):
        cf = frappe.get_doc("Custom Field", "Print Batch-cone")
        self.assertEqual(cf.fieldtype, "Data")
        self.assertEqual(cf.fieldname, "cone")
        self.assertEqual(cf.dt, "Print Batch")


class TestGetPrintBatchConeFilter(FrappeTestCase):

    def test_cone_param_present_and_optional(self):
        import inspect
        from mhr.utilis import get_print_batch
        sig = inspect.signature(get_print_batch)
        self.assertIn("cone", sig.parameters)
        self.assertIsNone(sig.parameters["cone"].default)

    def test_cone_param_uses_custom_cone_filter(self):
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis.get_print_batch)
        # Must add custom_cone to the filters when cone is passed.
        self.assertIn('filters["custom_cone"] = cone', src,
            "Cone arg must map to filters['custom_cone'] = cone.")


class TestJsConeHandler(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_print_batch_js()

    def test_cone_change_handler_exists(self):
        self.assertRegex(self.js,
            r"cone:\s*function\s*\(\s*frm\s*\)\s*\{",
            "Print Batch form must register a cone change handler.")

    def test_cone_handler_gates_on_vfy(self):
        m = re.search(
            r"cone:\s*function\s*\(\s*frm\s*\)\s*\{(.*?)\n\s*\},",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn('frm.doc.transaction_type !== "VFY"', body,
            "Cone handler must early-return when transaction_type isn't VFY.")
        self.assertIn("fetch_and_append_batch_by_cone(frm)", body,
            "Cone handler must call fetch_and_append_batch_by_cone.")

    def test_cone_fetch_helper_exists(self):
        self.assertRegex(self.js,
            r"function\s+fetch_and_append_batch_by_cone\s*\(\s*frm\s*\)\s*\{",
            "fetch_and_append_batch_by_cone must exist.")

    def test_cone_fetch_helper_guards(self):
        m = re.search(
            r"function\s+fetch_and_append_batch_by_cone\s*\(\s*frm\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, "Could not locate fetch_and_append_batch_by_cone.")
        body = m.group(1)
        self.assertRegex(body,
            r"!\s*frm\.doc\.container_no\s*\|\|\s*!\s*frm\.doc\.lot_no",
            "Cone fetch must still require container + lot.")
        self.assertRegex(body, r"!\s*frm\.doc\.cone",
            "Cone fetch must early-return when cone is empty.")

    def test_cone_fetch_helper_passes_cone_to_server(self):
        m = re.search(
            r"function\s+fetch_and_append_batch_by_cone\s*\(\s*frm\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertRegex(body, r"cone:\s*frm\.doc\.cone",
            "Cone fetch must pass frm.doc.cone in the server call args.")
        self.assertIn('"mhr.utilis.get_print_batch"', body,
            "Cone fetch must call mhr.utilis.get_print_batch.")
