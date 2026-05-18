"""MI1-I39 Phase 2B — Sales Order company-aware filter tests.

In HTY mode the SO Client Script enforces FRD §SO rules:
  - Warehouse fields (header `set_warehouse` + item rows `warehouse`)
    are filtered by selected Company via set_query.
  - On Company change, default Price List + Cost Center are auto-fetched
    from the Company master.
  - VFY mode is untouched (FRD's hard rule).

These tests pin the wiring in the Client Script source; behavior is
exercised manually via the form (no Frappe server-side hook in Phase 2B).
"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestSalesOrderHTYClientScript(FrappeTestCase):
    """The 'MI1-I39 — Sales Order HTY Mode' Client Script must wire all
    of the FRD §SO 2-5 company-aware behaviors."""

    def setUp(self):
        self.script = frappe.db.get_value(
            "Client Script", "MI1-I39 — Sales Order HTY Mode", "script"
        )
        self.assertIsNotNone(self.script,
            "Sales Order HTY Client Script must exist.")

    def test_set_query_on_header_warehouse(self):
        # FRD rule 2: "Warehouses must filter dynamically based on
        # selected Company field. Add dynamic filter: set_query on
        # warehouse fields → filter by company".
        self.assertIn(
            "frm.set_query('set_warehouse'", self.script,
            "HTY mode must set_query on `set_warehouse` to filter by company.",
        )

    def test_set_query_on_item_warehouse(self):
        # Same rule, applied to item-row warehouse picker.
        self.assertIn(
            "frm.set_query('warehouse', 'items'", self.script,
            "HTY mode must set_query on item-row `warehouse` to filter by company.",
        )

    def test_company_change_handler_fetches_defaults(self):
        # FRD rules 4 + 5: price_list + cost_center auto-fetched on
        # company change from the Company master.
        self.assertIn(
            "frappe.db.get_value('Company'", self.script,
            "HTY mode must consult the Company master on `company` change.",
        )
        self.assertIn(
            "default_price_list", self.script,
            "HTY mode must fetch Company.default_price_list.",
        )
        self.assertIn(
            "cost_center", self.script,
            "HTY mode must fetch Company.cost_center.",
        )

    def test_gated_by_hty_mode(self):
        # FRD's hard rule: VFY mode flow stays identical.
        self.assertIn(
            "frm.doc.transaction_type === 'HTY'", self.script,
            "All P2-B handlers must be guarded by transaction_type === 'HTY'.",
        )

    def test_company_handler_bound(self):
        # The `company` event must be wired so changing Company re-runs
        # the filters + fetch.
        self.assertIn(
            "company: function (frm)", self.script,
            "Client Script must bind a handler on the `company` field.",
        )

    def test_does_not_overwrite_already_set_values(self):
        # Auto-fetch must only fill empty values — don't clobber a
        # price_list the user already chose. Pin the guard.
        self.assertIn(
            "!frm.doc.selling_price_list", self.script,
            "Auto-fetch must skip selling_price_list if user already set one.",
        )
        self.assertIn(
            "!frm.doc.cost_center", self.script,
            "Auto-fetch must skip cost_center if user already set one.",
        )
