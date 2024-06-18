frappe.ui.form.on('Purchase Receipt', {
    refresh(frm) {
        
    },
    //get batche if lot no is filled
    custom_container_no: function(frm) {
        itemShow = []
            frappe.call({
                method: "mhr.utilis.get_lot_nos",
                args: {
                    "container_no": frm.doc.custom_container_no
                },
                callback: function(r) {
                    if(r.message){
                        //set the  th eoptions fo rlot not
                        frm.set_df_property('custom_lot_number', 'options', [r.message]);       
                    }
                }
            });
        
    },
    //get the lot no if container no is filled
    custom_lot_number: function(frm) {
            get_batches(frm);
            get_total_batches(frm);
        
    },
    //get batches
    get_batches: function(frm){
        frappe.call({
            method: "mhr.utilis.get_batches",
            args: {
                "container_no": frm.doc.custom_container_no,
                "lot_no": frm.doc.custom_lot_number
            },
            callback: function(r) {
                if(r.message){
                  //append the items to the table
                    console.log(r.message);
                    frm.doc.items = []
                    $.each(r.message, function(i, d) {
                        var item = {
                            item_code: d.item,
                            item_name: d.item,
                            qty: d.batch_qty,
                            rate: d.rate,
                            amount: d.amount,
                            uom: d.stock_uom,
                            batch_no: d.batch_no,
                            rate:200,
                            base_rate:200,
                            price_list_rate:200,
                            amount:400,
                            base_price_list_rate:200,
                            base_amount:400,
                            use_serial_batch_fields:1,
                            batch_no: d.name,
                            warehouse: "Finished Goods - MC",

                            
                        }
                        frm.add_child("items", item);
                    });
                    frm.refresh_field("items");
            }
        }
        
        });
    },
    get_total_batches: function(frm) {
        frappe.call({
            method: "mhr.utilis.get_total_batches",
            args: {
                "container_no": frm.doc.custom_container_no,
                "lot_no": frm.doc.custom_lot_number
            },
            callback: function(r) {
                if(r.message){
                    frm.set_value("custom_total_batches", r.message);
                }
            }
        });
    }    
  
});
