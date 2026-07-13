"""MI1-I78 P5 (Raj 2026-07-13): the DN/SE 'fetch batch by supplier
batch no' flow (mhr.utilis.get_delivery_note_batch) was over-constraining
the lookup. When the client sent supplier_batch_no PLUS spec fields
(cone/glue/pulp/lusture/grade/fsc/denier) from the previously-picked
batch, a mismatched cone would silently block the lookup.

Concrete repro: user picks batch MILA-04710068583400023924 (cone 11)
from the popup — DN/SE header cone becomes 11. User then enters
supplier_batch_no '3700012614' which belongs to a different batch
MILA-04710068583700012614 (same container + lot, cone 12). The
server query filters by cone=11 and returns None. UI reads that as
'not fetching'.

Fix: when supplier_batch_no is supplied it's specific enough to
uniquely identify a batch within a container/lot. Skip the spec
filters. Container_no + lot_no + supplier_batch_no is the minimum
uniqueness scope.
"""
import inspect

import frappe
from frappe.tests.utils import FrappeTestCase


class TestSupplierBatchNoBypassesSpecFilters(FrappeTestCase):

    def test_source_shape(self):
        """Structural pin: the SBN branch must skip the spec filters."""
        from mhr import utilis
        src = inspect.getsource(utilis.get_delivery_note_batch)
        self.assertIn("MI1-I78 P5", src,
            "The get_delivery_note_batch source must carry the "
            "MI1-I78 P5 marker.")
        self.assertIn(
            "if not supplier_batch_no:",
            src,
            "The spec filters must be gated behind "
            "`if not supplier_batch_no:` — when SBN is supplied it "
            "identifies the batch on its own.",
        )

    def test_supplier_batch_no_still_included_in_filters(self):
        """Regression pin: dropping the spec-guarded filters must NOT
        drop the SBN filter itself — that's how the batch resolves."""
        from mhr import utilis
        src = inspect.getsource(utilis.get_delivery_note_batch)
        self.assertIn(
            'filters["custom_supplier_batch_no"] = supplier_batch_no',
            src,
            "The SBN filter itself must remain — it's the resolver.",
        )

    def test_lot_and_container_filters_still_applied_when_present(self):
        """These two identity filters must apply regardless of SBN —
        they scope the SBN lookup to the right container / lot.
        Without them, an SBN duplicated across containers would resolve
        to the wrong batch."""
        from mhr import utilis
        src = inspect.getsource(utilis.get_delivery_note_batch)
        self.assertIn(
            'filters["custom_lot_no"] = lot_no',
            src,
            "custom_lot_no filter must apply regardless of SBN.",
        )
        self.assertIn(
            'filters["custom_container_no"] = container_no',
            src,
            "custom_container_no filter must apply regardless of SBN.",
        )


class TestBehaviorEndToEnd(FrappeTestCase):
    """When SBN is supplied and identifies a batch whose cone/glue/etc.
    disagree with the header, the batch still resolves — not blocked
    by the mismatch."""

    def _find_pair(self):
        """Find any container that has two batches with the SAME lot but
        DIFFERENT cones. If none exists in test data, skip."""
        r = frappe.db.sql(
            """
            SELECT custom_container_no, custom_lot_no
            FROM `tabBatch`
            WHERE custom_container_no IS NOT NULL
              AND custom_lot_no IS NOT NULL
              AND custom_supplier_batch_no IS NOT NULL
              AND custom_supplier_batch_no != ''
            GROUP BY custom_container_no, custom_lot_no
            HAVING COUNT(DISTINCT custom_cone) >= 2
            LIMIT 1
            """,
            as_dict=True,
        )
        if not r:
            return None
        cn, ln = r[0]["custom_container_no"], r[0]["custom_lot_no"]
        rows = frappe.db.sql(
            """SELECT name, custom_cone, custom_supplier_batch_no
               FROM `tabBatch`
               WHERE custom_container_no=%s AND custom_lot_no=%s
               ORDER BY custom_cone
               LIMIT 2""",
            (cn, ln),
            as_dict=True,
        )
        return cn, ln, rows

    def test_mismatched_cone_still_returns_batch(self):
        pair = self._find_pair()
        if not pair:
            self.skipTest("No container has two batches with different cones — data-dependent.")
        cn, ln, rows = pair
        # Take the first batch's SBN, but send the OTHER batch's cone in
        # the request. Pre-P5 that filter combination returned None.
        target_sbn = rows[0]["custom_supplier_batch_no"]
        mismatched_cone = rows[1]["custom_cone"]

        from mhr.utilis import get_delivery_note_batch
        result = get_delivery_note_batch(
            lot_no=ln,
            container_no=cn,
            supplier_batch_no=target_sbn,
            cone=mismatched_cone,
        )
        self.assertIsNotNone(
            result,
            f"With supplier_batch_no={target_sbn!r} the lookup must "
            f"resolve regardless of the header's mismatched cone "
            f"({mismatched_cone}). Pre-P5 the cone filter blocked this.",
        )
        self.assertEqual(
            result.get("batch_no"), rows[0]["name"],
            "The resolved batch must match the SBN, not the cone.",
        )
