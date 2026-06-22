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
        # MI1-I27/I39 follow-up: commit BEFORE enqueue. Otherwise a fast
        # worker picks up the job before this transaction is committed
        # and `frappe.get_doc("Print Batch", X)` raises DoesNotExistError
        # ("Print Batch X not found"). That's what Raj saw — Print Batch
        # creation looked broken because the background PDF job silently
        # failed and `file_url` stayed empty.
        frappe.db.commit()
        frappe.msgprint("PDF generation has been enqueued. You will be notified once it's ready.")
        enqueue(
            method=self.generate_multi_pdf_url,
            queue='default',
            timeout=300,
            job_name=f'generate-multi-pdf-{self.name}',
            print_batch_name=self.name,
            # Retry once if the worker still races us — cheaper than
            # forcing the user to recreate the doc.
            enqueue_after_commit=True,
        )

    @staticmethod
    def generate_multi_pdf_url(print_batch_name):
        # MI1-I27/I39 follow-up: if the worker races the parent
        # transaction (or the doc was deleted between enqueue and run),
        # exit cleanly with a log instead of an uncaught DoesNotExistError.
        if not frappe.db.exists("Print Batch", print_batch_name):
            frappe.log_error(
                message=f"Print Batch {print_batch_name} did not exist when "
                "the background PDF job ran. Likely race with after_insert "
                "commit or doc was deleted before job picked it up.",
                title=f"Print Batch PDF: missing doc {print_batch_name}",
            )
            return

        print_batch = frappe.get_doc("Print Batch", print_batch_name)
        name = print_batch.name  # Use the document's name for the PDF file

        batches = [b.batch for b in print_batch.list_batches]

        doctype = {
            "Batch": batches
        }

        try:
            # MI1-I39 Phase 2E: HTY-mode Print Batch runs use the HTY label
            # layout (Container/Pallet/Den-Fil/Cone/Net Wt/Gross Wt/Grade/
            # Luster/Type/Lot + serial + QR). VFY mode keeps the existing
            # "NB" Print Designer format (FRD's hard rule that the VFY flow
            # stays identical).
            #
            # MI1-I62 (6-up final): HTY mode renders 6 labels per A4 page
            # via a custom multi-PDF builder (download_multi_pdf produces
            # 1-per-page, which doesn't match Raj's reference PDF). VFY
            # path is untouched.
            txn_type = (getattr(print_batch, "transaction_type", None) or "VFY")
            if txn_type == "HTY":
                from mhr.utilis import render_hty_6up_pdf
                pdf_content = render_hty_6up_pdf(batches)
            else:
                download_multi_pdf(doctype, name, "NB")
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
            frappe.reload_doc("mhr", "doctype", "print_batch")

            # Inform the user with a message and trigger the event to open PDF
            frappe.publish_realtime(
                event='pdf_generated',
                message={'file_url': file_url},
                user=frappe.session.user
            )

        except Exception as e:
            frappe.log_error(f"Error generating PDF URL: {str(e)}")
            frappe.throw(f"Failed to generate PDF: {str(e)}")
@frappe.whitelist()
def get_lot_nos(container_no):
    """Get distinct lot numbers from batches that match the given container number"""
    if not container_no:
        return []

    lot_nos = frappe.db.sql("""
        SELECT DISTINCT custom_lot_no
        FROM `tabBatch`
        WHERE custom_container_no = %s
        AND custom_lot_no IS NOT NULL
        AND custom_lot_no != ''
        ORDER BY custom_lot_no
    """, (container_no,), as_list=True)

    return [lot[0] for lot in lot_nos]


@frappe.whitelist()
def get_items(container_no, lot_no):
    """MI1-I27: distinct Items present within one Container + Lot No.

    A Container can carry the same Lot No across multiple Items (deniers)
    — e.g. MCJC-1522 / Lot 13112025 holds two different deniers. The
    Print Batch "Item" Select is populated from this so the user can
    bifurcate and print one item at a time instead of getting a combined
    print for both. Returns [] when either filter is missing.
    """
    if not (container_no and lot_no):
        return []

    items = frappe.db.sql("""
        SELECT DISTINCT item
        FROM `tabBatch`
        WHERE custom_container_no = %s
          AND custom_lot_no = %s
          AND item IS NOT NULL
          AND item != ''
        ORDER BY item
    """, (container_no, lot_no), as_list=True)

    return [row[0] for row in items]