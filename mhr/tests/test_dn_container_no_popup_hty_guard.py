"""MI1 (2026-07-10) — the 'Select Batch' popup on DN.custom_container_no
must be VFY-only.

Raj 2026-07-10 (screenshot on a brand-new HTY DN, MCZFT-01): setting
Container No fires the Lot / Cone chooser popup even though HTY doesn't
use per-batch Lot / Cone selection — HTY has a dedicated 'Pick
Containers by Lot' 4-step flow (MI1-I39). Guard: early-return the
handler when transaction_type == 'HTY'.

The Client Script 'Fetching details on container no from batch to
delivery note' is DB-resident and shipped via the mhr fixture (Client
Script filter in hooks.py). Pin the guard both in the fixture JSON
(what fresh sites get on migrate) and in the DB row on this site
(what's live NOW).
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


CS_NAME = "Fetching details on container no from batch to delivery note"


class TestHtyGuardInDb(FrappeTestCase):

    def test_client_script_exists_and_enabled(self):
        s = frappe.db.get_value("Client Script", CS_NAME, ["script", "enabled"])
        self.assertIsNotNone(s, "Client Script row must exist.")
        _script, enabled = s
        self.assertTrue(enabled, "Client Script must be enabled.")

    def test_hty_early_return_present(self):
        script = frappe.db.get_value("Client Script", CS_NAME, "script") or ""
        # Guard clause must return before the batch lookup fires.
        self.assertRegex(
            script,
            r"transaction_type\s*\|\|\s*['\"]\s*['\"]\s*\)\.toUpperCase\(\)\s*===\s*['\"]HTY['\"]\s*\)\s*return",
            "Handler must early-return when transaction_type is 'HTY'.",
        )

    def test_guard_precedes_container_no_check(self):
        """Inside the LIVE handler (the last frappe.ui.form.on block —
        the earlier one is inside a /* ... */ comment), the HTY return
        MUST come BEFORE the 'if (frm.doc.custom_container_no)' check.
        Otherwise the popup could still fire when the guard is skipped."""
        script = frappe.db.get_value("Client Script", CS_NAME, "script") or ""
        live_start = script.rfind("frappe.ui.form.on")
        self.assertGreater(live_start, 0, "Live handler not found.")
        live = script[live_start:]
        guard_pos = live.find(".toUpperCase()")
        container_pos = live.find("if (frm.doc.custom_container_no)")
        self.assertGreater(guard_pos, 0, "Guard missing from live handler.")
        self.assertGreater(container_pos, 0)
        self.assertLess(guard_pos, container_pos,
            "HTY guard must precede the custom_container_no gate inside "
            "the LIVE handler block.")


class TestHtyGuardInFixture(FrappeTestCase):
    """Same pin against the exported fixture JSON so fresh sites get
    the guard on the next bench migrate."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(
            frappe.get_app_path("mhr"), "fixtures", "client_script.json"
        )
        with open(path) as f:
            data = json.load(f)
        for cs in data:
            if cs.get("name") == CS_NAME:
                cls.script = cs.get("script") or ""
                return
        raise AssertionError(f"{CS_NAME!r} not in fixtures/client_script.json")

    def test_hty_early_return_in_fixture(self):
        self.assertIn("'HTY'", self.script,
            "Fixture Client Script must reference 'HTY' for the guard.")
        self.assertIn(".toUpperCase() === 'HTY'", self.script,
            "Guard must uppercase the transaction_type before comparing.")
        self.assertIn(") return;", self.script,
            "Guard must be a return, not just a log/warn.")
