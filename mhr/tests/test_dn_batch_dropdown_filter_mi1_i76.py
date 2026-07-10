"""MI1-I76 (Raj 2026-07-01) — the Batch dropdown on Delivery Note
showed batches for BOTH transaction types. HTY DNs should see only
HTY batches; VFY DNs should see only VFY batches.

Batch already carries `custom_transaction_type` (populated by the
mhr.utilis.set_batch_transaction_type_from_container Batch.validate
hook). Fix is a client-side set_query filter that constrains the two
Batch dropdown surfaces on Delivery Note:

  * Header field: `custom_batch` (Link → Batch)
  * Item-row field: `batch_no` (standard ERPNext Link → Batch)

Both filtered by custom_transaction_type = DN.transaction_type. The
filter re-applies on refresh and whenever transaction_type changes.
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


class TestBatchDropdownFilterInstalled(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script()

    def test_marker_present(self):
        self.assertIn("MI1-I76", self.src,
            "The Batch dropdown filter block must carry the MI1-I76 marker.")

    def test_apply_function_present(self):
        self.assertIn(
            "function mi1_i76_apply_batch_query_filters(frm)",
            self.src,
            "The set_query installer function must exist.",
        )

    def test_filter_key_is_custom_transaction_type(self):
        """Pin the exact filter key. If someone changes it to
        `transaction_type` or similar, the filter would silently miss
        every batch (that field doesn't exist on Batch)."""
        self.assertIn(
            "custom_transaction_type: tt",
            self.src,
            "Filter key must be Batch.custom_transaction_type — that's "
            "the field the mhr.utilis.set_batch_transaction_type_from_container "
            "hook populates.",
        )

    def test_reads_transaction_type_from_dn(self):
        self.assertIn(
            "const tt = frm.doc.transaction_type || ''",
            self.src,
            "Filter must key off the DN's current transaction_type.",
        )

    def test_no_filters_when_transaction_type_empty(self):
        """A brand-new DN before user picks a mode should NOT crash the
        dropdown by filtering on empty string (which would show zero
        batches). Pin the guard."""
        self.assertIn(
            "const filters = tt ? { custom_transaction_type: tt } : {}",
            self.src,
            "When transaction_type is empty, filters must be empty {} — "
            "not { custom_transaction_type: '' } which would match "
            "nothing.",
        )

    def test_header_custom_batch_filter_installed(self):
        self.assertIn(
            "frm.set_query('custom_batch', () => ({ filters }))",
            self.src,
            "The custom_batch (header) Link field must be filtered.",
        )

    def test_item_row_batch_no_filter_installed(self):
        self.assertIn(
            "frm.set_query('batch_no', 'items', () => ({ filters }))",
            self.src,
            "The item-row batch_no field must also be filtered — child "
            "table Link filters use the (fieldname, child_table_fieldname, "
            "fn) signature.",
        )

    def test_wired_on_refresh(self):
        self.assertIn(
            "refresh: mi1_i76_apply_batch_query_filters",
            self.src,
            "The filter must (re)install on refresh — otherwise a "
            "freshly loaded doc has no filter until the user touches "
            "transaction_type.",
        )

    def test_wired_on_transaction_type_change(self):
        self.assertIn(
            "transaction_type: mi1_i76_apply_batch_query_filters",
            self.src,
            "The filter must re-apply when the user switches modes.",
        )


class TestGuardWhenBatchFieldMissing(FrappeTestCase):
    """Defensive: custom_batch is a custom field. On rare setups it may
    not be present — the filter installer must not crash then.
    Pin that we defensively check frm.fields_dict.custom_batch."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script()

    def test_guard_on_custom_batch(self):
        self.assertIn(
            "if (frm.fields_dict.custom_batch)",
            self.src,
            "The custom_batch set_query must be guarded on the field "
            "existing — a fresh site or a stripped-down bench may not "
            "have it, and calling set_query on a missing field throws.",
        )


class TestBatchDoctypeHasTheFilterField(FrappeTestCase):
    """The filter is only useful if Batch actually carries the field.
    Pin the field's existence via meta so this test flags a schema
    regression."""

    def test_batch_has_custom_transaction_type_field(self):
        meta = frappe.get_meta("Batch")
        fieldnames = {df.fieldname for df in meta.fields}
        self.assertIn(
            "custom_transaction_type",
            fieldnames,
            "Batch must expose custom_transaction_type — the MI1-I76 "
            "client-side filter depends on it. If this field is gone, "
            "the dropdown filter silently returns zero rows.",
        )


class TestBatchDoctypeHookPopulatesField(FrappeTestCase):
    """Pin the server-side wiring that populates the field."""

    def test_hook_wired(self):
        import mhr.hooks as h
        batch_validate = h.doc_events.get("Batch", {}).get("validate") or []
        if isinstance(batch_validate, str):
            batch_validate = [batch_validate]
        self.assertIn(
            "mhr.utilis.set_batch_transaction_type_from_container",
            batch_validate,
            "Batch.validate must call "
            "mhr.utilis.set_batch_transaction_type_from_container so the "
            "MI1-I76 filter has data to match against.",
        )
