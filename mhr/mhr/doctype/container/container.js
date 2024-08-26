frappe.ui.form.on("Container", {
    refresh: function(frm) {
        console.log(this)
    },
        qty: function(frm, cdt, cdn) {
        console.log("qty");
        var d = locals[cdt][cdn];}
});

frappe.ui.form.on("Batches", {
    qty: function(frm, cdt, cdn) {
        console.log("Batches qty field changed.");
        var d = locals[cdt][cdn];
        console.log("Current row data:", d);
        var total = 0;
        
        // Ensure frm.doc.batches exists and is an array
        if (frm.doc.batches && Array.isArray(frm.doc.batches)) {
            frm.doc.batches.forEach(function(batch) {
                total += batch.qty || 0; // Add a default value of 0 to handle undefined
            });
        } else {
            console.log("frm.doc.batches is not defined or not an array");
        }

        console.log("Total quantity:", total);
        frm.set_value("total_batches", total);
    }
});
