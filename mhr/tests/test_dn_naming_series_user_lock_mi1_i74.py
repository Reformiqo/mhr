"""MI1-I74 (Raj 2026-07-01) — the MI1-I39 P2-F naming_series
auto-switch was wired to BOTH refresh and transaction_type. Refreshes
(including the ones after the batch picker sets header fields) re-ran
the check and forced HTY-DN-.YYYY.- back over the user's manual pick.

Fix: fire the auto-switch ONLY on transaction_type change — that's when
the correct default series legitimately changes.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "MI1-I39 — Delivery Note HTY Mode":
            return cs.get("script", "")
    raise AssertionError("'MI1-I39 — Delivery Note HTY Mode' missing from fixtures.")


class TestNamingSeriesAutoSwitchNoLongerFiresOnRefresh(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script()

    def test_marker_present(self):
        self.assertIn("MI1-I74", self.src,
            "The naming_series wire block must carry the MI1-I74 marker.")

    def test_refresh_no_longer_wired_to_naming_series_apply(self):
        """The buggy wire wrote:
            refresh: mi1_i39_apply_dn_naming_series,
            transaction_type: mi1_i39_apply_dn_naming_series,
        which re-ran the override on every refresh. The fix removed the
        refresh half."""
        self.assertNotIn(
            "refresh: mi1_i39_apply_dn_naming_series",
            self.src,
            "refresh: must NOT be wired to mi1_i39_apply_dn_naming_series — "
            "it re-fires the override after every batch-picker refresh, "
            "wiping the user's manual naming_series pick.",
        )

    def test_transaction_type_still_wired(self):
        """The transaction_type trigger is the intended entry point —
        that's when the correct default series genuinely changes."""
        self.assertIn(
            "transaction_type: mi1_i39_apply_dn_naming_series",
            self.src,
            "transaction_type: must remain wired — that's the legitimate "
            "signal for the default series to change.",
        )

    def test_apply_function_body_unchanged(self):
        """The function body itself was correct — only the wiring was
        wrong. Pin that the core apply logic still exists."""
        self.assertIn(
            "function mi1_i39_apply_dn_naming_series(frm)",
            self.src,
        )
        self.assertIn(
            "if (frm.doc.transaction_type === 'HTY' && hty_opts.length)",
            self.src,
            "HTY branch of the apply function must still exist.",
        )
        self.assertIn(
            "frm.set_value('naming_series', hty_opts[0]);",
            self.src,
            "HTY-side set_value must still be there.",
        )
        self.assertIn(
            "frm.set_value('naming_series', non_hty_opts[0]);",
            self.src,
            "VFY-side set_value must still be there.",
        )


class TestMi1I39PriorFixesStillHold(FrappeTestCase):
    """MI1-I74 layers on top of MI1-I72 (P1..P6) + MI1-I75. Pin that
    the prior HTY-mode + sort logic in the same script survives."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script()

    def test_mi1_i72_hide_in_hty_survives(self):
        for fn in ("custom_lusture", "custom_glue", "custom_pulp"):
            self.assertIn(f"'{fn}'", self.src,
                f"MI1-I72 regression — {fn} lost from hide_in_hty.")

    def test_mi1_i75_sort_still_wired(self):
        self.assertIn("MI1-I75", self.src,
            "MI1-I75 sort marker must remain.")

    def test_pick_by_lot_button_still_wired(self):
        """Regression: the 'Pick Containers by Lot' button — the entire
        reason MI1-I39 exists — must still be there."""
        self.assertIn("Pick Containers by Lot", self.src,
            "The main HTY entry point must survive.")
