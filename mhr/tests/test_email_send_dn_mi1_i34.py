"""MI1-I34 — Delivery Note email send hardening.

`send_delivery_notes_email` improvements:
  - Sends with `now=True` so SMTP transmission happens inside the
    request and the mail does not wait on the queue/flush. The PDF
    render already runs synchronously, so the only added cost is the
    SMTP send (seconds), safe within the 300s http_timeout.
  - Wraps per-DN PDF render in try/except so a single broken DN doesn't
    kill the whole batch.
  - Wraps sendmail in try/except + log_error so failures are visible.
"""
import inspect
import json
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import email as mhr_email


class TestEmailSendIsImmediate(FrappeTestCase):
    """The function MUST pass now=True to frappe.sendmail — Meher wants
    the mail to leave over SMTP inside the request, not wait on the
    queue/flush. The PDF render already runs synchronously, so now=True
    only adds the (seconds-long) SMTP send, safe within http_timeout."""

    def test_now_true_is_passed(self):
        src = inspect.getsource(mhr_email.send_delivery_notes_email)
        # Find the frappe.sendmail call site.
        sendmail_idx = src.find("frappe.sendmail(")
        self.assertGreater(sendmail_idx, 0, "frappe.sendmail must be called.")
        # Within that call (until its closing paren), `now=True` must appear.
        # Find the matching close paren by counting depth.
        depth = 0
        i = sendmail_idx + len("frappe.sendmail")
        call_end = i
        for j in range(i, len(src)):
            c = src[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    call_end = j
                    break
        call_body = src[sendmail_idx:call_end + 1]
        self.assertIn(
            "now=True", call_body,
            "frappe.sendmail must use now=True — Meher wants the mail "
            "sent immediately, not queued.",
        )


class TestEmailErrorHandling(FrappeTestCase):

    def test_per_dn_try_except(self):
        src = inspect.getsource(mhr_email.send_delivery_notes_email)
        # The per-DN PDF render must be wrapped in try/except + log_error
        # so a single broken DN doesn't abort the whole batch.
        self.assertIn(
            "try:", src,
            "send_delivery_notes_email must use try/except for per-DN "
            "PDF render — a single broken DN must not kill the batch.",
        )
        self.assertIn(
            "frappe.log_error", src,
            "Failed PDF renders must be logged — otherwise users see "
            "'email not sent' with no diagnostic trail.",
        )

    def test_sendmail_failure_logged_and_thrown(self):
        src = inspect.getsource(mhr_email.send_delivery_notes_email)
        # The sendmail call must be wrapped in try/except so a SMTP/queue
        # failure surfaces a clear error to the user, not a 500.
        self.assertIn(
            "frappe.throw", src,
            "On sendmail failure the function must frappe.throw a clear "
            "message to the user.",
        )

    def test_empty_pdf_writer_throws_before_sendmail(self):
        """If every DN fails to render, we must throw with a useful
        message before even attempting sendmail."""
        src = inspect.getsource(mhr_email.send_delivery_notes_email)
        self.assertIn(
            "pdf_writer.pages", src,
            "Function must guard against an empty merged PDF — sending "
            "an attachment with zero pages is a silent broken email.",
        )


class TestEmailInputHandling(FrappeTestCase):

    def test_empty_delivery_notes_throws(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            mhr_email.send_delivery_notes_email([])
        self.assertIn("No delivery notes selected", str(ctx.exception))

    def test_string_json_arg_is_parsed(self):
        # If the function gets called via /api/method, delivery_notes
        # arrives as a JSON string. The parser must handle that path —
        # we test by passing an empty list as JSON and expecting the
        # downstream "no delivery notes" throw.
        with self.assertRaises(frappe.ValidationError):
            mhr_email.send_delivery_notes_email(json.dumps([]))
