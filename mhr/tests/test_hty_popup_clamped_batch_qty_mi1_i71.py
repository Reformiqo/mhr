"""MI1-I71 (Raj 2026-07-17): the HTY popup + Select handler must
render / persist the CURRENT SBB balance in `batch_qty`, not the
stale Batch master value.

Root cause of the bug this pins against:
  * The popup was populated by `frappe.client.get_list('Batch', ...)`
    from JS, which returns raw `Batch.batch_qty` (drifts as stock is
    consumed via Serial and Batch Bundle).
  * The Select handler wrote `data.batch_qty` (900 on a batch with
    actual SBB balance 450) into the new DN row's qty.
  * ERPNext then failed submit with 'Batch has negative stock -450 in
    the warehouse Finished Goods - MC'.

Fix has two halves:
  1. `mhr.note.get_hty_batches_by_item` — new server endpoint the JS
     calls instead of `frappe.client.get_list`. Clamps `batch_qty` to
     the SBB available balance.
  2. `mhr.utilis.get_container_batches_with_stock` — already filtered
     zero-balance batches; now ALSO overwrites `batch_qty` with the
     available balance so the popup's 'Batch Qty' column matches
     reality on the container-scoped path too.

Also: the Client Script's `show_hty_batch_dialog` Select handler
skips rows with `batch_qty <= 0` so a depleted batch never becomes
a zero-qty DN row.
"""
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _client_script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "HTY & VFY":
            return cs.get("script", "")
    raise AssertionError("HTY & VFY missing from fixtures.")


class TestNewEndpointExists(FrappeTestCase):

    def test_endpoint_is_whitelisted(self):
        from mhr import note
        fn = getattr(note, "get_hty_batches_by_item", None)
        self.assertTrue(callable(fn),
            "mhr.note.get_hty_batches_by_item must exist.")
        # `frappe.whitelisted` is a set of function objects, not names.
        self.assertIn(
            fn,
            frappe.whitelisted,
            "mhr.note.get_hty_batches_by_item must be @frappe.whitelist()'d — "
            "the HTY popup Client Script calls it from the browser.",
        )

    def test_endpoint_calls_the_clamp_helper(self):
        """The endpoint's job is to return CLAMPED batch_qty. If it
        skips `_clamp_batch_qty_to_available` it would just re-export
        the stale Batch master values — the entire fix is moot."""
        from mhr import note
        src = inspect.getsource(note.get_hty_batches_by_item)
        self.assertIn(
            "_clamp_batch_qty_to_available",
            src,
            "get_hty_batches_by_item must call _clamp_batch_qty_to_available "
            "— otherwise it re-exports the stale Batch master value that "
            "caused the original -450 negative-stock error.",
        )


class TestEndpointClampsBatchQty(FrappeTestCase):
    """Behaviour test: given a Batch with `batch_qty` far above what the
    SBB bundles say is available, the endpoint's response must carry the
    SBB-derived value in `batch_qty` (not the raw master)."""

    def setUp(self):
        # Reuse an existing item that has batches. If none exist we skip
        # rather than fabricating stock docs — this suite runs on a
        # populated bench (mhr.erpera.io).
        rows = frappe.db.sql(
            """
            SELECT b.name, b.item, b.batch_qty
            FROM `tabBatch` b
            WHERE b.batch_qty > 0
              AND EXISTS (
                SELECT 1
                FROM `tabSerial and Batch Entry` sbe
                JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
                WHERE sbe.batch_no = b.name
                  AND sbb.docstatus = 1
                  AND sbb.is_cancelled = 0
              )
            LIMIT 1
            """,
            as_dict=True,
        )
        self.pin = rows[0] if rows else None

    def test_batch_qty_matches_sbb_balance(self):
        if not self.pin:
            self.skipTest("No populated batch to pin against on this bench.")
        from mhr.note import get_hty_batches_by_item

        # Compute what the SBB says the available balance is (largest
        # positive per warehouse) — same shape as _clamp_batch_qty_to_available.
        rows = frappe.db.sql(
            """
            SELECT SUM(sbe.qty) AS balance
            FROM `tabSerial and Batch Bundle` sbb
            JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name
            WHERE sbe.batch_no = %s
              AND sbb.docstatus = 1
              AND sbb.is_cancelled = 0
              AND sbb.type_of_transaction IN ('Inward', 'Outward')
            GROUP BY sbb.warehouse
            HAVING balance > 0
            ORDER BY balance DESC
            LIMIT 1
            """,
            (self.pin["name"],),
            as_dict=True,
        )
        expected_balance = float(rows[0]["balance"]) if rows else 0.0
        master = float(self.pin["batch_qty"])
        expected_clamped = min(master, expected_balance) if master > 0 else expected_balance

        result = get_hty_batches_by_item(self.pin["item"], 0, 500)
        entry = next((b for b in result if b["name"] == self.pin["name"]), None)
        self.assertIsNotNone(
            entry,
            f"Endpoint must return the pinned batch {self.pin['name']} for "
            f"item {self.pin['item']}.",
        )
        self.assertAlmostEqual(
            float(entry["batch_qty"]),
            expected_clamped,
            places=6,
            msg=f"batch_qty on returned batch must be clamped to SBB "
                f"available balance ({expected_clamped}), was "
                f"{entry['batch_qty']}. Master says {master}.",
        )


class TestContainerHelperClamps(FrappeTestCase):
    """The container-scoped helper (`get_container_batches_with_stock`)
    also had to overwrite `batch_qty` — pin that."""

    def test_container_helper_overwrites_batch_qty(self):
        from mhr import utilis
        src = inspect.getsource(utilis.get_container_batches_with_stock)
        self.assertIn(
            'b["batch_qty"] = entry["balance"]',
            src,
            "get_container_batches_with_stock must overwrite each returned "
            "row's batch_qty with the SBB balance — otherwise the container-"
            "scoped popup still shows the stale Batch master value in the "
            "'Batch Qty' column.",
        )


class TestClientScriptWiring(FrappeTestCase):
    """The Client Script must call the new endpoint and skip 0-qty rows
    in the Select handler."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.script = _client_script()

    def test_get_all_batches_by_item_calls_new_endpoint(self):
        self.assertIn(
            "mhr.note.get_hty_batches_by_item",
            self.script,
            "get_all_batches_by_item must route through the stock-aware "
            "server helper. Without this, the popup keeps calling "
            "frappe.client.get_list('Batch', ...) which returns the raw "
            "Batch master batch_qty — the exact bug this ticket fixes.",
        )

    def test_get_all_batches_by_item_no_longer_calls_raw_get_list(self):
        # Fenced-block check: the get_all_batches_by_item body must
        # NOT contain the raw frappe.client.get_list call anymore.
        start = self.script.find("async function get_all_batches_by_item(item)")
        # Find matching close brace at depth 0.
        depth = 0
        i = start
        end = start
        started = False
        while i < len(self.script):
            ch = self.script[i]
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    end = i + 1
                    break
            i += 1
        body = self.script[start:end]
        self.assertNotIn(
            "'frappe.client.get_list'",
            body,
            "get_all_batches_by_item must NOT retain the raw "
            "'frappe.client.get_list' call — that was the source of the "
            "stale batch_qty. Route via mhr.note.get_hty_batches_by_item.",
        )

    def test_select_handler_skips_zero_qty_rows(self):
        self.assertIn(
            "Number(data.batch_qty) > 0",
            self.script,
            "show_hty_batch_dialog's primary_action must guard against "
            "0-batch_qty rows — otherwise a depleted batch becomes a "
            "0-qty DN row that later fails Submit.",
        )
