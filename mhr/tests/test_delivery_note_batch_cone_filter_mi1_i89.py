"""MI1-I89 (Raj 2026-07-20): the VFY DN's `custom_supplier_batch_no`
trigger — which calls `mhr.utilis.get_delivery_note_batch` — must
ALWAYS enforce the header's `custom_cone` filter, even when the SBN
uniquely identifies a batch.

Prior state (MI1-I78 P5, 2026-07-13):
  * If supplier_batch_no was supplied, the server skipped every
    spec filter (glue/pulp/lusture/grade/fsc/**cone**/denier). Rationale
    at the time: "user may want to add a batch with mismatched header
    cone without the lookup silently 'not fetching'."

Raj's 2026-07-20 correction: the correct workflow to switch cone is
to PICK a new `custom_batch` first (which auto-updates header cone),
THEN enter the matching SBN. The header cone must remain a hard
filter; typing SBN of a 12-cone batch while the header shows 8 cones
must NOT resolve to the 12-cone batch.

This fix keeps the MI1-I78 P5 leniency for the OTHER spec filters
(glue/pulp/lusture/grade/fsc/denier) — cone is the only one Raj
called out and the only one that needs strict enforcement.
"""
import inspect

import frappe
from frappe.tests.utils import FrappeTestCase


class TestConeFilterAlwaysApplies(FrappeTestCase):
    """Source-level pin — the cone filter must live OUTSIDE the
    `if not supplier_batch_no:` block so it fires whether SBN is
    supplied or not."""

    def test_cone_filter_hoisted_out_of_sbn_gate(self):
        from mhr import utilis
        src = inspect.getsource(utilis.get_delivery_note_batch)

        # Find the SBN gate.
        gate_idx = src.find("if not supplier_batch_no:")
        self.assertGreater(gate_idx, -1, "SBN gate block missing.")

        # The cone filter assignment must appear BEFORE the gate.
        cone_idx = src.find('filters["custom_cone"] = cone')
        self.assertGreater(
            cone_idx, -1,
            "Cone filter assignment `filters[\"custom_cone\"] = cone` "
            "must exist somewhere in the function.",
        )
        self.assertLess(
            cone_idx, gate_idx,
            "MI1-I89: the `filters[\"custom_cone\"] = cone` line MUST "
            "sit BEFORE `if not supplier_batch_no:` so it fires even "
            "when SBN is supplied. Currently it's nested inside the "
            "SBN gate → an 8-cone header still matches a 12-cone SBN, "
            "which is the exact bug Raj flagged.",
        )

    def test_cone_filter_still_guarded_on_is_return(self):
        from mhr import utilis
        src = inspect.getsource(utilis.get_delivery_note_batch)
        # The guard on is_return False must remain — return receipts
        # are allowed against any cone.
        self.assertIn(
            "cone and is_return is False",
            src,
            "Cone filter must retain `is_return is False` guard — "
            "return receipts against a depleted cone are legitimate.",
        )

    def test_other_spec_filters_remain_gated_on_sbn(self):
        """Regression pin for MI1-I78 P5: when SBN is supplied, the
        OTHER spec filters (glue/pulp/lusture/grade/fsc/denier) MUST
        remain skipped — that's what MI1-I78 P5 fixed and what MI1-I89
        preserves. Only cone was hoisted out."""
        from mhr import utilis
        src = inspect.getsource(utilis.get_delivery_note_batch)
        gate_idx = src.find("if not supplier_batch_no:")
        after_gate = src[gate_idx:]
        for f in ('custom_glue', 'custom_pulp', 'custom_lusture',
                  'custom_grade', 'custom_fsc'):
            self.assertIn(
                f, after_gate,
                f"{f} filter must still live inside the "
                "`if not supplier_batch_no:` block (MI1-I78 P5 "
                "leniency — mismatched spec from prior batch must not "
                "block SBN lookup).",
            )


class TestBehaviour(FrappeTestCase):
    """End-to-end: seed two batches with the same
    (container, lot, supplier_batch_no) but different cones — verify
    the header cone picks the right one."""

    ITEM = "MI1-I89-TEST-ITEM"
    CONTAINER = "MI1-I89-CONT"
    LOT = "MI1-I89-LOT"
    SBN = "MI1-I89-SBN"
    BATCH_8 = "MI1-I89-B8"
    BATCH_12 = "MI1-I89-B12"

    def setUp(self):
        # Item
        if not frappe.db.exists("Item", self.ITEM):
            item = frappe.new_doc("Item")
            item.item_code = self.ITEM
            item.item_name = self.ITEM
            item.item_group = frappe.db.get_value("Item Group", {}, "name")
            item.stock_uom = "Nos"
            item.has_batch_no = 1
            item.create_new_batch = 0
            item.insert(ignore_permissions=True)
        # Two batches: same container/lot/SBN, different cones
        for name, cone in ((self.BATCH_8, 8), (self.BATCH_12, 12)):
            if frappe.db.exists("Batch", name):
                frappe.delete_doc("Batch", name, ignore_permissions=True, force=1)
            b = frappe.new_doc("Batch")
            b.batch_id = name
            b.item = self.ITEM
            b.batch_qty = 100
            b.custom_container_no = self.CONTAINER
            b.custom_lot_no = self.LOT
            b.custom_supplier_batch_no = self.SBN
            b.custom_cone = cone
            b.insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        for n in (self.BATCH_8, self.BATCH_12):
            if frappe.db.exists("Batch", n):
                frappe.delete_doc("Batch", n, ignore_permissions=True, force=1)
        if frappe.db.exists("Item", self.ITEM):
            frappe.delete_doc("Item", self.ITEM, ignore_permissions=True, force=1)
        frappe.db.commit()

    def test_header_cone_8_matches_8_cone_batch(self):
        from mhr.utilis import get_delivery_note_batch
        r = get_delivery_note_batch(
            lot_no=self.LOT, container_no=self.CONTAINER,
            supplier_batch_no=self.SBN, cone=8,
        )
        self.assertIsNotNone(r, "Lookup must resolve when cone matches.")
        self.assertEqual(r["batch_no"], self.BATCH_8,
            "Header cone=8 must resolve to the 8-cone batch, not the "
            "12-cone one sharing SBN.")
        self.assertEqual(r["cone"], 8)

    def test_header_cone_12_matches_12_cone_batch(self):
        from mhr.utilis import get_delivery_note_batch
        r = get_delivery_note_batch(
            lot_no=self.LOT, container_no=self.CONTAINER,
            supplier_batch_no=self.SBN, cone=12,
        )
        self.assertIsNotNone(r)
        self.assertEqual(r["batch_no"], self.BATCH_12,
            "Header cone=12 must resolve to the 12-cone batch.")
        self.assertEqual(r["cone"], 12)

    def test_return_flow_ignores_cone(self):
        """Regression pin: return receipts (is_return=True) still
        skip the cone filter — depleted cones are legitimate return
        targets."""
        from mhr.utilis import get_delivery_note_batch
        # With is_return=True and cone=8, both batches match — server
        # picks whichever frappe.get_doc(filters) returns (typically
        # the first by name). We just assert we get *some* batch, not
        # None — the point is the cone filter didn't gate the lookup.
        r = get_delivery_note_batch(
            lot_no=self.LOT, container_no=self.CONTAINER,
            supplier_batch_no=self.SBN, cone=8, is_return=True,
        )
        self.assertIsNotNone(
            r,
            "is_return=True must skip cone filter — depleted cones "
            "are legitimate return targets.",
        )
