import frappe
import json
from io import BytesIO
from pypdf import PdfWriter


@frappe.whitelist()
def send_delivery_notes_email(delivery_notes, cc=None):
    """
    Send delivery notes as a single merged PDF attachment via email.

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

    # Create merged PDF using PdfWriter
    pdf_writer = PdfWriter()

    for dn_name in delivery_notes:
        pdf_writer = frappe.get_print(
            doctype="Delivery Note",
            name=str(dn_name),
            print_format="Delivery note",
            as_pdf=True,
            output=pdf_writer
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

    frappe.sendmail(
        recipients=[recipient],
        cc=cc_list,
        subject=subject,
        message="Please find attached delivery notes.",
        attachments=attachments,
        now=True
    )

    return f"Email sent successfully with {len(delivery_notes)} delivery note(s) in one PDF"


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
