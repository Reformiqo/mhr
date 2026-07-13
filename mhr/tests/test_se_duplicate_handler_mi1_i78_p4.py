"""MI1-I78 P4 (Raj 2026-07-13) — two enabled Stock Entry Client
Scripts both installed a `custom_supplier_batch_no` handler:

  * 'Stock entry' → fetch_and_append_batch (via mhr.utilis.get_print_batch)
  * 'Stock Entry Container Info' → fetch_and_append_batch_se (via
    mhr.utilis.get_delivery_note_batch)

Both fired on the same event. The first appended the batch to items;
the second saw the row and threw "Batch already exists in the list."

Fix: disable the older, minimal 'Stock entry' script. 'Stock Entry
Container Info' is a strict superset (popup + scan + supplier handler).
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _script(name):
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == name:
            return cs
    raise AssertionError(f"Client Script {name!r} missing from fixtures.")


class TestOlderStockEntryScriptDisabled(FrappeTestCase):

    def test_stock_entry_script_is_disabled(self):
        cs = _script("Stock entry")
        self.assertEqual(
            cs.get("enabled"), 0,
            "The older 'Stock entry' Client Script must be disabled — "
            "its custom_supplier_batch_no + custom_lot_no handlers "
            "duplicate 'Stock Entry Container Info' and cause "
            "'Batch already exists in the list' false-positives when "
            "both fire on the same event.",
        )


class TestStockEntryContainerInfoRemainsSoleHandler(FrappeTestCase):

    def test_container_info_still_enabled(self):
        cs = _script("Stock Entry Container Info")
        self.assertEqual(cs.get("enabled"), 1,
            "'Stock Entry Container Info' must remain enabled — disabling "
            "both would strip the popup entirely.")

    def test_container_info_handles_supplier_batch_no(self):
        src = _script("Stock Entry Container Info").get("script", "")
        self.assertIn(
            "custom_supplier_batch_no: function(frm)",
            src,
            "'Stock Entry Container Info' must retain the "
            "custom_supplier_batch_no handler — it's the sole handler "
            "after disabling 'Stock entry'.",
        )


class TestOnlyOneEnabledCustomSupplierBatchHandlerOnSE(FrappeTestCase):
    """Cross-script structural pin — across ALL enabled SE Client
    Scripts in the fixture, exactly ONE may install
    `custom_supplier_batch_no: function(...)`. More than one =
    duplicate append + false-positive again."""

    def test_exactly_one_enabled_supplier_batch_handler(self):
        path = os.path.join(
            frappe.get_app_path("mhr"), "fixtures", "client_script.json"
        )
        with open(path) as fh:
            data = json.load(fh)
        offenders = []
        for cs in data:
            if cs.get("dt") != "Stock Entry":
                continue
            if not cs.get("enabled"):
                continue
            src = cs.get("script") or ""
            if "custom_supplier_batch_no: function" in src:
                offenders.append(cs.get("name"))
        self.assertEqual(
            offenders, ["Stock Entry Container Info"],
            f"Exactly one enabled SE Client Script may install "
            f"custom_supplier_batch_no: function(...). Got: {offenders}. "
            f"Anything else duplicates the append and triggers the "
            f"'Batch already exists in the list' false-positive.",
        )
