"""MI1-I62 (Container ID inline Select, HTY-only, 2026-06-23) — pins.

Raj's UX: instead of a 'Fetch by Container ID' button + popup, surface
a Container ID Select field inline (mirroring Item). Only relevant in
HTY mode — VFY uses the Cone fetch path.

Pins:
  - Custom Field 'Print Batch-container_id' exists, Select fieldtype,
    HTY-gated depends_on.
  - Server methods (get_container_ids_for / get_batches_for_container_id)
    still exist with the same signatures.
  - JS:
      * populate_container_ids helper exists, mirrors mi1_i27_populate_items.
      * refresh, lot_no, item, transaction_type handlers all repopulate
        the options (HTY-gated).
      * container_id change handler fetches via the existing
        fetch_and_append_batches_for_container_id helper.
      * The old 'Fetch by Container ID' button + open_container_id_picker
        popup are GONE.
"""

import os
import re
import inspect

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_js():
    path = os.path.join(
        frappe.get_app_path("mhr"),
        "mhr", "doctype", "print_batch", "print_batch.js",
    )
    return open(path).read()


def _utilis_src():
    from mhr import utilis
    return open(inspect.getsourcefile(utilis)).read()


class TestCustomFieldShape(FrappeTestCase):

    def test_field_exists(self):
        self.assertTrue(
            frappe.db.exists("Custom Field", "Print Batch-container_id"),
            "Custom Field 'Print Batch-container_id' must exist after migrate.",
        )

    def test_field_is_select(self):
        cf = frappe.get_doc("Custom Field", "Print Batch-container_id")
        self.assertEqual(cf.fieldtype, "Select")
        self.assertEqual(cf.dt, "Print Batch")
        self.assertEqual(cf.fieldname, "container_id")

    def test_field_depends_on_hty(self):
        depends_on = frappe.db.get_value(
            "Custom Field", "Print Batch-container_id", "depends_on"
        ) or ""
        self.assertIn("HTY", depends_on,
            "Container ID Select must be hidden except in HTY mode.")
        self.assertIn("container_no", depends_on)
        self.assertIn("lot_no", depends_on)


class TestServerMethodsStillExist(FrappeTestCase):
    """The popup is gone but the server methods stay — the inline Select
    populates via get_container_ids_for, and on selection we call
    get_batches_for_container_id."""

    def test_get_container_ids_for_signature(self):
        from mhr.utilis import get_container_ids_for
        params = list(inspect.signature(get_container_ids_for).parameters.keys())
        self.assertEqual(params, ["container_no", "lot_no", "item"])

    def test_get_batches_for_container_id_signature(self):
        from mhr.utilis import get_batches_for_container_id
        params = list(inspect.signature(get_batches_for_container_id).parameters.keys())
        self.assertEqual(params, ["container_id"])

    def test_both_still_whitelisted(self):
        src = _utilis_src()
        for fn in ("get_container_ids_for", "get_batches_for_container_id"):
            self.assertRegex(src,
                rf"@frappe\.whitelist\(\)\s*\ndef\s+{fn}\b",
                f"{fn} must remain @frappe.whitelist()-ed.")


class TestPopulateHelper(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_js()

    def test_populate_helper_exists(self):
        self.assertRegex(
            self.js,
            r"function\s+populate_container_ids\s*\(\s*frm\s*,\s*preserve_value\s*\)\s*\{",
            "populate_container_ids helper must exist.",
        )

    def test_helper_calls_listing_endpoint(self):
        m = re.search(
            r"function\s+populate_container_ids\s*\([^)]*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn('"mhr.utilis.get_container_ids_for"', body)
        for arg in ("container_no", "lot_no", "item"):
            self.assertIn(f"{arg}:", body)

    def test_helper_updates_select_options(self):
        m = re.search(
            r"function\s+populate_container_ids\s*\([^)]*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertIn('"container_id"', body)
        self.assertIn('"options"', body)
        self.assertIn("refresh_field", body)

    def test_helper_supports_preserve_value(self):
        m = re.search(
            r"function\s+populate_container_ids\s*\([^)]*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertIn("preserve_value", body,
            "Helper must honour preserve_value (re-select prior value if "
            "still in the new options).")


class TestHandlersGatedOnHty(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_js()

    def _handler_body(self, name):
        m = re.search(
            rf'{name}:\s*function\s*\(\s*frm\s*\)\s*\{{(.*?)\n\s*\}},',
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, f"Could not extract handler {name!r}.")
        return m.group(1)

    def test_refresh_repopulates_on_hty(self):
        body = self._handler_body("refresh")
        self.assertIn("populate_container_ids(frm", body)
        self.assertIn('transaction_type === "HTY"', body)

    def test_lot_no_clears_and_repopulates(self):
        body = self._handler_body("lot_no")
        self.assertIn("set_value('container_id', '')", body,
            "lot_no change must clear the container_id selection.")
        self.assertIn("populate_container_ids(frm", body)

    def test_item_handler_hty_gated(self):
        body = self._handler_body("item")
        self.assertIn('transaction_type !== "HTY"', body,
            "item handler must early-return when not in HTY mode.")
        self.assertIn("populate_container_ids(frm", body)

    def test_transaction_type_repopulates_or_clears(self):
        body = self._handler_body("transaction_type")
        self.assertIn('transaction_type === "HTY"', body)
        self.assertIn("populate_container_ids(frm", body)
        # When switching back from HTY -> VFY, options must clear.
        self.assertIn("set_value('container_id', '')", body)

    def test_container_id_change_triggers_fetch(self):
        body = self._handler_body("container_id")
        self.assertIn('transaction_type !== "HTY"', body,
            "container_id handler must early-return when not HTY.")
        self.assertIn("fetch_and_append_batches_for_container_id(frm, frm.doc.container_id)",
            body,
            "container_id change must call the fetch helper.")
        self.assertIn("set_value('container_id', '')", body,
            "After fetching, clear the selection so the user can pick "
            "another Container ID without manually emptying the field.")


class TestFetchHelperStillWorks(FrappeTestCase):
    """fetch_and_append_batches_for_container_id was added with the
    button flow but stays in place for the inline Select."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_js()

    def test_helper_exists(self):
        self.assertRegex(
            self.js,
            r"function\s+fetch_and_append_batches_for_container_id\s*\("
            r"\s*frm\s*,\s*container_id\s*\)\s*\{",
        )

    def test_helper_prepends_and_dedups(self):
        m = re.search(
            r"function\s+fetch_and_append_batches_for_container_id\s*\("
            r"[^)]*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertIn("prepend_added_rows(frm, added)", body,
            "Container ID fetch must use prepend_added_rows (newest on top).")
        self.assertIn("existing.has(data.batch)", body,
            "Container ID fetch must dedup against existing rows.")


class TestOldButtonAndPickerRemoved(FrappeTestCase):
    """The button + popup approach has been replaced by the inline Select."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_js()

    def test_no_fetch_by_container_id_button(self):
        self.assertNotIn(
            'add_custom_button(__("Fetch by Container ID")',
            self.js,
            "The 'Fetch by Container ID' button must be removed — "
            "the inline Select replaces it.",
        )

    def test_no_open_container_id_picker_helper(self):
        self.assertNotRegex(
            self.js,
            r"function\s+open_container_id_picker\s*\(",
            "open_container_id_picker (the popup) must be removed.",
        )

    def test_no_frappe_ui_dialog_for_container_id(self):
        # Stronger pin: no frappe.ui.Dialog call near 'Container ID'.
        # (Generic frappe.ui.Dialog usage elsewhere in this file is fine.)
        for m in re.finditer(r"frappe\.ui\.Dialog", self.js):
            window = self.js[max(0, m.start() - 200):m.end() + 200]
            self.assertNotIn(
                "Pick a Container ID", window,
                "The old 'Pick a Container ID' dialog must be removed.",
            )
