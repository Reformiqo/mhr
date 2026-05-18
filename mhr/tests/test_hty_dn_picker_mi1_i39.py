"""MI1-I39 Phase 2A — HTY DN 4-step lot-based picker tests.

The FRD's flagship HTY workflow: from a Delivery Note, the user picks a
Lot No, the system auto-loads matching Containers, the user multi-selects,
and Proceed populates DN items from the selected Containers' Batch Items.

Tests pin:
  - 3 whitelisted server endpoints exist + return correct shape
  - get_hty_batches_for_containers handles both list and JSON-string input
  - Empty / missing input degrades gracefully
  - DN Client Script wires the "Pick Containers by Lot" custom button
    and binds the picker on transaction_type=HTY only
"""

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis as mhr_utilis


class TestHTYPickerEndpoints(FrappeTestCase):
    """The 3 server endpoints behind the DN lot picker."""

    def test_endpoints_are_whitelisted(self):
        # The picker dialog reaches these over /api/method; without
        # @frappe.whitelist() Frappe 403s the call.
        src = inspect.getsource(mhr_utilis)
        for fn_name in ("get_hty_lots", "get_hty_containers_for_lot", "get_hty_batches_for_containers"):
            with self.subTest(endpoint=fn_name):
                fn = getattr(mhr_utilis, fn_name, None)
                self.assertIsNotNone(fn, f"Missing endpoint: {fn_name}")
                # Look for the decorator immediately above the function in source.
                idx = src.find(f"def {fn_name}(")
                self.assertGreater(idx, 0, f"Definition for {fn_name} not found.")
                preamble = src[max(0, idx - 200):idx]
                self.assertIn(
                    "@frappe.whitelist()",
                    preamble,
                    f"{fn_name} must be @frappe.whitelist()'d — it's called from the "
                    "picker dialog over /api/method.",
                )

    def test_get_hty_lots_returns_list(self):
        out = mhr_utilis.get_hty_lots()
        self.assertIsInstance(
            out, list,
            "get_hty_lots must return a list (possibly empty).",
        )
        # Either empty or each row has lot_no + container_count keys.
        for row in out[:5]:
            self.assertIn("lot_no", row)
            self.assertIn("container_count", row)

    def test_get_hty_containers_for_lot_no_lot(self):
        # Falsy lot_no → empty list, no DB hit error.
        self.assertEqual(mhr_utilis.get_hty_containers_for_lot(None), [])
        self.assertEqual(mhr_utilis.get_hty_containers_for_lot(""), [])

    def test_get_hty_containers_for_lot_returns_list(self):
        out = mhr_utilis.get_hty_containers_for_lot("__no_such_lot_should_not_exist__")
        self.assertIsInstance(out, list)
        self.assertEqual(out, [])

    def test_get_hty_batches_for_containers_empty_input(self):
        self.assertEqual(mhr_utilis.get_hty_batches_for_containers([]), [])
        self.assertEqual(mhr_utilis.get_hty_batches_for_containers("[]"), [])

    def test_get_hty_batches_for_containers_accepts_json_string(self):
        # The /api/method serializer sends Array args as JSON strings — the
        # endpoint must parse that path. We assert it returns a list (empty
        # because the fake names won't exist).
        out = mhr_utilis.get_hty_batches_for_containers('["__nope__"]')
        self.assertIsInstance(out, list)

    def test_get_hty_batches_for_containers_payload_shape(self):
        """Source-level check: each returned row carries the keys the
        client-side picker pushes into the DN items child table."""
        src = inspect.getsource(mhr_utilis.get_hty_batches_for_containers)
        required_keys = [
            "item_code", "qty", "batch_no", "warehouse",
            "custom_container_no", "custom_lot_no",
            "custom_cone", "custom_sr_no",
            "custom_gross_weight", "custom_supplier_batch_no",
        ]
        for key in required_keys:
            with self.subTest(key=key):
                self.assertIn(
                    f'"{key}":', src,
                    f"payload must include {key!r} — the DN picker dialog reads this key when "
                    "appending child rows."
                )


class TestHTYPickerClientScript(FrappeTestCase):
    """The DN Client Script must wire the custom button + dialog."""

    def setUp(self):
        self.script = frappe.db.get_value(
            "Client Script",
            "MI1-I39 — Delivery Note HTY Mode",
            "script",
        )
        self.assertIsNotNone(self.script, "DN HTY Client Script is missing.")

    def test_pick_button_only_in_hty_mode(self):
        # The add_custom_button call must be guarded by the HTY check.
        self.assertIn(
            "Pick Containers by Lot",
            self.script,
            "DN HTY Client Script must add a 'Pick Containers by Lot' custom button.",
        )
        self.assertIn(
            "frm.doc.transaction_type === 'HTY'",
            self.script,
            "Button must only appear when transaction_type=HTY.",
        )

    def test_dialog_has_4_steps(self):
        # Sanity check that the FRD's step labels are referenced in the dialog.
        self.assertIn("Step 1", self.script)
        self.assertIn("Step 2", self.script)
        self.assertIn("Proceed", self.script,
            "Step 4 (Proceed) is the action that populates DN items.")

    def test_dialog_calls_the_three_endpoints(self):
        for endpoint in (
            "mhr.utilis.get_hty_lots",
            "mhr.utilis.get_hty_containers_for_lot",
            "mhr.utilis.get_hty_batches_for_containers",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(
                    endpoint, self.script,
                    f"DN HTY Client Script must call {endpoint} (the picker depends on it).",
                )

    def test_proceed_populates_items_table(self):
        # The proceed path must clear the items table and add rows for each
        # batch — pin the relevant API calls.
        self.assertIn(
            "frm.clear_table('items')",
            self.script,
            "Proceed must clear existing rows before appending fresh ones.",
        )
        self.assertIn(
            "frm.add_child('items'",
            self.script,
            "Proceed must add new child rows to the items table.",
        )
        self.assertIn(
            "frm.refresh_field('items')",
            self.script,
            "Proceed must refresh the items grid after appending.",
        )
