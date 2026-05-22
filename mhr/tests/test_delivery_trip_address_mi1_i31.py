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
        # Must not call get_default_address (no stops) and must not touch DB.
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   side_effect=AssertionError("must not call")), \
             patch.object(frappe.db, "exists",
                          side_effect=AssertionError("must not call DB")):
            mhr_utilis.fill_default_addresses_on_delivery_trip(doc)

    def test_stop_without_customer_skipped(self):
        doc = self._make_doc([{"customer": None}])
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   side_effect=AssertionError("must not call for None customer")), \
             patch.object(frappe.db, "exists",
                          side_effect=AssertionError("must not call DB for None customer")):
            mhr_utilis.fill_default_addresses_on_delivery_trip(doc)

    def test_existing_address_skips_fetch_but_still_runs_link_pass(self):
        """MI1-I31 v2: when address is already set, the fetch pass skips
        (good), BUT the auto-link pass still runs to ensure the
        Dynamic Link exists."""
        doc = self._make_doc([{"customer": "ABC", "address": "ABC-Address"}])
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   side_effect=AssertionError("must not call when address set")), \
             patch.object(frappe.db, "exists", return_value=True) as m_exists:
            mhr_utilis.fill_default_addresses_on_delivery_trip(doc)
        # Pass 2 calls frappe.db.exists to check for existing Dynamic Link.
        self.assertTrue(m_exists.called,
            "Pass 2 must check tabDynamic Link for an existing Address↔Customer link.")

    def test_fills_when_customer_set_and_address_empty(self):
        doc = self._make_doc([{"customer": "ABC", "address": None, "customer_address": None}])
        with patch("frappe.contacts.doctype.address.address.get_default_address",
                   return_value="ABC-Billing"), \
             patch("frappe.contacts.doctype.address.address.get_address_display",
                   return_value="ABC Address Line 1, City"), \
             patch.object(frappe.db, "exists", return_value=True):
            # Dynamic Link already exists, so no extra save call.
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
                   side_effect=Exception("broken")), \
             patch.object(frappe.db, "exists", return_value=True):
            try:
                mhr_utilis.fill_default_addresses_on_delivery_trip(doc)
            except Exception as e:
                self.fail(f"Hook must swallow get_address_display errors; raised {e!r}.")
        self.assertEqual(doc.delivery_stops[0].address, "ABC-Billing",
            "address must still be set even when display lookup fails.")


class TestEnsureAddressCustomerLink(FrappeTestCase):
    """MI1-I31 v2 — _ensure_address_customer_link tests."""

    def test_skips_when_link_already_exists(self):
        """Idempotency: if Dynamic Link row already exists, no doc load
        and no save."""
        with patch.object(frappe.db, "exists", return_value=True) as m_exists, \
             patch.object(frappe, "get_doc",
                          side_effect=AssertionError("must not load Address")):
            mhr_utilis._ensure_address_customer_link("ABC-Address", "ABC")
        m_exists.assert_called_once()

    def test_creates_link_when_missing(self):
        """When no Dynamic Link exists, load Address + append + save."""
        addr_doc = MagicMock()
        with patch.object(frappe.db, "exists", return_value=False), \
             patch.object(frappe, "get_doc", return_value=addr_doc):
            mhr_utilis._ensure_address_customer_link("ABC-Address", "ABC")
        addr_doc.append.assert_called_once()
        # Verify the appended row is the right shape.
        args, _ = addr_doc.append.call_args
        self.assertEqual(args[0], "links")
        link_payload = args[1]
        self.assertEqual(link_payload["link_doctype"], "Customer")
        self.assertEqual(link_payload["link_name"], "ABC")
        addr_doc.save.assert_called_once_with(ignore_permissions=True)

    def test_skips_when_address_doesnt_exist(self):
        """Stale stop.address pointing at a deleted Address — skip
        without raising. The Trip save must not be blocked."""
        with patch.object(frappe.db, "exists", return_value=False), \
             patch.object(frappe, "get_doc",
                          side_effect=frappe.DoesNotExistError):
            # Must not raise.
            mhr_utilis._ensure_address_customer_link("__nope__", "ABC")

    def test_save_failure_is_logged_not_raised(self):
        """If Address.save() fails (e.g. missing mandatory fields), log
        but don't bubble up — the Trip save must succeed."""
        addr_doc = MagicMock()
        addr_doc.save.side_effect = Exception("missing pincode")
        with patch.object(frappe.db, "exists", return_value=False), \
             patch.object(frappe, "get_doc", return_value=addr_doc), \
             patch.object(frappe, "log_error") as m_log:
            try:
                mhr_utilis._ensure_address_customer_link("ABC-Address", "ABC")
            except Exception as e:
                self.fail(f"Helper must swallow save errors; raised {e!r}.")
        m_log.assert_called_once()


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
