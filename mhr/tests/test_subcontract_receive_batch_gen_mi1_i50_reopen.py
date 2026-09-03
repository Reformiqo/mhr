"""MI1-I50 reopen (Raj 2026-07-17, batch rules revised 2026-09-03) — Job Work Receive Warehouse Logic.

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
    """`_receive_batch_id(doc, row)` is the single source of truth for the
    batch-name format. MI1-I50 (Raj 2026-09-03): container and lot come
    from the entry HEADER's Received Container Number / Received Lot No
    (editable — the subcontractor may return in a different container),
    the supplier batch from the row."""

    @staticmethod
    def _doc(**kw):
        d = {"custom_received_container_no": "MC-JC-2222", "custom_received_lot_no": "13042026"}
        d.update(kw)
        return frappe._dict(d)

    @staticmethod
    def _row(sbn="6086"):
        return frappe._dict({"custom_supplier_batch_no": sbn})

    def test_helper_exists(self):
        from mhr import utilis
        self.assertTrue(callable(getattr(utilis, "_receive_batch_id", None)),
            "mhr.utilis._receive_batch_id must exist.")

    def test_returns_hyphen_joined_triplet(self):
        from mhr.utilis import _receive_batch_id
        self.assertEqual(
            _receive_batch_id(self._doc(), self._row()), "MC-JC-2222-13042026-6086",
            "Raj's 2026-09-03 example: MC-JC-2222 + 13042026 + 6086.",
        )

    def test_returns_none_when_any_field_missing(self):
        from mhr.utilis import _receive_batch_id
        self.assertIsNone(_receive_batch_id(self._doc(custom_received_lot_no=""), self._row()),
            "Missing received lot -> None so the caller throws a clean error.")
        self.assertIsNone(_receive_batch_id(self._doc(custom_received_container_no=None), self._row()))
        self.assertIsNone(_receive_batch_id(self._doc(), self._row(sbn="")))

    def test_strips_whitespace(self):
        from mhr.utilis import _receive_batch_id
        self.assertEqual(
            _receive_batch_id(self._doc(custom_received_container_no=" MC-JC-2222 ",
                                        custom_received_lot_no=" 13042026 "), self._row(" 6086 ")),
            "MC-JC-2222-13042026-6086",
            "Whitespace must be stripped so a stray space doesn't create a "
            "distinct 'duplicate' batch.",
        )

    def test_row_level_container_and_lot_are_ignored(self):
        """Stock Entry Detail has no container / lot columns; the 2026-07-17
        row-based derivation therefore never resolved. Pin that the helper
        reads the header, not the row."""
        from mhr.utilis import _receive_batch_id
        row = frappe._dict({"custom_supplier_batch_no": "1",
                            "custom_container_no": "ROW-C", "custom_lot_no": "ROW-L"})
        self.assertEqual(_receive_batch_id(self._doc(), row), "MC-JC-2222-13042026-1")


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


class TestDraftInsertsUnderFullValidation(FrappeTestCase):
    """The rows' targets are blank on purpose (user picks), yet the draft
    must insert without skipping validation.

    History: 2026-07-18 used `flags.ignore_validate` because
    StockEntry.validate_warehouse threw "Target warehouse is mandatory for
    row 1". MI1-I103 replaced that with a header-target default — given a
    header, validate_warehouse fills the rows itself — and pinned that
    ERPNext owns the validation. The shortcut also skipped every mhr hook
    on insert (set_receive_purpose, validate_subcontract_receipt), so it
    must stay gone."""

    def test_no_validation_shortcuts(self):
        src = inspect.getsource(_read_module().make_receive_from_subcontractor)
        for escape in ("ignore_validate", "ignore_mandatory", "flags.ignore"):
            self.assertNotIn(escape, src,
                f"{escape!r} must not be used — MI1-I103: ERPNext owns the validation.")

    def test_header_target_defaults_to_where_it_was_sent_from(self):
        src = inspect.getsource(_read_module().make_receive_from_subcontractor)
        self.assertIn("receipt.to_warehouse = source.from_warehouse or next(", src,
            "The header default is what lets a blank-target row pass validate_warehouse.")


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
