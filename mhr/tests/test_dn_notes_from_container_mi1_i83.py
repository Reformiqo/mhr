"""MI1-I83 (Raj 2026-07-18): Delivery Note's `custom_notes` must be
auto-populated from the linked Container Inward's `notes` when
creating a VFY DN.

Why not `fetch_from` on the custom field? Because DN.custom_container_no
is a Data field (not a Link), so Frappe's `fetch_from` doesn't apply.
The helper does the (container_no, transaction_type='VFY') lookup
manually on validate.

Scope: VFY only per spec. HTY DNs untouched.
Preserves user-typed overrides (only fills when empty).
"""
import inspect

import frappe
from frappe.tests.utils import FrappeTestCase


class TestHelperShapeAndScope(FrappeTestCase):
    """Cheap source-level pins so a rename / hook-drop fails loud."""

    def test_helper_exists(self):
        from mhr import utilis
        self.assertTrue(
            callable(getattr(utilis, "fetch_notes_from_container", None)),
            "mhr.utilis.fetch_notes_from_container must exist.",
        )

    def test_registered_on_delivery_note_validate(self):
        import mhr.hooks as hooks
        dn = hooks.doc_events.get("Delivery Note", {})
        validate = dn.get("validate", [])
        if isinstance(validate, str):
            validate = [validate]
        self.assertIn(
            "mhr.utilis.fetch_notes_from_container",
            validate,
            "hooks.py must register fetch_notes_from_container on "
            "Delivery Note.validate.",
        )

    def test_scoped_to_vfy(self):
        from mhr.utilis import fetch_notes_from_container
        src = inspect.getsource(fetch_notes_from_container)
        self.assertIn(
            '"VFY"', src,
            "Helper must scope to VFY per Raj's 2026-07-18 spec — "
            "HTY DNs must not be touched.",
        )

    def test_lookup_by_container_no_and_transaction_type(self):
        """Container.container_no isn't unique across HTY/VFY — the
        Container Inward for the same container_no exists twice, once
        per transaction_type. The lookup MUST filter on transaction_type
        or it'll pull an HTY container's notes into a VFY DN."""
        from mhr.utilis import fetch_notes_from_container
        src = inspect.getsource(fetch_notes_from_container)
        self.assertIn(
            '"transaction_type": "VFY"',
            src,
            "Container lookup must include transaction_type filter to "
            "disambiguate between HTY and VFY containers sharing a "
            "container_no.",
        )


class TestBehaviour(FrappeTestCase):
    """Real-doc behavioural pins — construct a synthetic Container +
    DN skeleton and call the helper directly."""

    CONTAINER_NO = "MI1-I83-TESTCONT"
    NOTES_TEXT = "MI1-I83 pin"

    @classmethod
    def _make_container(cls, container_no, notes, transaction_type):
        """Insert a Container row directly, skipping the doctype's
        heavy validate chain (we only need the notes lookup to work)."""
        # Nuke any stale test row from a prior run.
        for stale in frappe.db.get_all(
            "Container",
            filters={"container_no": container_no, "transaction_type": transaction_type},
            fields=["name"],
        ):
            frappe.delete_doc("Container", stale.name, ignore_permissions=True, force=1)
        c = frappe.new_doc("Container")
        c.container_no = container_no
        c.transaction_type = transaction_type
        c.notes = notes
        # Skip validate/insert chain — direct row is enough for
        # frappe.db.get_value to find it.
        c.flags.ignore_validate = True
        c.flags.ignore_mandatory = True
        c.insert(ignore_permissions=True)
        return c.name

    def setUp(self):
        self.vfy = self._make_container(
            self.CONTAINER_NO, self.NOTES_TEXT, "VFY")
        # Also seed an HTY container with the SAME container_no but
        # different notes — the helper must NOT pull this one for a
        # VFY DN (transaction_type disambiguation).
        self.hty = self._make_container(
            self.CONTAINER_NO, "SHOULD NEVER APPEAR", "HTY")
        frappe.db.commit()

    def tearDown(self):
        for n in (self.vfy, self.hty):
            if n and frappe.db.exists("Container", n):
                frappe.delete_doc("Container", n, ignore_permissions=True, force=1)
        frappe.db.commit()

    def _fake_dn(self, **overrides):
        """Frappe hooks accept any doc-like object with `.get()`/attr
        writes — a plain dict-backed shim keeps this test cheap and
        isolated from Delivery Note's own validate chain."""
        class Doc:
            def __init__(self, data):
                self._d = data
            def get(self, key, default=None):
                return self._d.get(key, default)
            def __getattr__(self, key):
                if key.startswith("_"):
                    raise AttributeError(key)
                return self._d.get(key)
            def __setattr__(self, key, value):
                if key == "_d":
                    object.__setattr__(self, key, value)
                else:
                    self._d[key] = value
        base = {"transaction_type": "VFY", "custom_container_no": self.CONTAINER_NO, "custom_notes": None}
        base.update(overrides)
        return Doc(base)

    def test_vfy_dn_empty_notes_gets_container_notes(self):
        from mhr.utilis import fetch_notes_from_container
        dn = self._fake_dn()
        fetch_notes_from_container(dn)
        self.assertEqual(
            dn.custom_notes, self.NOTES_TEXT,
            "VFY DN with empty custom_notes must pull notes from the "
            "VFY Container.",
        )

    def test_user_override_preserved(self):
        from mhr.utilis import fetch_notes_from_container
        dn = self._fake_dn(custom_notes="user typed this")
        fetch_notes_from_container(dn)
        self.assertEqual(
            dn.custom_notes, "user typed this",
            "Existing custom_notes value must not be overwritten — "
            "user-typed text (or Batch-fetched text) wins.",
        )

    def test_hty_dn_untouched(self):
        """Even if an HTY DN has empty notes AND its container has notes,
        the helper must NOT populate — Raj's spec is VFY-only."""
        from mhr.utilis import fetch_notes_from_container
        dn = self._fake_dn(transaction_type="HTY")
        fetch_notes_from_container(dn)
        self.assertIsNone(
            dn.custom_notes,
            "HTY DN must be untouched. If this fails, the transaction-"
            "type scope guard is broken.",
        )

    def test_pulls_vfy_container_not_hty_when_both_share_container_no(self):
        """Regression pin: same container_no exists in both HTY and
        VFY — a VFY DN must pull the VFY container's notes ('MI1-I83
        pin'), NOT the HTY one ('SHOULD NEVER APPEAR')."""
        from mhr.utilis import fetch_notes_from_container
        dn = self._fake_dn()
        fetch_notes_from_container(dn)
        self.assertEqual(
            dn.custom_notes, self.NOTES_TEXT,
            "Wrong container picked up — transaction_type filter isn't "
            "working. This is the exact HTY/VFY container_no ambiguity "
            "we've hit before (MI1-I72 P6).",
        )

    def test_no_container_no_no_op(self):
        from mhr.utilis import fetch_notes_from_container
        dn = self._fake_dn(custom_container_no="")
        fetch_notes_from_container(dn)
        self.assertIsNone(
            dn.custom_notes,
            "Empty custom_container_no must be a no-op — no lookup, "
            "no fabricated value.",
        )

    def test_container_with_empty_notes_no_op(self):
        """If the Container itself has notes='', the helper must leave
        custom_notes alone (don't overwrite empty with empty, don't
        blank out a previously-fetched value)."""
        # Wipe the container's notes.
        frappe.db.set_value("Container", self.vfy, "notes", "")
        frappe.db.commit()

        from mhr.utilis import fetch_notes_from_container
        dn = self._fake_dn(custom_notes="prior value")
        fetch_notes_from_container(dn)
        self.assertEqual(
            dn.custom_notes, "prior value",
            "Empty Container.notes must not overwrite a prior "
            "custom_notes — this is a fetch-when-empty helper, not a "
            "reset-on-save helper.",
        )
