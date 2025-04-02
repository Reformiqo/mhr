/**
 * Checks if a batch is already used in any Delivery Note Item
 * @param {string} batchNo - The batch number to check
 * @returns {Promise} - Promise that resolves with the result of the check
 */
function check_batch_already_used_in_delivery_note(batchNo) {
    return new Promise((resolve, reject) => {
        frappe.call({
            method: "mhr.utilis.check_batch_already_used_in_delivery_note",
            args: {
                batch_no: batchNo
            },
            callback: function(r) {
                if (r.message && r.message.used) {
                    // Batch is already used
                    frappe.msgprint({
                        title: __("Batch Already Used"),
                        indicator: "red",
                        message: __(`The batch <strong>${batchNo}</strong> is already used in delivery note <strong>${r.message.delivery_note}</strong>. Please select a different batch.`)
                    });
                    resolve(true); // Batch is used
                } else {
                    // Batch is not used
                    resolve(false); // Batch is not used
                }
            },
            error: function(err) {
                console.error("Error checking batch usage:", err);
                reject(err);
            }
        });
    });
}
