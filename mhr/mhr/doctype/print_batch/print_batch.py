import frappe
from frappe.model.document import Document
from frappe.utils.background_jobs import enqueue
from frappe.utils.print_format import download_multi_pdf

class PrintBatch(Document):
    def validate(self):
        # check the limit of the batches added to the table to 1000 0nly
        if len(self.list_batches) > 1000:
            frappe.throw("You can only add a maximum of 1000 batches to a Print Batch.")
        for batch in self.list_batches:
            frappe.db.sql(f"UPDATE `tabBatch` SET custom_cone = '{batch.cone}' WHERE name = '{batch.batch}'")
            frappe.db.sql(f"UPDATE `tabBatch` SET batch_qty = '{batch.batch_qty}' WHERE name = '{batch.batch}'")

    def after_insert(self):
        self.enqueue_generate_multi_pdf_url()

    def enqueue_generate_multi_pdf_url(self):
        frappe.msgprint("PDF generation has been enqueued. You will be notified once it's ready.")
        enqueue(
            method=self.generate_multi_pdf_url,
            queue='default',
            timeout=300,
            job_name=f'generate-multi-pdf-{self.name}',
            print_batch_name=self.name
        )

    @staticmethod
    def generate_multi_pdf_url(print_batch_name):
        print_batch = frappe.get_doc("Print Batch", print_batch_name)
        name = print_batch.name  # Use the document's name for the PDF file

        batches = [b.batch for b in print_batch.list_batches]
        
        doctype = {
            "Batch": batches
        }
        
        try:
            format = "NB"
            download_multi_pdf(doctype, name, format)
            pdf_content = frappe.local.response.filecontent

            if not pdf_content:
                raise ValueError("PDF content is empty or not generated correctly.")

            # Construct the filename using the document's name
            name_str = name.replace(" ", "-").replace("/", "-")
            filename = f"{name_str}.pdf"

            # Save the PDF content as a File document in the database
            _file = frappe.get_doc({
                "doctype": "File",
                "file_name": filename,
                "is_private": 0,
                "content": pdf_content
            })
            _file.save()
            frappe.db.commit()
            file_url = _file.file_url
            frappe.db.set_value("Print Batch", print_batch_name, "file_url", file_url)

            # Inform the user with a message and trigger the event to open PDF
            frappe.publish_realtime(
                event='pdf_generated',
                message={'file_url': file_url},
                user=frappe.session.user
            )

        except Exception as e:
            frappe.log_error(f"Error generating PDF URL: {str(e)}")
            frappe.throw(f"Failed to generate PDF: {str(e)}")
