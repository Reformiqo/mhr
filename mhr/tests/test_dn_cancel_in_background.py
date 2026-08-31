"""Cancelling a large Delivery Note timed out.

MC-CH-DL-DN00035 carries 1000 item rows, and therefore 1000 Stock Ledger
Entries and 1000 Serial and Batch Bundles. Cancelling means cancelling every
one of those bundles as a document and reversing every ledger entry, which
runs past the gunicorn request limit: the browser reported "Request Timed
Out", nothing committed, and the note stayed submitted with no sign of the
attempt.

Same failure MI1-I26 already solved for Stock Entry submit, on the other end
of the document's life, so this reuses that shape:

  endpoint  mhr.utilis.cancel_delivery_note_in_background — validates, then
            enqueues and returns immediately
  worker    _cancel_delivery_note_worker — cancels, commits, publishes
            realtime either way
  form      Client Script "MI1 — Delivery Note Cancel in Background" —
            intercepts the standard Cancel above the row threshold, offers
            the button, reloads on the realtime event

The two things that must not regress: the permission check (the worker runs
outside the request, so the Desk's own Cancel check does not apply to it),
and the deduplication (a second click must not start a competing cancel).
"""

import inspect
import json
import re

import frappe
from frappe.tests.utils import FrappeTestCase

SCRIPT_NAME = "MI1 — Delivery Note Cancel in Background"


def _endpoint_source():
	return inspect.getsource(
		frappe.get_attr("mhr.utilis.cancel_delivery_note_in_background")
	)


def _worker_source():
	return inspect.getsource(frappe.get_attr("mhr.utilis._cancel_delivery_note_worker"))


def _client_script():
	path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
	with open(path, encoding="utf-8") as f:
		records = json.load(f)
	for record in records:
		if record.get("name") == SCRIPT_NAME:
			if not record.get("enabled"):
				raise AssertionError(f"{SCRIPT_NAME!r} is disabled")
			if record.get("dt") != "Delivery Note" or record.get("view") != "Form":
				raise AssertionError(f"{SCRIPT_NAME!r} is not a Delivery Note Form script")
			return (record.get("script") or "").replace("\r\n", "\n")
	raise AssertionError(f"{SCRIPT_NAME!r} is missing from client_script.json")


def _live_code(source):
	"""Drop // comments so a commented-out call cannot pass for a real one."""
	return "\n".join(
		line for line in source.split("\n") if not line.strip().startswith("//")
	)


class TestTheEndpointIsReachableFromTheDesk(FrappeTestCase):
	def test_it_is_whitelisted(self):
		"""frappe.whitelist() registers the function in frappe.whitelisted rather
		than tagging it, so that list is what the check has to look at."""
		method = frappe.get_attr("mhr.utilis.cancel_delivery_note_in_background")
		self.assertIn(
			method,
			frappe.whitelisted,
			"the form calls this over frappe.call; unwhitelisted it is refused",
		)

	def test_the_worker_is_not_whitelisted(self):
		"""It cancels without a permission check — it must not be callable directly."""
		worker = frappe.get_attr("mhr.utilis._cancel_delivery_note_worker")
		self.assertNotIn(worker, frappe.whitelisted)


class TestTheEndpointGuardsBeforeEnqueueing(FrappeTestCase):
	def setUp(self):
		self.source = _endpoint_source()

	def test_it_refuses_a_document_that_is_not_submitted(self):
		self.assertIn("if doc.docstatus != 1:", self.source)

	def test_it_checks_the_cancel_permission(self):
		"""The job runs outside the request as the worker's user, so the Desk's
		own Cancel permission check never reaches it."""
		self.assertIn('doc.check_permission("cancel")', self.source)

	def test_the_permission_check_happens_before_the_enqueue(self):
		self.assertLess(
			self.source.index('check_permission("cancel")'),
			self.source.index("frappe.enqueue("),
		)

	def test_a_second_click_cannot_start_a_competing_cancel(self):
		self.assertIn("deduplicate=True", self.source)
		self.assertRegex(self.source, r'job_id=f"mhr-cancel-delivery-note-\{name\}"')

	def test_it_runs_on_the_long_queue_with_room_for_a_thousand_bundles(self):
		self.assertIn('queue="long"', self.source)
		timeout = re.search(r"timeout=(\d+)", self.source)
		self.assertIsNotNone(timeout)
		self.assertGreaterEqual(int(timeout.group(1)), 1800)

	def test_it_returns_without_waiting_for_the_cancel(self):
		"""The whole point: the HTTP response must not carry the work."""
		self.assertIn('return {"queued": True, "name": name}', self.source)
		self.assertNotIn("doc.cancel()", self.source)


class TestTheWorkerReportsBothOutcomes(FrappeTestCase):
	def setUp(self):
		self.source = _worker_source()

	def test_it_cancels_and_commits(self):
		self.assertIn("doc.cancel()", self.source)
		self.assertIn("frappe.db.commit()", self.source)

	def test_a_failure_rolls_back_and_is_logged(self):
		self.assertIn("frappe.db.rollback()", self.source)
		self.assertIn("frappe.log_error(", self.source)

	def test_the_form_is_told_either_way(self):
		"""publish_realtime sits outside the try/except, so a failed cancel still
		releases the form instead of leaving it waiting forever."""
		self.assertIn('event="mhr_delivery_note_cancelled"', self.source)
		lines = self.source.split("\n")
		publish = next(i for i, text in enumerate(lines) if "publish_realtime(" in text)
		except_at = next(i for i, text in enumerate(lines) if text.strip().startswith("except "))
		self.assertGreater(publish, except_at)

		def indent(i):
			return len(lines[i]) - len(lines[i].lstrip())

		self.assertEqual(indent(publish), indent(except_at))


class TestTheFormOffersItAndBlocksTheSlowPath(FrappeTestCase):
	def setUp(self):
		self.code = _live_code(_client_script())

	def test_the_standard_cancel_is_intercepted_on_a_large_note(self):
		"""form.js reads frappe.validated straight after the before_cancel
		trigger (frappe/public/js/frappe/form/form.js), so clearing it aborts
		the cancel without relying on a rejected promise."""
		body = self.code.split("before_cancel(frm) {", 1)[1].split("\n    },", 1)[0]
		self.assertIn("frappe.validated = false;", body)
		self.assertIn("MI1_DNCANCEL_LARGE_ROWS", body)

	def test_small_notes_still_cancel_normally(self):
		"""Existing behaviour: below the threshold nothing is intercepted."""
		body = self.code.split("before_cancel(frm) {", 1)[1].split("\n    },", 1)[0]
		self.assertIn("if (n <= MI1_DNCANCEL_LARGE_ROWS) return;", body)

	def test_the_button_calls_the_endpoint(self):
		self.assertIn("mhr.utilis.cancel_delivery_note_in_background", self.code)

	def test_the_button_only_appears_on_a_submitted_note(self):
		body = self.code.split("refresh(frm) {", 1)[1]
		self.assertIn("if (frm.doc.docstatus !== 1) return;", body)

	def test_the_realtime_listener_is_registered_once(self):
		"""refresh runs on every render; an unguarded subscription would stack
		and the form would reload once per render."""
		self.assertIn("if (!frm.__mi1_dncancel_listener) {", self.code)
		self.assertIn("frm.__mi1_dncancel_listener = true;", self.code)

	def test_it_listens_for_the_event_the_worker_publishes(self):
		self.assertIn("frappe.realtime.on('mhr_delivery_note_cancelled'", self.code)
		self.assertIn('event="mhr_delivery_note_cancelled"', _worker_source())


class TestItDoesNotDisturbTheOtherDeliveryNoteScripts(FrappeTestCase):
	"""Frappe concatenates every enabled Form Client Script for a DocType into
	one blob and evaluates it together (frappe/desk/form/meta.py ::
	add_custom_script), so a name declared twice breaks all of them."""

	def _dn_form_scripts(self):
		path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
		with open(path, encoding="utf-8") as f:
			records = json.load(f)
		return [
			r
			for r in records
			if r.get("dt") == "Delivery Note" and r.get("view") == "Form" and r.get("enabled")
		]

	def test_no_top_level_name_is_shared_between_two_scripts(self):
		"""Two scripts declaring the same name is the hazard: which one wins
		depends on `creation asc`. A name repeated inside a single script is
		ordinary shadowing and is left alone — two already do it."""
		pattern = re.compile(r"^(?:function|var|let|const)\s+([A-Za-z_$][\w$]*)", re.M)
		owners = {}
		for record in self._dn_form_scripts():
			code = _live_code((record.get("script") or "").replace("\r\n", "\n"))
			for name in set(pattern.findall(code)):
				owners.setdefault(name, set()).add(record["name"])
		shared = {n: sorted(s) for n, s in owners.items() if len(s) > 1}
		self.assertEqual(shared, {})

	def test_this_script_declares_only_prefixed_names(self):
		pattern = re.compile(r"^(?:function|var|let|const)\s+([A-Za-z_$][\w$]*)", re.M)
		for name in pattern.findall(_live_code(_client_script())):
			self.assertRegex(name, r"^(MI1_DNCANCEL_|mi1_dncancel_)")


class TestTheFixtureWillActuallySync(FrappeTestCase):
	"""import_file_by_path skips a record whose DB `modified` is not older than
	the JSON's, so an edit without a bump never reaches a migrated site."""

	def test_the_record_carries_a_modified_timestamp(self):
		path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
		with open(path, encoding="utf-8") as f:
			records = json.load(f)
		record = next(r for r in records if r.get("name") == SCRIPT_NAME)
		self.assertRegex(record["modified"], r"^\d{4}-\d{2}-\d{2} ")
