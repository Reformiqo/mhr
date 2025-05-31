// Copyright (c) 2025, reformiqo and contributors
// For license information, please see license.txt

frappe.ui.form.on("Merge and Send", {
	refresh(frm) {
		// Only show buttons if document is saved
		if (!frm.is_new()) {
			let messageContent = '';
			let useMergedUrl = frm.doc.enable_merge && frm.doc.merge_url;
			let attachmentsToSend = []; // Array to hold file attachments

			if (useMergedUrl) {
				messageContent = `Hello,<br><br>Here is your merged document:<br><br>${frm.doc.merge_url}`;
				// To attach the merged file, we need its details. Assuming the merge process creates a File document.
				// We would ideally fetch the File document related to frm.doc.merge_url here.
				// For now, we'll prepare a basic attachment structure if the URL is present.
				// A more robust solution might involve storing the File doc name of the merged file.
				if (frm.doc.merge_url) {
					// This is a simplified representation. A real implementation might need the File doctype name and name.
					attachmentsToSend.push({file_url: frm.doc.merge_url, is_private: 0}); // Assuming public file for simplicity
				}
			} else {
				// Create default message with all file URLs
				let documents = frm.doc.documents || [];
				let documentsWithUrls = documents.filter(doc => doc.file_url);

				if (documentsWithUrls.length === 0) {
					// No documents or no URLs, handle this case before creating messageContent
					messageContent = 'No documents with file URLs found.'; // Fallback message
				} else {
					messageContent = 'Hello,<br><br>Here are your documents:<br><br>';
					documentsWithUrls.forEach((doc, index) => {
						messageContent += `${index + 1}. ${doc.file_url}<br>`;
					});
					// If not merging, we could potentially add individual file attachments here
					// This would require fetching the File document names for each child entry
					// For now, we rely on attach_document_print for the main doc's print.
				}
			}

			// WhatsApp Button
			frm.add_custom_button(__('Send WhatsApp'), function() {
				let documents = frm.doc.documents || [];
				let documentsWithUrls = documents.filter(doc => doc.file_url);

				// Check if there's a merged URL or individual URLs available
				if (!useMergedUrl && documentsWithUrls.length === 0) {
					frappe.msgprint(__("No documents with file URLs found. Please generate PDFs first."));
					return;
				}


				// Create and show dialog
				let d = new frappe.ui.Dialog({
					title: __('Send via WhatsApp'),
					fields: [
						{
							fieldname: 'phone',
							label: __('Phone Number'),
							fieldtype: 'Data',
							reqd: 1,
                            default: 917801988820,
							description: __('Enter phone number with country code (e.g., +1234567890)')
						},
						{
							fieldname: 'message',
							label: __('Message'),							
							fieldtype: 'Text Editor',
							reqd: 1,
							default: messageContent.replace(/<br>/g, '\n') // Replace <br> with newline for WhatsApp
						}
					],
					size: 'large',
					primary_action_label: __('Send'),
					primary_action(values) {
						// Remove any spaces or special characters from phone number
						let phone = values.phone.replace(/[^0-9+]/g, '');
						
						// Encode the message for URL
						let encodedMessage = encodeURIComponent(values.message);
						
						// Open WhatsApp with the phone number and message
						window.open(`https://wa.me/${phone}?text=${encodedMessage}`, '_blank');
						
						d.hide();
					}
				});
				
				d.show();
			});

			// Email Button
			frm.add_custom_button(__('Send Email'), function() {
				let documents = frm.doc.documents || [];
				let documentsWithUrls = documents.filter(doc => doc.file_url);

				// Check if there's a merged URL or individual URLs available
				if (!useMergedUrl && documentsWithUrls.length === 0) {
					frappe.msgprint(__("No documents with file URLs found. Please generate PDFs first."));
					return;
				}

				// Open Frappe Communication Composer
				new frappe.views.CommunicationComposer({
                    doctype: frm.doctype,
                    name: frm.doc.name,
                    subject: useMergedUrl ? __('Your Merged Document') : __('Your Documents'), // Change subject if merged
                    message: messageContent, // Use generated message content
                    frm: frm,
                    attach_document_print: !useMergedUrl, // Attach document print only if not using merged URL
                    attachments: attachmentsToSend, // Attach the merged file if available
                    recipients: '',
                    cc: ['viscose@meherinternational.in, haresh@meherinternational.in, warehouse2@meherinternational.in'],
                    bcc: ''
                });
			});
		}
	}
});
