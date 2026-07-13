"""MI1-I78 P7 (Raj 2026-07-13): the SE 'fetch supplier batch → append
items row' flow was leaving s_warehouse blank on the appended row,
so the row inherited the SE header's company-default source
(Vadod - MC = Meher Creations). But the batch was inwarded under
Meher International (Vadod - MI). On submit, ERPNext reports
'Batch has negative stock of -X in warehouse Vadod - MC' because
that warehouse's balance is 0 for the batch.

Fix:
  * mhr.utilis.get_delivery_note_batch now also returns
    `warehouse` — the warehouse holding the largest positive
    balance for the batch, resolved from Serial and Batch Bundle.
  * The client (Stock Entry Container Info's fetch_and_append_batch_se)
    sets s_warehouse from data.warehouse when present.
"""
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


class TestServerReturnsWarehouse(FrappeTestCase):

    def test_helper_function_defined(self):
        from mhr import utilis
        self.assertTrue(
            callable(getattr(utilis, "_resolve_batch_warehouse", None)),
            "mhr.utilis._resolve_batch_warehouse must exist.",
        )

    def test_helper_reads_from_sbb_not_sle(self):
        """Batchwise-valuation batches don't index on SLE.batch_no —
        must read via SBB / SBE."""
        from mhr import utilis
        src = inspect.getsource(utilis._resolve_batch_warehouse)
        self.assertIn(
            "tabSerial and Batch Bundle",
            src,
            "Resolver must query Serial and Batch Bundle — reading SLE "
            "directly misses batchwise-valuation batches.",
        )
        self.assertIn(
            "tabSerial and Batch Entry",
            src,
            "Resolver must join SBE for the batch_no filter.",
        )

    def test_helper_prefers_largest_positive_balance(self):
        """Picking the FIRST warehouse would be wrong if a batch has
        been partially delivered (some warehouses would show 0 or
        even negative). Prefer the largest positive balance."""
        from mhr import utilis
        src = inspect.getsource(utilis._resolve_batch_warehouse)
        self.assertIn("HAVING balance > 0", src)
        self.assertIn("ORDER BY balance DESC", src)
        self.assertIn("LIMIT 1", src)

    def test_helper_returns_none_when_uninwarded(self):
        from mhr.utilis import _resolve_batch_warehouse
        # Non-existent batch name → no SBB entries → None.
        self.assertIsNone(_resolve_batch_warehouse("__does_not_exist__"))

    def test_helper_returns_none_for_empty_input(self):
        from mhr.utilis import _resolve_batch_warehouse
        self.assertIsNone(_resolve_batch_warehouse(None))
        self.assertIsNone(_resolve_batch_warehouse(""))

    def test_get_delivery_note_batch_returns_warehouse_key(self):
        from mhr import utilis
        src = inspect.getsource(utilis.get_delivery_note_batch)
        self.assertIn(
            '"warehouse": resolved_warehouse',
            src,
            "get_delivery_note_batch must include the resolved warehouse "
            "in its return dict.",
        )

    def test_end_to_end_returns_correct_warehouse_for_known_batch(self):
        """Pick a real inwarded batch and verify the resolver returns
        the actual warehouse it lives in. Skips if no such data."""
        rows = frappe.db.sql(
            """
            SELECT sbe.batch_no, sbb.warehouse, SUM(sbe.qty) AS bal
            FROM `tabSerial and Batch Bundle` sbb
            INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name
            WHERE sbb.docstatus = 1
              AND sbb.is_cancelled = 0
              AND sbb.type_of_transaction = 'Inward'
            GROUP BY sbe.batch_no, sbb.warehouse
            HAVING bal > 0
            LIMIT 1
            """,
            as_dict=True,
        )
        if not rows:
            self.skipTest("No inwarded batches in test data.")
        batch_no = rows[0]["batch_no"]
        expected_wh = rows[0]["warehouse"]

        from mhr.utilis import _resolve_batch_warehouse
        got = _resolve_batch_warehouse(batch_no)
        self.assertEqual(
            got, expected_wh,
            f"For batch {batch_no!r} the resolver must return "
            f"{expected_wh!r} — the warehouse where the SBB inwarded it.",
        )


class TestClientAppliesWarehouseToItemRow(FrappeTestCase):

    def _script(self):
        path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
        with open(path) as fh:
            data = json.load(fh)
        for cs in data:
            if cs.get("name") == "Stock Entry Container Info":
                return cs.get("script", "")
        raise AssertionError("SE script missing from fixtures.")

    def test_marker_present(self):
        self.assertIn("MI1-I78 P7", self._script())

    def test_sets_s_warehouse_from_data(self):
        self.assertIn(
            "row_defaults.s_warehouse = data.warehouse;",
            self._script(),
            "Client must set s_warehouse on the item row from data.warehouse.",
        )

    def test_only_sets_when_present(self):
        """If server returns no warehouse (batch not inwarded yet), the
        row falls back to the SE header's default — pin the guard."""
        self.assertIn(
            "if (data.warehouse) {",
            self._script(),
            "Setting s_warehouse must be gated on data.warehouse being "
            "truthy — otherwise a missing warehouse would clobber the "
            "SE default.",
        )
