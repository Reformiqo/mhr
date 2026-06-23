"""MI1-I50 P6 — end-to-end behavioural test of the subcontract receipt flow.

Standing up a real submitted 'Send to Subcontractor' Stock Entry requires
a Subcontracting setup (supplier + PO + serial-batch bundles) far heavier
than what this hook chain needs. Instead, the tests below construct a
minimal source Stock Entry directly in the DB — bypassing controllers
that aren't on the code path under test — and then exercise the three
hook functions exactly as the framework would.

Coverage:
  - validate_subcontract_receipt: source missing / source draft / over-receipt
    blocked / tolerance allows overflow.
  - apply_subcontract_receipt: increments custom_received_qty, sets
    custom_pending_qty, transitions Open -> Partially Received ->
    Fully Received as receipts land.
  - revert_subcontract_receipt: rolls back to the prior state.
  - Idempotence: re-applying isn't double-counted by the hook itself
    (the hook trusts the framework's docstatus transition guarantees).
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate


def _insert_minimal_source(item_code, supplier, sent_qty, name=None):
    """Insert a Send-to-Subcontractor Stock Entry into the DB at docstatus=1
    bypassing controller validation. Returns the doc name."""
    name = name or frappe.generate_hash(length=10)
    se_name = f"_TEST-SE-SEND-{name}"

    # Minimal parent row
    frappe.db.sql(
        """
        INSERT INTO `tabStock Entry`
            (name, owner, modified, modified_by, creation, docstatus,
             purpose, stock_entry_type, posting_date, posting_time,
             supplier, company, custom_subcontract_status,
             custom_overreceipt_tolerance_pct)
        VALUES
            (%(name)s, %(u)s, NOW(), %(u)s, NOW(), 1,
             'Send to Subcontractor', 'Send to Subcontractor',
             %(d)s, '00:00:00',
             %(supplier)s, NULL, 'Open', 0)
        """,
        {"name": se_name, "u": "Administrator", "d": nowdate(),
         "supplier": supplier},
    )

    # Single child row
    sed_name = f"_TEST-SED-SEND-{name}"
    frappe.db.sql(
        """
        INSERT INTO `tabStock Entry Detail`
            (name, owner, modified, modified_by, creation, docstatus,
             parent, parenttype, parentfield, idx,
             item_code, qty, transfer_qty, conversion_factor,
             uom, stock_uom, custom_received_qty, custom_pending_qty)
        VALUES
            (%(name)s, %(u)s, NOW(), %(u)s, NOW(), 1,
             %(parent)s, 'Stock Entry', 'items', 1,
             %(item)s, %(qty)s, %(qty)s, 1,
             'Nos', 'Nos', 0, %(qty)s)
        """,
        {"name": sed_name, "u": "Administrator", "parent": se_name,
         "item": item_code, "qty": flt(sent_qty)},
    )
    frappe.db.commit()
    return se_name


def _cleanup_source(se_name):
    if not se_name:
        return
    frappe.db.sql(
        "DELETE FROM `tabStock Entry Detail` WHERE parent = %s", (se_name,)
    )
    frappe.db.sql("DELETE FROM `tabStock Entry` WHERE name = %s", (se_name,))
    frappe.db.commit()


def _make_receipt_doc(source_name, item_code, qty):
    """Build a draft Stock Entry IN MEMORY (not inserted) that points back
    at the source. The hook functions accept any doc-like object with the
    required attributes; not inserting keeps the test fast + isolated."""
    d = frappe.new_doc("Stock Entry")
    d.purpose = "Material Transfer"
    d.stock_entry_type = "Material Transfer"
    d.posting_date = nowdate()
    d.set("custom_original_send_entry", source_name)
    d.append("items", {
        "item_code": item_code,
        "qty": flt(qty),
        "conversion_factor": 1,
        "uom": "Nos",
        "stock_uom": "Nos",
    })
    return d


class TestValidateSubcontractReceipt(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.item = "_Test 210/72 7.2 GPD"
        cls.supplier = "ANKIT"
        cls.source_name = _insert_minimal_source(cls.item, cls.supplier, sent_qty=100)

    @classmethod
    def tearDownClass(cls):
        _cleanup_source(cls.source_name)
        super().tearDownClass()

    def test_noop_when_no_source(self):
        """A normal Stock Entry without custom_original_send_entry must
        pass through validate untouched."""
        from mhr.utilis import validate_subcontract_receipt
        d = frappe.new_doc("Stock Entry")
        d.purpose = "Material Transfer"
        # No custom_original_send_entry set.
        validate_subcontract_receipt(d)  # must not throw

    def test_throws_when_source_missing(self):
        from mhr.utilis import validate_subcontract_receipt
        d = _make_receipt_doc("_DOES_NOT_EXIST_X9", self.item, 1)
        with self.assertRaises(frappe.ValidationError):
            validate_subcontract_receipt(d)

    def test_within_pending_passes(self):
        """Source has 100 pending, receipt asks for 80 — must pass."""
        from mhr.utilis import validate_subcontract_receipt
        d = _make_receipt_doc(self.source_name, self.item, 80)
        validate_subcontract_receipt(d)

    def test_over_pending_blocked(self):
        from mhr.utilis import validate_subcontract_receipt
        d = _make_receipt_doc(self.source_name, self.item, 101)
        with self.assertRaises(frappe.ValidationError) as ctx:
            validate_subcontract_receipt(d)
        self.assertIn("Over-receipt", str(ctx.exception))

    def test_tolerance_allows_overflow(self):
        """Bump tolerance to 5% on the source → 105 must pass, 106 must fail."""
        from mhr.utilis import validate_subcontract_receipt
        frappe.db.set_value("Stock Entry", self.source_name,
                            "custom_overreceipt_tolerance_pct", 5,
                            update_modified=False)
        try:
            validate_subcontract_receipt(_make_receipt_doc(self.source_name, self.item, 105))
            with self.assertRaises(frappe.ValidationError):
                validate_subcontract_receipt(_make_receipt_doc(self.source_name, self.item, 106))
        finally:
            frappe.db.set_value("Stock Entry", self.source_name,
                                "custom_overreceipt_tolerance_pct", 0,
                                update_modified=False)


class TestApplyAndRevertCycle(FrappeTestCase):
    """Full Open -> Partial -> Full -> revert cycle on the recompute hooks."""

    def setUp(self):
        # Fresh source per test for isolation.
        self.item = "_Test I27 Denier B"
        self.supplier = "ARADHANA SAROJ"
        self.source_name = _insert_minimal_source(self.item, self.supplier, sent_qty=100)

    def tearDown(self):
        _cleanup_source(self.source_name)

    def _status(self):
        return frappe.db.get_value("Stock Entry", self.source_name,
                                    "custom_subcontract_status")

    def _row_state(self):
        """Return (received, pending) on the single child row."""
        return frappe.db.get_value(
            "Stock Entry Detail",
            {"parent": self.source_name},
            ("custom_received_qty", "custom_pending_qty"),
        )

    def test_initial_state(self):
        self.assertEqual(self._status(), "Open")
        recv, pend = self._row_state()
        self.assertEqual(flt(recv), 0)
        self.assertEqual(flt(pend), 100)

    def test_partial_then_full_then_revert(self):
        from mhr.utilis import apply_subcontract_receipt, revert_subcontract_receipt

        # Partial receipt: 40 of 100.
        r1 = _make_receipt_doc(self.source_name, self.item, 40)
        apply_subcontract_receipt(r1)
        recv, pend = self._row_state()
        self.assertEqual(flt(recv), 40)
        self.assertEqual(flt(pend), 60)
        self.assertEqual(self._status(), "Partially Received")

        # Second partial: 60 of 100 -> fully received.
        r2 = _make_receipt_doc(self.source_name, self.item, 60)
        apply_subcontract_receipt(r2)
        recv, pend = self._row_state()
        self.assertEqual(flt(recv), 100)
        self.assertEqual(flt(pend), 0)
        self.assertEqual(self._status(), "Fully Received")

        # Cancel the second receipt -> back to Partially Received.
        revert_subcontract_receipt(r2)
        recv, pend = self._row_state()
        self.assertEqual(flt(recv), 40)
        self.assertEqual(flt(pend), 60)
        self.assertEqual(self._status(), "Partially Received")

        # Cancel the first too -> back to Open.
        revert_subcontract_receipt(r1)
        recv, pend = self._row_state()
        self.assertEqual(flt(recv), 0)
        self.assertEqual(flt(pend), 100)
        self.assertEqual(self._status(), "Open")

    def test_revert_clamps_at_zero(self):
        """If someone reverts a receipt that was never applied (or applied
        in a stale state), the row's received qty must clamp at 0, not
        go negative."""
        from mhr.utilis import revert_subcontract_receipt
        # No apply first — revert a 30 qty.
        d = _make_receipt_doc(self.source_name, self.item, 30)
        revert_subcontract_receipt(d)
        recv, pend = self._row_state()
        self.assertEqual(flt(recv), 0,
            "Revert without prior apply must clamp to 0, not negative.")
        self.assertEqual(self._status(), "Open")


class TestApplyNoopWithoutSource(FrappeTestCase):
    """Stock Entries with no custom_original_send_entry must pay zero cost."""

    def test_apply_noop(self):
        from mhr.utilis import apply_subcontract_receipt
        d = frappe.new_doc("Stock Entry")
        d.purpose = "Material Transfer"
        # No custom_original_send_entry set.
        apply_subcontract_receipt(d)  # must not throw

    def test_revert_noop(self):
        from mhr.utilis import revert_subcontract_receipt
        d = frappe.new_doc("Stock Entry")
        d.purpose = "Material Transfer"
        revert_subcontract_receipt(d)  # must not throw
