# Add this method to your app's Python file (e.g., in hooks.py or a custom module)

import frappe
from frappe import _
from frappe.utils.pdf import get_pdf
from frappe.utils import get_url_to_form
from frappe.utils.print_format import print_language
import base64
import os

@frappe.whitelist()
def send_bulk_delivery_note_email(recipients, subject, message, delivery_notes, cc=None, bcc=None, attach_pdf=True):
    """
    Send bulk emails for multiple delivery notes with PDF attachments
    """
    try:
        # Validate inputs
        if not recipients or not delivery_notes:
            frappe.throw(_("Recipients and Delivery Notes are required"))
        
        # Convert string inputs to lists if needed
        if isinstance(recipients, str):
            recipients = [email.strip() for email in recipients.split(',')]
        if isinstance(delivery_notes, str):
            delivery_notes = [delivery_notes]
        if cc and isinstance(cc, str):
            cc = [email.strip() for email in cc.split(',') if email.strip()]
        if bcc and isinstance(bcc, str):
            bcc = [email.strip() for email in bcc.split(',') if email.strip()]
        
        attachments = []
        
        # Generate PDF attachments if requested
        if attach_pdf:
            for delivery_note in delivery_notes:
                try:
                    # Check if delivery note exists and user has permission
                    if not frappe.has_permission("Delivery Note", "read", delivery_note):
                        frappe.throw(_("No permission to access Delivery Note {0}").format(delivery_note))
                    
                    # Get the delivery note document
                    doc = frappe.get_doc("Delivery Note", delivery_note)
                    
                    # Generate PDF
                    pdf_content = get_pdf(
                        frappe.get_print(
                            "Delivery Note", 
                            delivery_note,
                            print_format="Standard"  # You can customize this
                        )
                    )
                    
                    # Add to attachments
                    attachments.append({
                        "fname": f"{delivery_note}.pdf",
                        "fcontent": pdf_content
                    })
                    
                except Exception as e:
                    frappe.log_error(f"Error generating PDF for {delivery_note}: {str(e)}")
                    # Continue with other delivery notes even if one fails
        
        # Send email
        email_args = {
            "recipients": recipients,
            "subject": subject,
            "message": message,
            "attachments": attachments,
            "reference_doctype": "Delivery Note",
            "reference_name": delivery_notes[0] if delivery_notes else None,
        }
        
        # Add CC and BCC if provided
        if cc:
            email_args["cc"] = cc
        if bcc:
            email_args["bcc"] = bcc
        
        # Send the email
        frappe.sendmail(**email_args)
        
        # Log the communication
        for delivery_note in delivery_notes:
            try:
                comm = frappe.get_doc({
                    "doctype": "Communication",
                    "communication_type": "Communication",
                    "communication_medium": "Email",
                    "sent_or_received": "Sent",
                    "reference_doctype": "Delivery Note",
                    "reference_name": delivery_note,
                    "subject": subject,
                    "content": message,
                    "sender": frappe.session.user,
                    "recipients": ";".join(recipients),
                    "cc": ";".join(cc) if cc else "",
                    "bcc": ";".join(bcc) if bcc else "",
                    "has_attachment": 1 if attach_pdf else 0
                })
                comm.insert(ignore_permissions=True)
                
            except Exception as e:
                frappe.log_error(f"Error logging communication for {delivery_note}: {str(e)}")
        
        return {
            "success": True,
            "message": _("Emails sent successfully to {0} recipients").format(len(recipients))
        }
        
    except Exception as e:
        frappe.log_error(f"Bulk email error: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

@frappe.whitelist()
def get_delivery_note_customer_emails(delivery_notes):
    """
    Get customer email addresses for selected delivery notes
    """
    try:
        if isinstance(delivery_notes, str):
            delivery_notes = [delivery_notes]
        
        emails = []
        for delivery_note in delivery_notes:
            doc = frappe.get_doc("Delivery Note", delivery_note)
            
            # Try to get email from contact
            if doc.contact_person:
                contact_doc = frappe.get_doc("Contact", doc.contact_person)
                if contact_doc.email_id:
                    emails.append(contact_doc.email_id)
            
            # Try to get email from customer
            elif doc.customer:
                customer_doc = frappe.get_doc("Customer", doc.customer)
                if hasattr(customer_doc, 'email_id') and customer_doc.email_id:
                    emails.append(customer_doc.email_id)
        
        # Remove duplicates
        unique_emails = list(set(emails))
        
        return {
            "success": True,
            "emails": unique_emails
        }
        
    except Exception as e:
        frappe.log_error(f"Error getting customer emails: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }
    
@frappe.whitelist(allow_guest=True)
def download_receipt(ref):
    try:
        doc = frappe.get_doc("Delivery Note", ref)
        doc.flags.ignore_permissions = 1
        
        with print_language(None):
            pdf_file = frappe.get_print(
                "Delivery Note", 
                doc.name,  
                doc=doc, 
                as_pdf=True, 
                letterhead=None, 
                no_letterhead=1
            )

        # Save the PDF to a file
        file_name = f"{doc.name.replace(' ', '-').replace('/', '-')}.pdf"
        file_path = frappe.get_site_path('private', 'files', file_name)
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # Write the PDF content to file
        with open(file_path, 'wb') as f:
            f.write(pdf_file)
            
        # Create a File document
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": file_name,
            "file_url": f"/files/{file_name}",
            "is_private": 0,
            "content": pdf_file
        })
        file_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Generate the full URL
        site_url = frappe.utils.get_url()
        file_url = f"{site_url}{file_doc.file_url}"
        
        return {
            "success": True,
            "message": _("Receipt URL generated successfully"),
            "url": file_url
        }
        
    except Exception as e:
        frappe.log_error(f"Error generating receipt URL: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

@frappe.whitelist(allow_guest=True)
def get_file_urls(delivery_notes):
    """
    Get URLs for multiple delivery notes
    Args:
        delivery_notes: List of delivery note names or comma-separated string
    Returns:
        Dictionary containing success status and list of URLs
    """
    try:
        # Convert string input to list if needed
        if isinstance(delivery_notes, str):
            delivery_notes = [dn.strip() for dn in delivery_notes.split(',') if dn.strip()]
        
        if not delivery_notes:
            return {
                "success": False,
                "error": _("No delivery notes provided")
            }
        
        urls = []
        failed_docs = []
        
        for ref in delivery_notes:
            try:
                # Reuse the existing download_receipt function
                result = download_receipt(ref)
                if result.get("success"):
                    urls.append({
                        "delivery_note": ref,
                        "url": result.get("url")
                    })
                else:
                    failed_docs.append({
                        "delivery_note": ref,
                        "error": result.get("error")
                    })
            except Exception as e:
                failed_docs.append({
                    "delivery_note": ref,
                    "error": str(e)
                })
        
        return {
            "success": True,
            "message": _("Generated URLs for {0} delivery notes").format(len(urls)),
            "urls": urls,
            "failed_docs": failed_docs
        }
        
    except Exception as e:
        frappe.log_error(f"Error generating multiple receipt URLs: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }