import json
import re
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from mhr import delivery_note_background as bg

SCRIPT_NAME = "MI1 — Delivery Note Submit in Background"


def _client_script():
    path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
    with open(path, encoding="utf-8") as f:
        record = next(r for r in json.load(f) if r.get("name") == SCRIPT_NAME)
    return record, "\n".join(
        line for line in (record.get("script") or "").split("\n") if not line.strip().startswith("//")
    )


def _fake_doc(docstatus):
    doc = MagicMock()
    doc.docstatus = docstatus
    return doc


class TestEndpoint(IntegrationTestCase):
    def test_endpoint_is_whitelisted_and_worker_is_not(self):
        self.assertIn(bg.submit_delivery_note_in_background, frappe.whitelisted)
        self.assertNotIn(bg._submit_delivery_note_worker, frappe.whitelisted)

    def test_refuses_a_note_that_is_not_a_draft(self):
        for docstatus in (1, 2):
            with patch("frappe.get_doc", return_value=_fake_doc(docstatus)), patch("frappe.enqueue") as enqueue:
                with self.assertRaises(frappe.ValidationError):
                    bg.submit_delivery_note_in_background("MAT-DN-TEST")
                enqueue.assert_not_called()

    def test_permission_failure_stops_before_enqueue(self):
        doc = _fake_doc(0)
        doc.check_permission.side_effect = frappe.PermissionError
        with patch("frappe.get_doc", return_value=doc), patch("frappe.enqueue") as enqueue:
            with self.assertRaises(frappe.PermissionError):
                bg.submit_delivery_note_in_background("MAT-DN-TEST")
        doc.check_permission.assert_called_once_with("submit")
        enqueue.assert_not_called()

    def test_draft_is_queued_once_on_the_long_queue_and_not_submitted_inline(self):
        doc = _fake_doc(0)
        with patch("frappe.get_doc", return_value=doc), patch("frappe.enqueue") as enqueue:
            result = bg.submit_delivery_note_in_background("MAT-DN-TEST")
        self.assertEqual(result, {"queued": True, "name": "MAT-DN-TEST"})
        doc.submit.assert_not_called()
        kwargs = enqueue.call_args.kwargs
        self.assertEqual(kwargs["method"], "mhr.delivery_note_background._submit_delivery_note_worker")
        self.assertEqual(kwargs["queue"], "long")
        self.assertGreaterEqual(kwargs["timeout"], 1800)
        self.assertEqual(kwargs["job_id"], "mhr-submit-delivery-note-MAT-DN-TEST")
        self.assertTrue(kwargs["deduplicate"])
        self.assertEqual(kwargs["name"], "MAT-DN-TEST")


class TestWorker(IntegrationTestCase):
    def _run(self, doc):
        with patch("frappe.get_doc", return_value=doc), \
             patch.object(frappe.db, "commit") as commit, \
             patch.object(frappe.db, "rollback") as rollback, \
             patch("frappe.log_error") as log_error, \
             patch("frappe.publish_realtime") as publish:
            bg._submit_delivery_note_worker("MAT-DN-TEST", "someone@example.com")
        return commit, rollback, log_error, publish

    def test_success_submits_the_same_document_commits_and_reports_ok(self):
        doc = _fake_doc(0)
        commit, rollback, log_error, publish = self._run(doc)
        doc.submit.assert_called_once_with()
        doc.amend.assert_not_called()
        commit.assert_called_once()
        rollback.assert_not_called()
        log_error.assert_not_called()
        self.assertEqual(publish.call_args.kwargs["event"], "mhr_delivery_note_submitted")
        self.assertEqual(publish.call_args.kwargs["message"], {"name": "MAT-DN-TEST", "ok": True, "error": ""})
        self.assertEqual(publish.call_args.kwargs["user"], "someone@example.com")

    def test_failure_rolls_back_logs_and_still_releases_the_form(self):
        doc = _fake_doc(0)
        doc.submit.side_effect = frappe.ValidationError("insufficient stock")
        commit, rollback, log_error, publish = self._run(doc)
        commit.assert_not_called()
        rollback.assert_called_once()
        log_error.assert_called_once()
        message = publish.call_args.kwargs["message"]
        self.assertFalse(message["ok"])
        self.assertIn("insufficient stock", message["error"])

    def test_already_submitted_note_is_left_alone(self):
        doc = _fake_doc(1)
        commit, rollback, log_error, publish = self._run(doc)
        doc.submit.assert_not_called()
        self.assertTrue(publish.call_args.kwargs["message"]["ok"])


class TestClientScript(IntegrationTestCase):
    def setUp(self):
        self.record, self.code = _client_script()

    def test_fixture_record_is_an_enabled_delivery_note_form_script(self):
        self.assertEqual(self.record["dt"], "Delivery Note")
        self.assertEqual(self.record["view"], "Form")
        self.assertEqual(self.record["module"], "Mhr")
        self.assertEqual(self.record["enabled"], 1)
        self.assertRegex(self.record["modified"], r"^\d{4}-\d{2}-\d{2} ")

    def test_threshold_is_50_rows(self):
        match = re.search(r"var MI1_DNSUBMIT_LARGE_ROWS = (\d+);", self.code)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 50)

    def test_button_only_on_a_saved_draft_above_the_threshold(self):
        refresh = self.code.split("refresh(frm) {", 1)[1]
        self.assertIn("if (frm.doc.docstatus !== 0 || frm.is_new()) return;", refresh)
        self.assertIn("if (mi1_dnsubmit_row_count(frm) <= MI1_DNSUBMIT_LARGE_ROWS) return;", refresh)
        self.assertLess(refresh.index("frm.is_new()) return;"), refresh.index("add_custom_button(__('Submit in Background')"))

    def test_standard_submit_is_intercepted_above_the_threshold(self):
        body = self.code.split("before_submit(frm) {", 1)[1].split("\n    },", 1)[0]
        self.assertIn("if (n <= MI1_DNSUBMIT_LARGE_ROWS) return;", body)
        self.assertIn("frappe.validated = false;", body)

    def test_button_saves_unsaved_changes_before_queueing(self):
        self.assertIn("if (frm.is_dirty()) {", self.code)
        self.assertIn("frm.save().then(function () {", self.code)

    def test_calls_the_endpoint_and_listens_once_for_the_worker_event(self):
        self.assertIn("mhr.delivery_note_background.submit_delivery_note_in_background", self.code)
        self.assertIn("if (!frm.__mi1_dnsubmit_listener) {", self.code)
        self.assertIn("frappe.realtime.on('mhr_delivery_note_submitted'", self.code)

    def test_top_level_names_are_prefixed(self):
        names = re.findall(r"^(?:var|function)\s+([A-Za-z_$][\w$]*)", self.code, re.M)
        self.assertTrue(names)
        for name in names:
            self.assertRegex(name, r"^(MI1_DNSUBMIT_|mi1_dnsubmit_)")
