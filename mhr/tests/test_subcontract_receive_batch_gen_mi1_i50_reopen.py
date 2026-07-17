"""MI1-I50 reopen (Raj 2026-07-17) — Job Work Receive Warehouse Logic.

Pinned rules per Raj's 2026-07-17 comment:

  1. Warehouse Mapping on Create -> Receive from Subcontractor:
     Source WH = Previous Target WH (auto)
     Target WH = BLANK (user picks manually)

  2. Item Fetch: Item, Quantity, Supplier Batch No, Container No,
     Lot No, Company, Customer, Transaction Type. Only warehouse
     mapping changes.

  3. Auto Batch Creation (on submit): new Batch per row named
     `container_no-lot_no-supplier_batch_no` — e.g. MCJC-1111 +
     01012001 + 3182 -> MCJC-1111-01012001-3182. The Batch is NOT
     copied from the original Send entry.

  4. Validation: if the derived Batch already exists, block with a
     validation message.

  5. Scope: only for Stock Entry rows on a Receive-from-Subcontractor
     entry (i.e. custom_original_send_entry set). Everything else
     stays standard ERPNext.

  6. Match key: reconciliation between Send and Receive rows now keys
     on (item_code, container_no, lot_no, supplier_batch_no) — the
     old (item, batch) key stopped working when the Receive batch
     started diverging from the Send batch.
"""
import inspect

import frappe
from frappe.tests.utils import FrappeTestCase


class TestReceiveBatchIdHelper(FrappeTestCase):
    """The `_receive_batch_id` helper is the single source of truth for
    the batch-name format — pin its behaviour directly."""

    def test_helper_exists(self):
        from mhr import utilis
        self.assertTrue(callable(getattr(utilis, "_receive_batch_id", None)),
            "mhr.utilis._receive_batch_id must exist.")

    def test_returns_hyphen_joined_triplet(self):
        from mhr.utilis import _receive_batch_id

        class Row:
            def get(self, key):
                return {
                    "custom_container_no": "MCJC-1111",
                    "custom_lot_no": "01012001",
                    "custom_supplier_batch_no": "3182",
                }.get(key)

        self.assertEqual(
            _receive_batch_id(Row()), "MCJC-1111-01012001-3182",
            "Raj's spec: `container-lot-supplier_batch`.",
        )

    def test_returns_none_when_any_field_missing(self):
        from mhr.utilis import _receive_batch_id

        class RowMissing:
            def get(self, key):
                return {
                    "custom_container_no": "MCJC-1111",
                    "custom_lot_no": "",  # missing
                    "custom_supplier_batch_no": "3182",
                }.get(key)

        self.assertIsNone(
            _receive_batch_id(RowMissing()),
            "Any missing field -> None so the caller can throw a clean "
            "validation error instead of silently building a broken name.",
        )

    def test_strips_whitespace(self):
        from mhr.utilis import _receive_batch_id

        class RowSpaced:
            def get(self, key):
                return {
                    "custom_container_no": " MCJC-1111 ",
                    "custom_lot_no": " 01012001 ",
                    "custom_supplier_batch_no": " 3182 ",
                }.get(key)

        self.assertEqual(
            _receive_batch_id(RowSpaced()), "MCJC-1111-01012001-3182",
            "Whitespace must be stripped so a stray leading space "
            "doesn't create a distinct 'duplicate' batch.",
        )


class TestCreateReceiveBatchesHook(FrappeTestCase):
    """`create_receive_batches` is wired on Stock Entry.before_submit."""

    def test_hook_registered_in_hooks_py(self):
        import mhr.hooks as hooks
        se = getattr(hooks, "doc_events", {}).get("Stock Entry", {})
        before_submit = se.get("before_submit", [])
        if isinstance(before_submit, str):
            before_submit = [before_submit]
        self.assertIn(
            "mhr.utilis.create_receive_batches",
            before_submit,
            "hooks.py must register create_receive_batches on "
            "Stock Entry.before_submit — otherwise no batch is generated "
            "on submit and the Receive entry fails with 'Batch is "
            "mandatory' (or produces a mystery SLE).",
        )

    def test_helper_is_whitelisted(self):
        from mhr import utilis
        fn = getattr(utilis, "create_receive_batches", None)
        self.assertTrue(callable(fn), "create_receive_batches must exist.")
        self.assertIn(
            fn, frappe.whitelisted,
            "create_receive_batches must be @frappe.whitelist()'d.",
        )

    def test_fast_path_early_return_when_not_receive_entry(self):
        """Every Stock Entry on the system triggers this hook. It MUST
        no-op when `custom_original_send_entry` is empty — otherwise
        we'd try to derive a batch ID for every unrelated SE row."""
        from mhr import utilis
        src = inspect.getsource(utilis.create_receive_batches)
        self.assertIn(
            "_subcontract_source_name(doc)",
            src,
            "create_receive_batches must call _subcontract_source_name(doc) "
            "and return early when it's None.",
        )

    def test_hard_blocks_duplicate_batch(self):
        """Raj's spec: 'If it exists, prevent duplicate creation and
        display an appropriate validation message.'"""
        src = inspect.getsource(_read_module().create_receive_batches)
        self.assertIn(
            'frappe.db.exists("Batch"',
            src,
            "create_receive_batches must check `frappe.db.exists('Batch', ...)`.",
        )
        self.assertIn(
            "frappe.throw",
            src,
            "Duplicate must be a hard throw (validation error), not a warning.",
        )

    def test_blocks_when_derivation_fields_missing(self):
        """If container/lot/supplier_batch aren't all set, we can't derive
        a batch — throw rather than silently skipping."""
        src = inspect.getsource(_read_module().create_receive_batches)
        # The helper returns None, the hook must throw on that.
        self.assertIn(
            "Cannot generate Batch",
            src,
            "Missing container/lot/supplier-batch must fail loudly.",
        )


class TestMatchKeyContainerLotSupplierBatch(FrappeTestCase):
    """The reconciliation key switched from (item, batch) to
    (item, container, lot, supplier_batch) — pin both the new shape
    and the new consumer sites."""

    def test_key_shape(self):
        from mhr.utilis import _subcontract_match_key

        class Item:
            item_code = "X"

            def get(self, key):
                return {
                    "custom_container_no": "C1",
                    "custom_lot_no": "L1",
                    "custom_supplier_batch_no": "SB1",
                }.get(key)

        self.assertEqual(
            _subcontract_match_key(Item()),
            ("X", "C1", "L1", "SB1"),
            "Match key must be (item_code, container_no, lot_no, "
            "supplier_batch_no).",
        )

    def test_key_tolerates_missing_fields(self):
        from mhr.utilis import _subcontract_match_key

        class Item:
            item_code = "X"

            def get(self, key):
                # container_no is set, other two are missing
                return {"custom_container_no": "C1"}.get(key)

        # Missing fields collapse to "" — otherwise a None on one side
        # and "" on the other would falsely mismatch.
        self.assertEqual(
            _subcontract_match_key(Item()),
            ("X", "C1", "", ""),
            "Missing custom fields must collapse to '' so Send/Receive "
            "sides with any None differences still match.",
        )

    def test_validate_receipt_unpacks_new_key(self):
        """The over-receipt validator formats the key into a user-facing
        error — pin that it unpacks all four components, not the old
        two."""
        src = inspect.getsource(_read_module().validate_subcontract_receipt)
        self.assertIn(
            "container_no, lot_no, supplier_batch_no",
            src,
            "validate_subcontract_receipt must unpack the 4-tuple key.",
        )


class TestScopeGuard(FrappeTestCase):
    """Rule 5 in Raj's spec: this customization applies ONLY when
    Stock Entry Type = Send to Subcontractor + Receipt via the Create ->
    Receive from Subcontractor button. Everything else = standard
    ERPNext. All three hooks (validate_subcontract_receipt,
    create_receive_batches, apply_subcontract_receipt) must gate on
    _subcontract_source_name being set."""

    def test_all_hooks_gate_on_source_name(self):
        m = _read_module()
        for fn in (
            m.validate_subcontract_receipt,
            m.create_receive_batches,
            m.apply_subcontract_receipt,
            m.revert_subcontract_receipt,
        ):
            src = inspect.getsource(fn)
            self.assertIn(
                "_subcontract_source_name(doc)",
                src,
                f"{fn.__name__} must call _subcontract_source_name(doc) "
                f"and early-return when None. This hook fires on every "
                f"Stock Entry on the system — the guard is what keeps "
                f"Raj's spec scoped to Send-to-Subcontractor receipts.",
            )


def _read_module():
    from mhr import utilis
    return utilis
