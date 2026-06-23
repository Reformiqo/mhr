"""MI1-I62 (Container ID fetch, 2026-06-23) — pin the "Fetch by
Container ID" flow on Print Batch.

Background: Container's autoname is `format:{container_no}-{#}`, so
one Container No can map to many Container documents. The user picks
Container + Lot + Item, the form shows a popup of the matching Container
IDs, the user picks one, and every Batch belonging to that Container
doc is fetched into List Batches.

Behavioural integration with a fully-seeded Container + Batch Items
fixture is out of scope here — these tests pin the contract:
  - both server methods exist + whitelisted
  - their signatures match what the JS calls
  - JS adds the custom button under the right gate
  - the picker calls the listing endpoint, the picker's selection
    calls the fetch endpoint, results go through prepend_added_rows
    so newly-added rows still land on top.
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


class TestServerMethods(FrappeTestCase):

    def test_get_container_ids_for_exists_and_whitelisted(self):
        from mhr import utilis
        self.assertTrue(callable(getattr(utilis, "get_container_ids_for", None)))
        src = _utilis_src()
        self.assertRegex(
            src,
            r"@frappe\.whitelist\(\)\s*\ndef\s+get_container_ids_for\b",
            "get_container_ids_for must be @frappe.whitelist()-ed.",
        )

    def test_get_container_ids_for_signature(self):
        from mhr.utilis import get_container_ids_for
        params = list(inspect.signature(get_container_ids_for).parameters.keys())
        self.assertEqual(params, ["container_no", "lot_no", "item"],
            "Signature must be (container_no, lot_no, item).")
        # item is optional (default None).
        sig = inspect.signature(get_container_ids_for)
        self.assertIsNone(sig.parameters["item"].default)

    def test_get_container_ids_for_empty_inputs(self):
        from mhr.utilis import get_container_ids_for
        self.assertEqual(get_container_ids_for(None, None), [])
        self.assertEqual(get_container_ids_for("", ""), [])
        self.assertEqual(get_container_ids_for("anything", ""), [])

    def test_get_container_ids_for_returns_list_of_strings(self):
        from mhr.utilis import get_container_ids_for
        # A nonsense filter returns empty; type must still be list-of-str
        out = get_container_ids_for("__no_such_container__", "__nope__")
        self.assertIsInstance(out, list)

    def test_get_batches_for_container_id_exists_and_whitelisted(self):
        from mhr import utilis
        self.assertTrue(callable(getattr(utilis, "get_batches_for_container_id", None)))
        src = _utilis_src()
        self.assertRegex(
            src,
            r"@frappe\.whitelist\(\)\s*\ndef\s+get_batches_for_container_id\b",
            "get_batches_for_container_id must be @frappe.whitelist()-ed.",
        )

    def test_get_batches_for_container_id_signature(self):
        from mhr.utilis import get_batches_for_container_id
        params = list(inspect.signature(get_batches_for_container_id).parameters.keys())
        self.assertEqual(params, ["container_id"],
            "Signature must be (container_id).")

    def test_get_batches_for_container_id_empty_input(self):
        from mhr.utilis import get_batches_for_container_id
        self.assertEqual(get_batches_for_container_id(None), [])
        self.assertEqual(get_batches_for_container_id(""), [])

    def test_get_batches_for_container_id_payload_keys(self):
        """Source-level pin: payload shape matches what the JS reads
        from list_batches rows (batch / item / cone / lot_no / batch_qty)."""
        from mhr import utilis
        src = inspect.getsource(utilis.get_batches_for_container_id)
        for key in ("batch", "item", "cone", "lot_no", "batch_qty"):
            self.assertIn(f"AS {key}" if " " not in key else f"AS `{key}`",
                          src.replace('"', "").replace("'", ""),
                          f"Result column {key!r} must be aliased in the SQL.")

    def test_get_batches_for_container_id_uses_batch_items_bridge(self):
        """Pin: must JOIN tabBatch Items so we correctly resolve which
        Batches belong to THIS Container doc (vs. all docs sharing the
        same container_no value)."""
        from mhr import utilis
        src = inspect.getsource(utilis.get_batches_for_container_id)
        self.assertIn("tabBatch Items", src,
            "Must use the Batch Items child table to resolve the link.")
        self.assertIn("bi.parent = %s", src,
            "Filter must be on Batch Items.parent = the Container.name.")


class TestJsButtonAndPicker(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.js = _load_js()

    def test_button_added_in_refresh(self):
        m = re.search(
            r"refresh:\s*function\s*\(\s*frm\s*\)\s*\{(.*?)\n\s*\},",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, "refresh handler must exist.")
        body = m.group(1)
        self.assertIn('"Fetch by Container ID"', body,
            "refresh must add the 'Fetch by Container ID' custom button.")

    def test_button_gated_on_container_lot_item(self):
        m = re.search(
            r"refresh:\s*function\s*\(\s*frm\s*\)\s*\{(.*?)\n\s*\},",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertRegex(
            body,
            r"frm\.doc\.container_no\s*&&\s*frm\.doc\.lot_no\s*&&\s*frm\.doc\.item",
            "Button must only render when Container + Lot + Item are all set.",
        )

    def test_picker_calls_listing_endpoint(self):
        m = re.search(
            r"function\s+open_container_id_picker\s*\(\s*frm\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m, "open_container_id_picker must exist.")
        body = m.group(1)
        self.assertIn('"mhr.utilis.get_container_ids_for"', body,
            "Picker must call get_container_ids_for to list Container IDs.")
        for arg in ("container_no", "lot_no", "item"):
            self.assertIn(f"{arg}:", body,
                f"Picker call must pass {arg} in args.")

    def test_picker_short_circuits_single_match(self):
        m = re.search(
            r"function\s+open_container_id_picker\s*\(\s*frm\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertRegex(
            body,
            r"ids\.length\s*===?\s*1",
            "When there's only one Container ID, picker must skip the "
            "dialog and fetch directly.",
        )

    def test_picker_uses_dialog(self):
        m = re.search(
            r"function\s+open_container_id_picker\s*\(\s*frm\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertIn("frappe.ui.Dialog", body,
            "Picker must use frappe.ui.Dialog for the popup.")
        self.assertRegex(body, r'fieldtype:\s*"Select"',
            "Dialog must offer the IDs as a Select field.")

    def test_fetch_helper_calls_batch_endpoint(self):
        m = re.search(
            r"function\s+fetch_and_append_batches_for_container_id\s*\("
            r"\s*frm\s*,\s*container_id\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        self.assertIsNotNone(m,
            "fetch_and_append_batches_for_container_id must exist.")
        body = m.group(1)
        self.assertIn('"mhr.utilis.get_batches_for_container_id"', body,
            "Fetch must call get_batches_for_container_id.")
        self.assertIn("container_id: container_id", body,
            "Fetch must pass the picked container_id in args.")

    def test_fetch_helper_uses_prepend(self):
        m = re.search(
            r"function\s+fetch_and_append_batches_for_container_id\s*\("
            r"\s*frm\s*,\s*container_id\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertIn("prepend_added_rows(frm, added)", body,
            "Container-ID fetch must also call prepend_added_rows so "
            "newly-added batches land on top of List Batches "
            "(consistent with the Supplier-Batch + Cone paths).")

    def test_fetch_helper_dedups_existing_rows(self):
        m = re.search(
            r"function\s+fetch_and_append_batches_for_container_id\s*\("
            r"\s*frm\s*,\s*container_id\s*\)\s*\{(.*?)\n\}\n",
            self.js, re.DOTALL,
        )
        body = m.group(1)
        self.assertIn("existing.has(data.batch)", body,
            "Fetch must dedup against existing list_batches rows.")
