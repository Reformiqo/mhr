# Copyright (c) 2025, reformiqo and contributors
# For license information, please see license.txt
from frappe import _
from frappe.utils.pdf import get_pdf
from frappe.utils import get_url_to_form
from frappe.utils.print_format import print_language
from pypdf import PdfWriter
import base64
import os
import frappe
from frappe.model.document import Document


class MergeandSend(Document):
	def validate(self):
		# This will trigger download_receipt which now also handles merging if enable_merge is true
		self.download_receipt()

	def download_receipt(self):
		"""Generates PDFs for documents and optionally merges them."""
		try:
			pdf_files_to_merge = []
			# Fetch document details in case self.documents is not fully loaded
			

			for document_info in self.documents:
				try:
					doc = frappe.get_doc(document_info.document_type, document_info.document)
					doc.flags.ignore_permissions = 1
					
					with print_language(None):
						pdf_content = frappe.get_print(
							document_info.document_type,
							doc.name,  
							doc=doc, 
							as_pdf=True, 
							letterhead=None, 
							no_letterhead=1
						)

					# Save the PDF to a file
					file_name = f"{doc.name.replace(' ', '-').replace('/', '-')}.pdf"
					file_path = frappe.get_site_path('public', 'files', file_name)
					
					# Ensure the directory exists
					os.makedirs(os.path.dirname(file_path), exist_ok=True)
					
					# Write the PDF content to file
					with open(file_path, 'wb') as f:
						f.write(pdf_content)
					
					pdf_files_to_merge.append(file_path)
						
					# Create a File document for individual PDF
					file_doc = frappe.get_doc({
						"doctype": "File",
						"file_name": file_name,
						"file_url": f"/files/{file_name}",
						"is_private": 0,
						"content": pdf_content, # Storing content here might be optional depending on needs
						"attached_to_doctype": self.doctype,
						"attached_to_name": self.name,
                        "folder": "Home/Attachments"
					})
					file_doc.insert(ignore_permissions=True)
					frappe.db.commit()
					
					# Update the child table document with file URL (optional)
					site_url = frappe.utils.get_url()
					document_info.file_url = f"{site_url}{file_doc.file_url}"

					
					frappe.db.commit()
					
				except Exception as e:
					frappe.log_error(f"Error processing document {document_info.document}: {str(e)}")
					# Continue processing other documents even if one fails
					continue

			# --- End of individual PDF generation and saving ---

			if self.enable_merge and pdf_files_to_merge:
				try:
					# Merge the PDFs using PdfWriter
					merged_pdf_writer = PdfWriter()
					for pdf_file in pdf_files_to_merge:
						with open(pdf_file, 'rb') as f:
							merged_pdf_writer.append(f)
					
					# Save the merged PDF to a BytesIO object first
					from io import BytesIO
					merged_pdf_stream = BytesIO()
					merged_pdf_writer.write(merged_pdf_stream)
					merged_pdf_content = merged_pdf_stream.getvalue()

					# Save the merged PDF file
					merged_file_name = f"{self.name.replace(' ', '-').replace('/', '-')}_merged.pdf"
					merged_file_path = frappe.get_site_path('public', 'files', merged_file_name)

					# Ensure the directory exists
					os.makedirs(os.path.dirname(merged_file_path), exist_ok=True)
					
					# Write the merged PDF content to file
					with open(merged_file_path, 'wb') as f:
						f.write(merged_pdf_content)

					# Create a File document for the merged PDF
					merged_file_doc = frappe.get_doc({
						"doctype": "File",
						"file_name": merged_file_name,
						"file_url": f"/files/{merged_file_name}",
						"is_private": 0,
						"content": merged_pdf_content,
						"attached_to_doctype": self.doctype,
						"attached_to_name": self.name,
                        "folder": "Home/Attachments"
					})
					merged_file_doc.insert(ignore_permissions=True)
					frappe.db.commit()

					# Update the merge_url field on the current document
					site_url = frappe.utils.get_url()
					merged_url = f"{site_url}{merged_file_doc.file_url}"
					self.merge_url = merged_url
					frappe.db.commit()
				except Exception as e:
					frappe.log_error(f"Error merging PDFs: {str(e)}", "Merge and Send PDF Processing")
					frappe.throw(_("Error merging PDFs."))

		except Exception as e:
			frappe.log_error(f"Error in download_receipt: {str(e)}", "Merge and Send PDF Processing")
			# The individual document errors are logged in the inner loop
			pass # Do not re-throw here to allow individual document processing to complete