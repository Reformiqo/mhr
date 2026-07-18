"""MI1-I85 (Raj 2026-07-18): exclude 0-Cone / 0-Available-Qty
batches from the Delivery Note Batch dropdown and from Fetch Batches
auto-populate.

Three fix surfaces are pinned here:

  1. `mhr.note.fetch_batches` — server endpoint used by both the
     Fetch Batches checkbox and the HTY / VFY popups. Now:
       * Filters `custom_cone > 0` at query time (unless is_return).
       * Post-clamp, drops any batch whose SBB balance is 0.

  2. Client Script `MI1-I39 — Delivery Note HTY Mode`, function
     `mi1_i76_apply_batch_query_filters`: this is the LAST set_query
     to run on both custom_batch and items[batch_no], so it must
     carry the `custom_cone > 0` filter itself (earlier set_query
     calls get clobbered).

  3. Client Script `Fetch Batches`: skips rows with `batch_qty <= 0`
     before add_child so a depleted batch never becomes a qty=0 DN
     row.

Plus: the legacy `Filter cone Greater then 0` Client Script is
disabled — it was redundant with (2) and its earlier set_query call
was silently overridden anyway.
"""
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


FIXTURE_PATH = os.path.join(
    frappe.get_app_path("mhr"), "fixtures", "client_script.json"
)


def _script(name):
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    for cs in data:
        if cs.get("name") == name:
            return cs
    raise AssertionError(f"Client Script {name!r} missing from fixtures.")


class TestFetchBatchesServerFilterAndDrop(FrappeTestCase):
    """Half 1 — the server endpoint must gate on cone > 0 and drop
    zero-qty batches after clamping. Source-level pins keep the two
    critical lines from being deleted."""

    def test_fetch_batches_gates_on_cone_gt_0(self):
        from mhr import note
        src = inspect.getsource(note.fetch_batches)
        self.assertIn(
            '[">", 0]', src,
            "fetch_batches must filter `custom_cone > 0` — otherwise "
            "0-cone Batches show up in the response and become qty=0 "
            "DN rows (MI1-I85).",
        )
        # Regression: the gate must apply only for non-return flows.
        self.assertIn("is_return is False", src)

    def test_fetch_batches_drops_zero_qty_after_clamp(self):
        from mhr import note
        src = inspect.getsource(note.fetch_batches)
        self.assertIn(
            'float(b.get("batch_qty") or 0) > 0',
            src,
            "fetch_batches must drop batches whose clamped batch_qty "
            "is 0 — those would become non-submittable qty=0 rows.",
        )


class TestHtyModeBatchQueryHasConeFilter(FrappeTestCase):
    """Half 2 — the LAST set_query call on custom_batch (which is in
    the HTY-mode script) must carry the cone > 0 filter, or the
    dropdown reopens the door to 0-cone rows."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script("MI1-I39 — Delivery Note HTY Mode")["script"]

    def test_carries_mi1_i85_marker(self):
        self.assertIn("MI1-I85", self.src,
            "The 2026-07-18 fix must land on this script — it's the "
            "handler that gets the last word on the Batch dropdown.")

    def test_cone_filter_in_query(self):
        self.assertIn(
            "custom_cone: ['>', 0]", self.src,
            "mi1_i76_apply_batch_query_filters must include "
            "`custom_cone: ['>', 0]` in the Batch link-query filters.",
        )

    def test_transaction_type_filter_preserved(self):
        """Regression: the MI1-I76 transaction-type scoping must not
        get lost in the merge with MI1-I85's cone filter."""
        self.assertIn(
            "custom_transaction_type = tt", self.src,
            "MI1-I76's transaction_type scoping must remain — merging "
            "MI1-I85's cone filter must not delete it.",
        )


class TestFetchBatchesScriptSkipsZeroQty(FrappeTestCase):
    """Half 3 — the client-side Fetch Batches handler must skip
    zero-qty rows before add_child."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script("Fetch Batches")["script"]

    def test_carries_mi1_i85_marker(self):
        self.assertIn("MI1-I85", self.src)

    def test_skips_zero_batch_qty(self):
        self.assertIn(
            "Number(data.batch_qty) > 0", self.src,
            "Fetch Batches handler must gate `add_child` on "
            "`Number(data.batch_qty) > 0` — otherwise depleted batches "
            "returned by fetch_batches become zero-quantity DN rows.",
        )


class TestLegacyConeScriptDisabled(FrappeTestCase):
    """The old 'Filter cone Greater then 0' script was superseded by
    the merged filter in the HTY-mode script — its set_query call was
    getting silently overridden anyway. Disable it explicitly so a
    future admin doesn't chase a phantom 'cone filter isn't working'."""

    def test_legacy_script_disabled_in_fixture(self):
        cs = _script("Filter cone Greater then 0")
        self.assertEqual(
            cs.get("enabled"), 0,
            "'Filter cone Greater then 0' Client Script must be "
            "disabled — it was overridden by mi1_i76_apply_batch_query_"
            "filters anyway, so leaving it enabled is a red herring.",
        )
