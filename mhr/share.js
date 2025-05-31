// Store the original settings before modification
const originalListViewSettings = frappe.listview_settings['Delivery Note'] || {};

// Create a new object with the original settings
frappe.listview_settings['Delivery Note'] = {
    ...originalListViewSettings,

    onload: function (listview) {
        // Call original onload if it exists
        if (originalListViewSettings.onload) {
            originalListViewSettings.onload(listview);
        }

        // --- "Send WhatsApp" button (multi-doc support) ---
        listview.page.add_action_item(__('Send WhatsApp'), function () {
            const selected_docs = listview.get_checked_items();
            if (selected_docs.length === 0) {
                frappe.msgprint(__('Please select at least one Delivery Note'));
                return;
            }
            console.log( selected_docs.map(doc => doc.name)

            // Get URLs for all selected delivery notes
            frappe.call({
                method: 'mhr.share.get_file_urls',
                args: {
                    delivery_notes: selected_docs.map(doc => doc.name)
                },
                callback: function(response) {
                    if (response.message.success) {
                        const messages = response.message.urls.map(item => {
                            return `• ${item.delivery_note}: ${item.url}`;
                        }).join('\n');

                        const full_message = encodeURIComponent(
                            `Hello,\nPlease find the Delivery Notes below:\n${messages}`
                        );

                        frappe.prompt(
                            {
                                fieldname: 'phone',
                                label: 'Enter WhatsApp Number',
                                fieldtype: 'Data',
                                reqd: 1,
                                default: '917801988820',
                                description: 'Use format like 919999999999 (No + or spaces)'
                            },
                            function (values) {
                                const phone = values.phone;
                                const whatsapp_url = `https://wa.me/${phone}?text=${full_message}`;
                                window.open(whatsapp_url, '_blank');
                            },
                            'Send WhatsApp Message',
                            'Send'
                        );
                    } else {
                        frappe.msgprint(__('Error generating URLs: ') + response.message.error);
                    }
                }
            });
        });
    }
};

// Prevent duplicate Send Email button
frappe.listview_settings['Delivery Note'].sendEmailAdded = true;
