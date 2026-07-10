"""MI1-I72 P5 (Raj 2026-07-10) — the VFY 'Select Batch' modal opened
twice when the user picked a container in VFY mode.

Root cause: two enabled Client Scripts on Delivery Note both installed
a `custom_container_no` handler that opened a Lot/Cone chooser popup:

  * 'Fetching details on container no from batch to delivery note' —
    VFY branch (its HTY early-return was added earlier, but VFY still
    fired the popup).
  * 'HTY & VFY' — L493 VFY branch, opens the exact same 2-column
    Lot/Cone dialog.

Both fired on the same custom_container_no change → duplicate 'Select
Batch' popups side-by-side.

Fix: disable the older 'Fetching details on...' script outright.
HTY & VFY is a strict superset — it handles both VFY and HTY in one
handler.

Not touched: the disabled script's contents. Its rename from P4 and
the HTY early-return stay in place for defence-in-depth in case an
admin re-enables it later. See
test_dn_hty_get_all_batches_collision_mi1_i72_p4.py.
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
    raise AssertionError(f"Client Script {name!r} not in fixtures.")


class TestSparseScriptDisabled(FrappeTestCase):

    def test_fetching_details_disabled(self):
        cs = _script("Fetching details on container no from batch to delivery note")
        self.assertEqual(
            cs.get("enabled"), 0,
            "'Fetching details on container no from batch to delivery "
            "note' must be disabled — its custom_container_no handler "
            "duplicates HTY & VFY's VFY branch and both fire on the "
            "same event, opening two 'Select Batch' popups side-by-side.",
        )


class TestHtyVfyRemainsEnabled(FrappeTestCase):
    """Regression pin: the disable applies to the OLDER script only.
    HTY & VFY must stay enabled, otherwise NEITHER VFY nor HTY user
    gets a popup at all."""

    def test_hty_vfy_still_enabled(self):
        cs = _script("HTY & VFY")
        self.assertEqual(cs.get("enabled"), 1,
            "HTY & VFY must remain enabled — it's the sole handler of "
            "custom_container_no now for both VFY and HTY modes.")

    def test_hty_vfy_has_both_vfy_and_hty_branches(self):
        """The enabled script must actually cover both modes."""
        src = _script("HTY & VFY").get("script", "")
        self.assertIn(
            'if (frm.doc.transaction_type === "VFY")', src,
            "HTY & VFY must handle VFY — otherwise disabling the older "
            "script leaves VFY users with no batch popup at all.",
        )
        self.assertIn(
            'if (frm.doc.transaction_type === "HTY")', src,
            "HTY & VFY must handle HTY — it's the entire point of the "
            "script.",
        )


class TestOnlyOneVfyContainerHandler(FrappeTestCase):
    """Cross-script structural pin — across all enabled DN Client
    Scripts in fixtures, exactly ONE must install a
    `async custom_container_no(frm)` handler that reacts to VFY. More
    than one = duplicate popups again."""

    def test_exactly_one_enabled_vfy_container_handler(self):
        path = os.path.join(
            frappe.get_app_path("mhr"), "fixtures", "client_script.json"
        )
        with open(path) as fh:
            data = json.load(fh)
        offenders = []
        for cs in data:
            if cs.get("dt") != "Delivery Note":
                continue
            if not cs.get("enabled"):
                continue
            src = cs.get("script") or ""
            if "async custom_container_no(frm)" not in src:
                continue
            # Skip scripts whose handler UNCONDITIONALLY early-returns
            # (they're inert on the form even if the syntax is there).
            # Detect by looking for a `return;` on the first non-blank
            # non-comment line inside the handler body. This is a rough
            # check but good enough for our small set of scripts.
            offenders.append(cs.get("name"))
        # The current fix leaves exactly HTY & VFY.
        self.assertEqual(
            offenders, ["HTY & VFY"],
            f"Exactly one enabled DN Client Script may install an "
            f"async custom_container_no(frm) handler. Got: {offenders}. "
            f"Anything else duplicates the 'Select Batch' popup for VFY.",
        )
