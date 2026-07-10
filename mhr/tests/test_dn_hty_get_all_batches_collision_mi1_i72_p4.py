"""MI1-I72 P4 (Raj 2026-07-10) — two enabled Client Scripts on
Delivery Note both defined `async function get_all_batches(container_no)`
in the global scope:

  * 'Fetching details on container no from batch to delivery note' —
    powered a small VFY Lot/Cone chooser; fetched only 9 fields.
  * 'HTY & VFY' — powers the big HTY show_hty_batch_dialog; fetches 20+
    fields including manufacturing_date, batch_qty, stock_uom,
    custom_supplier_batch_no, custom_container_no, custom_warehouse.

Frappe injects both scripts into the same page. The last one loaded won
in the global scope. When HTY & VFY's HTY branch called get_all_batches
it silently invoked the SPARSE version → the HTY modal showed `-` for
Mfg Date / Batch Qty / SBN / Container No / Warehouse.

Fix: rename the sparse one to `get_batches_for_lot_cone_dialog` so it
no longer collides. Also move it into the Mhr module so this ships via
fixtures.
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


class TestSparseGetAllBatchesRenamed(FrappeTestCase):
    """The sparse Lot/Cone-chooser fetcher must no longer be called
    `get_all_batches` — that name is HTY & VFY's."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cs = _script(
            "Fetching details on container no from batch to delivery note"
        )
        cls.src = cls.cs.get("script", "")

    def test_shipped_via_fixtures(self):
        self.assertEqual(self.cs.get("module"), "Mhr",
            "The script must live in the Mhr module so this collision fix "
            "propagates via bench migrate.")

    def test_no_get_all_batches_definition_in_sparse_script(self):
        """The old definition would silently shadow HTY & VFY's full-
        fields fetcher and cause the HTY modal to render sparse rows."""
        self.assertNotIn(
            "async function get_all_batches(container_no)",
            self.src,
            "The sparse fetcher must be renamed. As-was, it shadowed "
            "HTY & VFY's get_all_batches in the shared global scope so "
            "the HTY 'Select Batch' modal showed `-` for Mfg Date, "
            "Batch Qty, SBN, Container No, Warehouse.",
        )

    def test_renamed_definition_present(self):
        self.assertIn(
            "async function get_batches_for_lot_cone_dialog(container_no)",
            self.src,
            "The sparse fetcher must be renamed to "
            "get_batches_for_lot_cone_dialog — a name that mirrors "
            "what it actually powers (small Lot/Cone chooser popup, "
            "VFY-only).",
        )

    def test_caller_updated(self):
        """The VFY branch inside this same script must call the renamed
        function; leaving it on the old name would ReferenceError."""
        self.assertIn(
            "await get_batches_for_lot_cone_dialog(frm.doc.custom_container_no)",
            self.src,
            "The VFY branch call site must switch to the renamed function.",
        )
        self.assertNotIn(
            "await get_all_batches(frm.doc.custom_container_no)",
            self.src,
            "No lingering call to the old name in this script.",
        )

    def test_hty_early_return_preserved(self):
        """The earlier MI1 fix's HTY early-return must still be at the
        top of the async custom_container_no handler."""
        self.assertIn(
            "if ((frm.doc.transaction_type || '').toUpperCase() === 'HTY') return",
            self.src,
            "HTY early-return must remain — this script's popup is VFY-only.",
        )


class TestHtyVfyGetAllBatchesUnchanged(FrappeTestCase):
    """The HTY & VFY script's get_all_batches — the FULL-fields
    version — must remain the sole global `get_all_batches`."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cs = _script("HTY & VFY")
        cls.src = cls.cs.get("script", "")

    def test_full_fields_definition_present(self):
        self.assertIn(
            "async function get_all_batches(container_no)",
            self.src,
            "HTY & VFY must still declare get_all_batches — its callers "
            "(HTY branch of custom_container_no handler → "
            "show_hty_batch_dialog) depend on it.",
        )

    def test_full_fields_list_covers_modal_columns(self):
        """The modal renders Mfg Date, Batch Qty, Stock UOM, Supplier
        Batch No, Container No, Warehouse — the fetch must include all
        of them or the modal renders sparse rows again."""
        for f in (
            "'manufacturing_date'",
            "'batch_qty'",
            "'stock_uom'",
            "'custom_supplier_batch_no'",
            "'custom_container_no'",
            "'custom_warehouse'",
        ):
            self.assertIn(f, self.src,
                f"get_all_batches must request {f} — it's rendered by "
                "show_hty_batch_dialog and dropping it would revert the "
                "P4 bug (sparse HTY modal).")

    def test_show_hty_batch_dialog_reads_those_fields(self):
        for expr in (
            "batch.manufacturing_date",
            "batch.batch_qty",
            "batch.stock_uom",
            "batch.custom_supplier_batch_no",
            "batch.custom_container_no",
            "batch.custom_warehouse",
        ):
            self.assertIn(expr, self.src,
                f"{expr} must be read in show_hty_batch_dialog — this "
                "test pins the client-side contract with get_all_batches.")


class TestOnlyOneGlobalGetAllBatchesDefinition(FrappeTestCase):
    """Cross-script pin: across ALL enabled Client Scripts on Delivery
    Note in fixtures, exactly ONE must define
    `async function get_all_batches(container_no)`. More than one =
    silent shadow in the global scope again."""

    def test_exactly_one_definition_in_fixtures(self):
        path = os.path.join(
            frappe.get_app_path("mhr"), "fixtures", "client_script.json"
        )
        with open(path) as fh:
            data = json.load(fh)
        count = 0
        offenders = []
        for cs in data:
            if cs.get("dt") != "Delivery Note":
                continue
            if not cs.get("enabled"):
                continue
            if "async function get_all_batches(container_no)" in (cs.get("script") or ""):
                count += 1
                offenders.append(cs.get("name"))
        self.assertEqual(
            count, 1,
            f"Exactly one enabled Delivery Note Client Script may declare "
            f"`async function get_all_batches(container_no)`. Got {count}: "
            f"{offenders}. Two declarations shadow in the global scope; the "
            f"last-loaded wins and the other silently misbehaves.",
        )
