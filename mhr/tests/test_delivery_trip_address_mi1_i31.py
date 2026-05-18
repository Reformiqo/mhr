"""MI1-I31 — Delivery Trip auto-fetches customer's default address.

Raj reported the address link on Delivery Stop wasn't getting fetched
when a customer was set, so subsequent transactions also didn't
auto-fill. Fixed via:
  - Client Script "MI1-I31 — Delivery Stop Address Auto-Fetch" — wires
    Delivery Stop.customer → frappe.contacts.address.get_default_address
  - Server validate hook fill_default_addresses_on_delivery_trip —
    defensive backstop for API/import flows.
"""
import inspect
from unittest.mock import MagicMock, patch
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis as mhr_utilis


class TestDeliveryStopAddressClientScript(FrappeTestCase):
    """The Client Script must wire Delivery Stop.customer to the
    default-address helper, with a guard so existing addresses are not
    overwritten."""

    def setUp(self):
        self.script = frappe.db.get_value(
            "Client Script", "MI1-I31 — Delivery Stop Address Auto-Fetch", "script"
        )
        self.assertIsNotNone(
            self.script,
            "Client Script 'MI1-I31 — Delivery Stop Address Auto-Fetch' is missing.",
        )

    def test_targets_delivery_stop_child(self):
        self.assertIn(
            "frappe.ui.form.on('Delivery Stop'", self.script,
            "Script must bind on the Delivery Stop child doctype.",
        )

    def test_calls_get_default_address(self):
        self.assertIn(
            "frappe.contacts.doctype.address.address.get_default_address",
            self.script,
            "Script must call Frappe's standard default-address helper.",
        )

    def test_does_not_overwrite_existing_address(self):
        self.assertIn(
            "if (row.address) return;", self.script,
            "Script must skip the fetch when the user has already chosen "
            "an Address — never clobber a user-set value.",
        )

    def test_hydrates_display_text(self):
        self.assertIn(
            "get_address_display", self.script,
            "Script must also hydrate the customer_address Small Text "
            "via get_address_display — the form shows that to the user.",
        )

    def test_enabled_and_in_mhr_module(self):
        cs = frappe.db.get_value(
            "Client Script",
            "MI1-I31 — Delivery Stop Address Auto-Fetch",
            ["dt", "enabled", "view", "module"],
            as_dict=True,
        )
        self.assertEqual(cs.dt, "Delivery Trip")
        self.assertEqual(cs.enabled, 1)
        self.assertEqual(cs.view, "Form")
        self.assertEqual(cs.module, "Mhr")


class TestFillDefaultAddressesServerHook(FrappeTestCase):
    """`mhr.utilis.fill_default_addresses_on_delivery_trip` is the
    defensive server-side fallback for the Client Script."""

    def _make_doc(self, stops):
        doc = MagicMock()
        doc.delivery_stops = []
        for s in stops:
            stop = MagicMock()
            stop.customer = s.get("customer")
            stop.address = s.get("address")
            stop.customer_address = s.get("customer_address")
            doc.delivery_stops.append(stop)
        return doc

    def test_no_stops_is_noop(self):
        doc = self._make_doc([])
        # Must not call get_default_address.
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   side_effect=AssertionError("must not call")):
            mhr_utilis.fill_default_addresses_on_delivery_trip(doc)

    def test_stop_without_customer_skipped(self):
        doc = self._make_doc([{"customer": None}])
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   side_effect=AssertionError("must not call for None customer")):
            mhr_utilis.fill_default_addresses_on_delivery_trip(doc)

    def test_stop_with_existing_address_skipped(self):
        doc = self._make_doc([{"customer": "ABC", "address": "ABC-Address"}])
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   side_effect=AssertionError("must not call when address set")):
            mhr_utilis.fill_default_addresses_on_delivery_trip(doc)

    def test_fills_when_customer_set_and_address_empty(self):
        doc = self._make_doc([{"customer": "ABC", "address": None, "customer_address": None}])
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   return_value="ABC-Billing"), \
             patch("frappe.contacts.doctype.address.address.get_address_display",
                   return_value="ABC Address Line 1, City"):
            mhr_utilis.fill_default_addresses_on_delivery_trip(doc)
        self.assertEqual(doc.delivery_stops[0].address, "ABC-Billing")
        self.assertEqual(doc.delivery_stops[0].customer_address, "ABC Address Line 1, City")

    def test_get_address_display_failure_is_swallowed(self):
        """get_address_display can raise on broken Address rows; the hook
        must log and continue — never block the Trip save."""
        doc = self._make_doc([{"customer": "ABC", "address": None}])
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   return_value="ABC-Billing"), \
             patch("frappe.contacts.doctype.address.address.get_address_display",
                   side_effect=Exception("broken")):
            try:
                mhr_utilis.fill_default_addresses_on_delivery_trip(doc)
            except Exception as e:
                self.fail(f"Hook must swallow get_address_display errors; raised {e!r}.")
        self.assertEqual(doc.delivery_stops[0].address, "ABC-Billing",
            "address must still be set even when display lookup fails.")


class TestHookWiredInHooksPy(FrappeTestCase):
    def test_hook_registered(self):
        import importlib
        hooks_mod = importlib.import_module("mhr.hooks")
        validate = hooks_mod.doc_events.get("Delivery Trip", {}).get("validate")
        if isinstance(validate, str):
            validate = [validate]
        self.assertIn(
            "mhr.utilis.fill_default_addresses_on_delivery_trip",
            validate or [],
            "Delivery Trip validate hook list must include the address-fill helper.",
        )
