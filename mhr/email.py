import frappe
import json
from io import BytesIO
from pypdf import PdfWriter


@frappe.whitelist()
def send_delivery_notes_email(delivery_notes, cc=None):
    """
    Send delivery notes as a single merged PDF attachment via email.

    MI1-I34 changes:
      - Drop `now=True` so PDF render + SMTP go through Frappe's email
        queue worker. The old synchronous path blocked the HTTP request
        and frequently tripped the gunicorn timeout — users saw "email
        not being sent" because the request died before sendmail returned.
      - Wrap PDF generation in try/except. A single broken DN no longer
        kills the whole batch; it's logged and skipped.
      - Surface a clear error if Frappe.sendmail itself fails so the
        user knows what went wrong.

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
                print_format="Delivery note",
                as_pdf=True,
                output=pdf_writer
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
    attachments = [{
        "fname": "Delivery_Notes.pdf",
        "fcontent": pdf_content
    }]

    # Queue the email — Frappe's worker will deliver it asynchronously.
    # No `now=True` so the HTTP request returns immediately, avoiding
    # the gunicorn timeout that was causing the "email not sent" reports.
    try:
        frappe.sendmail(
            recipients=[recipient],
            cc=cc_list,
            subject=subject,
            message="Please find attached delivery notes.",
            attachments=attachments,
        )
    except Exception:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="MI1-I34: frappe.sendmail failed",
        )
        frappe.throw("Email queue rejected the message — see Error Log for details.")

    note = f"Email queued for {len(delivery_notes) - len(failed)} delivery note(s)."
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
            letterhead=letterhead
        )

    with BytesIO() as merged_pdf:
        pdf_writer.write(merged_pdf)
        return merged_pdf.getvalue()
