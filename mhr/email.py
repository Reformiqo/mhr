import json
from io import BytesIO

import frappe
from pypdf import PdfWriter


def flush_email_queue():
    """Cron entry point — drains the Email Queue every minute.

    Wraps Frappe's `frappe.email.queue.flush()` so a transient SMTP
    blip (auth error, DNS, throttling) doesn't bubble out as a generic
    "scheduler job failed" — instead it logs a clear titled Error Log
    row that names this app and the original exception. The flush
    itself is idempotent: rows already in 'Sent' status are skipped.

    Why this exists: Meher's Email Queue was sitting at 1000+ Not Sent
    because the default scheduled flush wasn't draining fast enough
    (or at all). Running every minute keeps the backlog short without
    forcing `now=True` on send sites — `now=True` blocks the HTTP
    request and trips gunicorn timeouts on big bulk-PDF emails.
    """
    try:
        from frappe.email.queue import flush

        flush()
    except Exception:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="MI1: flush_email_queue failed",
        )


def flush_email_after_insert(doc, method=None):
    """Email Queue `after_insert` hook — send the mail immediately.

    Meher wants outbound email to "work straight", but the standard
    Communication composer (the path their Delivery Note "Send Email"
    button opens) always queues with now=False. Rather than block the
    user's request with a synchronous SMTP send, we enqueue a flush on
    a background RQ worker so the mail leaves within a second or two.

    Key points:
      - `enqueue_after_commit=True`: the job runs only after the current
        transaction commits, so the freshly-inserted Email Queue row is
        visible to the flush (no race).
      - Background worker, not the scheduler: this fires even when the
        scheduler is disabled/lagging — which is why the existing
        `flush_email_queue` cron and `resend_email_queue` weren't
        draining the queue.
      - Idempotent: `flush()` skips rows already in 'Sent' status, so a
        now=True send (which also inserts an Email Queue row) won't be
        double-sent.
      - Only acts on 'Not Sent' rows; respects `send_after` (flush
        leaves future-dated rows alone).
    """
    if getattr(doc, "status", None) != "Not Sent":
        return
    try:
        frappe.enqueue(
            "frappe.email.queue.flush",
            enqueue_after_commit=True,
            queue="short",
        )
    except Exception:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="MI1: flush_email_after_insert failed",
        )


@frappe.whitelist()
def send_delivery_notes_email(delivery_notes, cc=None):
    """
    Send delivery notes as a single merged PDF attachment via email.

    MI1-I34 changes:
      - Send with `now=True` so the mail leaves over SMTP inside the
        request instead of sitting in the Email Queue. The earlier
        queued version depended on the scheduled flush actually running
        — when the scheduler/flush lagged, mail piled up "Not Sent" and
        users reported "email not being sent". The heavy work (PDF
        render) already happens synchronously in this request, so
        `now=True` only adds the few seconds of SMTP transmission; with
        `http_timeout` at 300s that is well within budget.
      - Wrap PDF generation in try/except. A single broken DN no longer
        kills the whole batch; it's logged and skipped.
      - Surface a clear error if Frappe.sendmail itself fails so the
        user knows what went wrong (the failed row stays in the queue
        with Error status for the 1-min flush to retry).

    Args:
        delivery_notes: JSON string or list of delivery note names
        cc: Optional comma-separated CC emails
    """
    if isinstance(delivery_notes, str):
        delivery_notes = json.loads(delivery_notes)

    if not delivery_notes:
        frappe.throw("No delivery notes selected")

    recipient = "billing@meherinternational.in"

    # Build subject with customer names
    subject_parts = []
    for note in delivery_notes:
        customer_name = frappe.db.get_value("Delivery Note", note, "customer")
        subject_parts.append(f"{customer_name} - {note}")

    subject = "Delivery Notes: " + ", ".join(subject_parts)

    # CC list
    cc_list = []
    if cc:
        cc_list = ["warehouse2@meherinternational.in", "haresh@meherinternational.in"]

    # Create merged PDF using PdfWriter. Skip + log on per-DN failure so a
    # single bad print format doesn't kill the whole batch.
    pdf_writer = PdfWriter()
    failed = []
    for dn_name in delivery_notes:
        try:
            pdf_writer = frappe.get_print(
                doctype="Delivery Note",
                name=str(dn_name),
                as_pdf=True,
                output=pdf_writer,
            )
        except Exception:
            failed.append(dn_name)
            frappe.log_error(
                message=frappe.get_traceback(),
                title=f"MI1-I34: PDF render failed for DN {dn_name}",
            )

    if not pdf_writer.pages:
        frappe.throw(
            "All Delivery Notes failed to render — nothing to email. "
            f"Check the Print Format 'Delivery note' and the docs: {delivery_notes}"
        )

    # Get merged PDF content
    with BytesIO() as merged_pdf:
        pdf_writer.write(merged_pdf)
        pdf_content = merged_pdf.getvalue()

    # Create single attachment with all delivery notes
    attachments = [{"fname": "Delivery_Notes.pdf", "fcontent": pdf_content}]

    # Send the email immediately (now=True) — SMTP transmission happens
    # in this request so the mail does not wait on the queue/flush.
    try:
        frappe.sendmail(
            recipients=[recipient],
            cc=cc_list,
            subject=subject,
            message="Please find attached delivery notes.",
            attachments=attachments,
            now=True,
        )
    except Exception:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="MI1-I34: frappe.sendmail failed",
        )
        frappe.throw("Email could not be sent — see Error Log for details.")

    note = f"Email sent for {len(delivery_notes) - len(failed)} delivery note(s)."
    if failed:
        note += f" Skipped {len(failed)} that failed to render: {failed}"
    return note


def get_merged_pdf(doctype, names, print_format=None, letterhead=None, no_letterhead=0):
    """
    Get merged PDF content for multiple documents.
    Similar to frappe.utils.print_format.download_multi_pdf but returns bytes.

    Args:
        doctype: DocType name
        names: List of document names
        print_format: Print format to use
        letterhead: Letterhead name
        no_letterhead: 1 to disable letterhead

    Returns:
        bytes: Merged PDF content
    """
    pdf_writer = PdfWriter()

    for name in names:
        pdf_writer = frappe.get_print(
            doctype=doctype,
            name=name,
            print_format=print_format,
            as_pdf=True,
            output=pdf_writer,
            no_letterhead=no_letterhead,
            letterhead=letterhead,
        )

    with BytesIO() as merged_pdf:
        pdf_writer.write(merged_pdf)
        return merged_pdf.getvalue()
