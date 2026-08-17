"""MI1-I101 — Delivery Note `custom_notes` from the Container Inward.

MI1-I83 introduced this for VFY only — HTY Delivery Notes were explicitly
out of scope at the time.
MI1-I101 extends it to HTY and adds an on-change refresh, so picking or
switching a Container No populates the field immediately rather than only at
save time.

Two halves, deliberately different:
  * server (validate hook) — backfills only when custom_notes is EMPTY,
    preserving a user override. Unchanged semantics from MI1-I83.
  * client (on-change)     — an explicit container change REPLACES the value.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.note import get_container_notes
from mhr.utilis import fetch_notes_from_container, resolve_container_notes

CLIENT_SCRIPT = "MI1-I101 — Delivery Note Container Notes"


class _FakeDN:
	def __init__(self, **kwargs):
		self.transaction_type = "VFY"
		self.custom_container_no = None
		self.custom_notes = None
		self.__dict__.update(kwargs)

	def get(self, key, default=None):
		return getattr(self, key, default)


def _container_with_notes(transaction_type):
	return frappe.db.get_value(
		"Container",
		{"transaction_type": transaction_type, "notes": ["!=", ""], "docstatus": 1},
		["container_no", "notes"],
		as_dict=True,
	)


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------


class TestResolveContainerNotes(FrappeTestCase):
	def test_blank_container_returns_none(self):
		self.assertIsNone(resolve_container_notes("", "HTY"))
		self.assertIsNone(resolve_container_notes(None, "HTY"))

	def test_resolves_hty_notes(self):
		c = _container_with_notes("HTY")
		if not c:
			self.skipTest("No submitted HTY Container with notes on this site.")
		self.assertEqual(resolve_container_notes(c.container_no, "HTY"), c.notes)

	def test_resolves_vfy_notes(self):
		c = _container_with_notes("VFY")
		if not c:
			self.skipTest("No submitted VFY Container with notes on this site.")
		self.assertEqual(resolve_container_notes(c.container_no, "VFY"), c.notes)

	def test_modes_do_not_read_each_other(self):
		"""An HTY container number must not resolve under a VFY lookup."""
		c = _container_with_notes("HTY")
		if not c:
			self.skipTest("No submitted HTY Container with notes on this site.")
		if frappe.db.exists(
			"Container",
			{"container_no": c.container_no, "transaction_type": "VFY", "docstatus": 1},
		):
			self.skipTest(f"{c.container_no} exists in both modes — not a clean case.")
		self.assertIsNone(resolve_container_notes(c.container_no, "VFY"))

	def test_is_deterministic_across_sibling_containers(self):
		"""container_no is not unique — MZB-32 maps to six Containers. The
		resolver must return the same answer every time, not whatever the DB
		happens to surface."""
		c = _container_with_notes("HTY")
		if not c:
			self.skipTest("No submitted HTY Container with notes on this site.")
		answers = {resolve_container_notes(c.container_no, "HTY") for _ in range(5)}
		self.assertEqual(len(answers), 1, "Resolver must be deterministic.")

	def test_ignores_drafts_and_cancelled(self):
		import inspect

		src = inspect.getsource(resolve_container_notes)
		self.assertIn(
			'"docstatus": 1', src,
			"Only submitted Container Inwards are a valid source of notes.",
		)
		self.assertIn(
			"order_by", src,
			"Sibling containers share a container_no — the pick must be ordered.",
		)


# ---------------------------------------------------------------------------
# validate hook
# ---------------------------------------------------------------------------


class TestFetchNotesFromContainer(FrappeTestCase):
	def test_hty_now_in_scope(self):
		"""The MI1-I83 guard returned early for anything that wasn't VFY."""
		c = _container_with_notes("HTY")
		if not c:
			self.skipTest("No submitted HTY Container with notes on this site.")
		doc = _FakeDN(transaction_type="HTY", custom_container_no=c.container_no)
		fetch_notes_from_container(doc)
		self.assertEqual(doc.custom_notes, c.notes)

	def test_vfy_still_works(self):
		c = _container_with_notes("VFY")
		if not c:
			self.skipTest("No submitted VFY Container with notes on this site.")
		doc = _FakeDN(transaction_type="VFY", custom_container_no=c.container_no)
		fetch_notes_from_container(doc)
		self.assertEqual(doc.custom_notes, c.notes)

	def test_existing_value_is_preserved(self):
		"""Unchanged MI1-I83 rule: first non-empty value wins on save."""
		c = _container_with_notes("HTY")
		if not c:
			self.skipTest("No submitted HTY Container with notes on this site.")
		doc = _FakeDN(
			transaction_type="HTY",
			custom_container_no=c.container_no,
			custom_notes="typed by user",
		)
		fetch_notes_from_container(doc)
		self.assertEqual(doc.custom_notes, "typed by user")

	def test_blank_container_is_a_noop(self):
		doc = _FakeDN(transaction_type="HTY", custom_container_no=None)
		fetch_notes_from_container(doc)
		self.assertIsNone(doc.custom_notes)

	def test_unknown_transaction_type_is_ignored(self):
		doc = _FakeDN(transaction_type="SOMETHING", custom_container_no="X")
		fetch_notes_from_container(doc)
		self.assertIsNone(doc.custom_notes)

	def test_missing_transaction_type_defaults_to_vfy(self):
		"""Legacy DNs predate the field; they must keep behaving as VFY."""
		c = _container_with_notes("VFY")
		if not c:
			self.skipTest("No submitted VFY Container with notes on this site.")
		doc = _FakeDN(transaction_type=None, custom_container_no=c.container_no)
		fetch_notes_from_container(doc)
		self.assertEqual(doc.custom_notes, c.notes)

	def test_hook_is_registered(self):
		events = frappe.get_hooks("doc_events", app_name="mhr") or {}
		handlers = (events.get("Delivery Note") or {}).get("validate") or []
		self.assertIn("mhr.utilis.fetch_notes_from_container", handlers)


# ---------------------------------------------------------------------------
# whitelisted endpoint + client script
# ---------------------------------------------------------------------------


class TestGetContainerNotesEndpoint(FrappeTestCase):
	def test_returns_notes(self):
		c = _container_with_notes("HTY")
		if not c:
			self.skipTest("No submitted HTY Container with notes on this site.")
		self.assertEqual(get_container_notes(c.container_no, "HTY"), c.notes)

	def test_returns_empty_string_not_none(self):
		"""The client checks `if (r.message)` — None and "" both fall through,
		but a string keeps the contract honest."""
		self.assertEqual(get_container_notes("__no_such_container__", "HTY"), "")

	def test_checks_permission(self):
		import inspect

		src = inspect.getsource(get_container_notes)
		self.assertIn("has_permission", src, "Whitelisted endpoints must check permissions.")


class TestOnChangeClientScript(FrappeTestCase):
	def setUp(self):
		self.src = frappe.db.get_value("Client Script", CLIENT_SCRIPT, "script") or ""
		if not self.src:
			self.skipTest(f"{CLIENT_SCRIPT} not synced on this site — run bench migrate.")

	def test_is_enabled_and_scoped_to_delivery_note(self):
		row = frappe.db.get_value(
			"Client Script", CLIENT_SCRIPT, ["enabled", "dt", "view", "module"], as_dict=True
		)
		self.assertEqual(row.enabled, 1)
		self.assertEqual(row.dt, "Delivery Note")
		self.assertEqual(row.view, "Form")
		self.assertEqual(row.module, "Mhr", "Must be module=Mhr so it exports via fixtures.")

	def test_fires_on_container_change(self):
		self.assertIn("custom_container_no: function (frm)", self.src)
		self.assertIn("mhr.note.get_container_notes", self.src)

	def test_is_hty_only(self):
		self.assertIn("!== 'HTY'", self.src, "VFY must keep the MI1-I83 save-time behaviour.")

	def test_is_inert_on_returns(self):
		self.assertIn("frm.doc.is_return", self.src, "MI1-I93: returns must stay inert.")

	def test_does_not_wipe_notes_when_container_has_none(self):
		self.assertIn("if (r && r.message)", self.src)
