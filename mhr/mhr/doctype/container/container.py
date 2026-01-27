# Copyright (c) 2024, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, now


class Container(Document):
    # def after_insert(self):
    # 	if frappe.db.exists("Container", {"container_no": self.container_no, "name" : ["!=", self.name]}):
    # 		doc = frappe.get_doc("Container", {"container_no": self.container_no, "name": ["!=", self.name]})
    # 		for batch in doc.batches:
    # 			self.append("batches", {
    # 				"batch_id": batch.batch_id,
    # 				"item": batch.item,
    # 				"qty": batch.qty,
    # 				"uom": batch.uom,
    # 				"cone": batch.cone,
    # 				"warehouse": batch.warehouse,
    # 				"supplier_batch_no": batch.supplier_batch_no
    # 			})
    # 			self.save()
    # 			frappe.db.commit()
    # 		frappe.delete_doc("Container", doc.name)
    # 		frappe.db.commit()

    def on_submit(self):
        # frappe.msgprint("on_submit"
        self.create_batches()
        self.create_purchase_receipt()

    def enqueue_create_batches(self):
        # frappe.msgprint("enqueue_create_batches")
        frappe.enqueue(
            "mhr.utilis.create_batches",
            container=self.name,
            queue="long",
            timeout=1500000000,
        )
        frappe.db.commit()

    def on_cancel(self):
        # First check if any batches have been consumed (have outward transactions)
        consumed_batches = self.get_consumed_batches()
        if consumed_batches:
            batch_list = ", ".join(consumed_batches[:5])  # Show first 5
            if len(consumed_batches) > 5:
                batch_list += f" and {len(consumed_batches) - 5} more"

            # Get Delivery Notes that used these batches
            delivery_notes = self.get_delivery_notes_for_batches(consumed_batches)
            dn_list = ", ".join(delivery_notes[:5]) if delivery_notes else "Unknown"
            if len(delivery_notes) > 5:
                dn_list += f" and {len(delivery_notes) - 5} more"

            frappe.throw(
                f"Cannot cancel Container {self.name} because stock from the following batches "
                f"has already been consumed: {batch_list}.<br><br>"
                f"Please cancel the following Delivery Notes first: {dn_list}"
            )

        # Get all Purchase Receipts linked to this container
        purchase_receipts = frappe.get_all(
            "Purchase Receipt",
            filters={"custom_container_no": self.name, "docstatus": 1},
            fields=["name"],
        )

        for pr in purchase_receipts:
            try:
                pr_doc = frappe.get_doc("Purchase Receipt", pr.name)

                # Get all Serial and Batch Bundles from this PR before cancelling
                bundle_names = []
                for item in pr_doc.items:
                    if item.serial_and_batch_bundle:
                        bundle_names.append(item.serial_and_batch_bundle)

                # Cancel the Purchase Receipt
                pr_doc.cancel()
                frappe.db.commit()

                # Cancel the Serial and Batch Bundles
                for bundle_name in bundle_names:
                    if frappe.db.exists("Serial and Batch Bundle", bundle_name):
                        frappe.db.set_value(
                            "Serial and Batch Bundle",
                            bundle_name,
                            "is_cancelled",
                            1
                        )

                frappe.db.commit()

            except Exception as e:
                frappe.log_error(
                    f"Failed to cancel PR {pr.name} for container {self.name}: {str(e)}",
                    "Container Cancel"
                )
                frappe.throw(
                    f"Failed to cancel Purchase Receipt {pr.name}: {str(e)}"
                )

        # Delete all batches linked to this container
        for batch in self.batches:
            if batch.batch_id and frappe.db.exists("Batch", batch.batch_id):
                frappe.db.sql("DELETE FROM `tabBatch` WHERE name = %s", batch.batch_id)

        frappe.db.commit()

    def get_consumed_batches(self):
        """Check which batches from this container have outward transactions (stock consumed)"""
        consumed = []
        for batch in self.batches:
            if not batch.batch_id:
                continue
            # Check if there are any outward Serial and Batch Entries for this batch
            outward_qty = frappe.db.sql("""
                SELECT COALESCE(SUM(ABS(sbe.qty)), 0) as qty
                FROM `tabSerial and Batch Entry` sbe
                INNER JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
                WHERE sbe.batch_no = %s
                AND sbb.type_of_transaction = 'Outward'
                AND sbb.docstatus = 1
                AND sbb.is_cancelled = 0
            """, (batch.batch_id,), as_dict=True)

            if outward_qty and outward_qty[0].qty > 0:
                consumed.append(batch.batch_id)

        return consumed

    def get_delivery_notes_for_batches(self, batch_names):
        """Get Delivery Notes that consumed stock from the given batches"""
        if not batch_names:
            return []

        placeholders = ", ".join(["%s"] * len(batch_names))
        delivery_notes = frappe.db.sql(f"""
            SELECT DISTINCT sbb.voucher_no
            FROM `tabSerial and Batch Entry` sbe
            INNER JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
            WHERE sbe.batch_no IN ({placeholders})
            AND sbb.voucher_type = 'Delivery Note'
            AND sbb.type_of_transaction = 'Outward'
            AND sbb.docstatus = 1
            AND sbb.is_cancelled = 0
        """, tuple(batch_names), as_dict=True)

        return [dn.voucher_no for dn in delivery_notes if dn.voucher_no]

    def on_trash(self):
        for batch in self.batches:
            if frappe.db.exists(
                "Batch",
                {
                    "name": batch.batch_id,
                    "custom_container_no": self.container_no,
                    "custom_lot_no": self.lot_no,
                },
            ):
                frappe.db.sql(
                    "DELETE FROM `tabBatch` WHERE name = %s AND custom_container_no = %s AND custom_lot_no = %s",
                    (batch.batch_id, self.container_no, self.lot_no),
                )
        pr = frappe.get_all(
            "Purchase Receipt",
            filters={"custom_container_no": self.name},
            fields=["name"],
        )
        if pr:
            for p in pr:
                doc = frappe.get_doc("Purchase Receipt", p.name)
                for item in doc.items:
                    if item.serial_and_batch_bundle:
                        frappe.db.sql(
                            "DELETE FROM `tabSerial and Batch Bundle` WHERE name = %s",
                            item.serial_and_batch_bundle,
                        )
                frappe.db.sql(
                    "DELETE FROM `tabPurchase Receipt` WHERE name = %s", p.name
                )
        frappe.db.commit()

    def validate(self):

        qty = 0
        cone = 0
        for batch in self.batches:
            qty += float(batch.qty)
            cone += cint(batch.cone)
        self.total_batches = len(self.batches)
        self.total_net_weight = qty
        self.total_cone = cone

    def create_batches(self):
        # frappe.msgprint("create_batches")
        # Validate all batches first
        for batch in self.batches:
            if not batch.item:
                frappe.throw(
                    f"Item is mandatory for Batch at row {batch.idx}"
                )
            if not batch.batch_id:
                frappe.throw(
                    f"Batch ID is mandatory for Batch at row {batch.idx}"
                )
            if frappe.db.exists(
                "Batch",
                {
                    "name": batch.batch_id,
                    "custom_container_no": self.container_no,
                    "custom_lot_no": self.lot_no,
                },
            ):
                frappe.throw(
                    f"Batch {batch.batch_id} of row {batch.idx} already exists in the system"
                )

        # Create batches after validation passes
        for batch in self.batches:
            batch_doc = frappe.new_doc("Batch")
            batch_doc.item = batch.item
            batch_doc.stock_uom = batch.uom
            batch_doc.batch_id = batch.batch_id
            batch_doc.custom_supplier_batch_no = batch.supplier_batch_no
            batch_doc.custom_container_no = self.container_no
            batch_doc.custom_cone = batch.cone
            batch_doc.custom_glue = self.glue
            batch_doc.custom_lusture = self.lusture
            batch_doc.custom_grade = self.grade
            batch_doc.custom_pulp = self.pulp
            batch_doc.custom_fsc = self.fsc
            batch_doc.cross_section = self.cross_section
            batch_doc.custom_merge_no = self.merge_no
            batch_doc.custom_warehouse = self.warehouse
            # batch.custom_net_weight = batch.qty
            batch_doc.custom_lot_no = self.lot_no
            batch_doc.save(ignore_permissions=True)
            batch_doc.submit()
            frappe.db.commit()

    def get_items(self):
        # Fetch the last created Container document

        items = []

        # Iterate over the batches in the Container document
        for batch in self.batches:
            # Skip batches with empty item or batch_id
            if not batch.item or not batch.batch_id:
                continue
            # Check if the item is already in the list
            existing_item = next(
                (item for item in items if item["item"] == batch.item), None
            )
            if existing_item:
                # If the item exists, increase the batch_qty
                existing_item["batch_qty"] += float(batch.qty)
            else:
                # If the item does not exist, add it to the list
                items.append(
                    {
                        "item": batch.item,
                        "batch_qty": float(batch.qty),
                        "stock_uom": batch.uom,
                        "name": batch.batch_id,
                    }
                )
        return items

    def get_item_batches(self, item_code, check_exists=False):
        batches = []
        for batch in self.batches:
            # Skip batches with empty batch_id
            if not batch.batch_id:
                continue
            # Optionally check if batch exists in system (for returns)
            if check_exists and not frappe.db.exists("Batch", batch.batch_id):
                continue
            if batch.item == item_code:
                batches.append(
                    {
                        "batch_id": batch.batch_id,
                        "qty": float(batch.qty),
                        "uom": batch.uom,
                        "cone": batch.cone,
                        "supplier_batch_no": batch.supplier_batch_no,
                        "warehouse": "Finished Goods - MC",
                    }
                )
        return batches

    def create_serial_and_batch_bundle(self, item_code, transaction_type):
        try:
            # Get item's serial and batch settings
            item_doc = frappe.get_cached_doc("Item", item_code)
            has_serial_no = item_doc.has_serial_no
            has_batch_no = item_doc.has_batch_no

            # Handle items that require serial numbers
            if has_serial_no:
                if transaction_type == "Inward":
                    # For regular submit, throw error - serial numbers not supported
                    frappe.throw(
                        f"Item {item_code} requires serial numbers which is not supported in container flow. "
                        "Please disable serial number tracking for this item or remove it from the container."
                    )
                else:
                    # For returns, just skip
                    return None

            # Skip if item doesn't use batch tracking
            if not has_batch_no:
                if transaction_type == "Inward":
                    frappe.throw(
                        f"Item {item_code} does not have batch tracking enabled. "
                        "Please enable batch tracking for this item."
                    )
                return None

            # For Outward transactions (returns), only include batches that exist
            check_exists = transaction_type == "Outward"
            batches = self.get_item_batches(item_code, check_exists=check_exists)
            if not batches:
                return None

            sb_bundle = frappe.new_doc("Serial and Batch Bundle")
            sb_bundle.company = "Meher Creations"
            sb_bundle.type_of_transaction = transaction_type
            sb_bundle.has_batch_no = 1
            sb_bundle.has_serial_no = 0
            sb_bundle.item_code = item_code
            sb_bundle.item_name = item_code
            sb_bundle.voucher_type = "Purchase Receipt"
            sb_bundle.warehouse = "Finished Goods - MC"

            for batch in batches:
                sb_bundle.append(
                    "entries",
                    {
                        "batch_no": batch["batch_id"],
                        "qty": batch["qty"],
                        "uom": batch["uom"],
                        "cone": batch["cone"],
                        "supplier_batch_no": batch["supplier_batch_no"],
                        "warehouse": "Finished Goods - MC",
                    },
                )

            sb_bundle.save()
            frappe.db.commit()
            return sb_bundle.name
        except Exception as e:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), "create_serial_and_batch_bundle")
            return {
                "message": "Failed to create Serial and Batch Bundle",
                "error": str(e),
            }

    def create_purchase_receipt(self, is_return=0, pr=None):
        items = self.get_items()

        # Store original batch quantities before Purchase Receipt submission
        # This is needed because update_batch_qty() will recalculate from SLE and may double the value
        batch_qty_map = {}
        for batch in self.batches:
            if frappe.db.exists("Batch", batch.batch_id):
                batch_qty_map[batch.batch_id] = flt(batch.qty)

        # Create a new Purchase Receipt document
        purchase_receipt = frappe.new_doc("Purchase Receipt")
        purchase_receipt.supplier = self.supplier
        purchase_receipt.posting_date = self.posting_date
        purchase_receipt.custom_container_no = self.name
        purchase_receipt.custom_total_batches = len(self.batches)
        purchase_receipt.custom_lot_number = self.lot_no
        purchase_receipt.custom_lusture = self.lusture
        purchase_receipt.custom_glue = self.glue
        purchase_receipt.custom_grade = self.grade
        purchase_receipt.custom_pulp = self.pulp
        purchase_receipt.custom_fsc = self.fsc
        purchase_receipt.custom_merge_no = self.merge_no
        purchase_receipt.items = []

        # Add items to the Purchase Receipt
        if is_return == 1:
            purchase_receipt.is_return = 1
            purchase_receipt.return_against = pr
            for item in items:
                serial_and_batch_bundle = self.create_serial_and_batch_bundle(
                    item["item"], "Outward"
                )
                # Check if create_serial_and_batch_bundle returned an error dict
                if isinstance(serial_and_batch_bundle, dict):
                    frappe.throw(
                        f"Failed to create Serial and Batch Bundle for item {item['item']}: {serial_and_batch_bundle.get('error', 'Unknown error')}"
                    )
                # Skip items that returned None (serial number items or non-batch items)
                if serial_and_batch_bundle is None:
                    continue
                purchase_receipt.append(
                    "items",
                    {
                        "item_code": item["item"],
                        "item_name": item["item"],
                        "qty": -(flt(item["batch_qty"])),
                        "stock_uom": item["stock_uom"],
                        "warehouse": "Finished Goods - MC",
                        "allow_zero_valuation_rate": 1,
                        "rate": 100,
                        "price_list_rate": 100,
                        "received_qty": -(flt(item["batch_qty"])),
                        "conversion_factor": 1,
                        "use_serial_batch_fields": 0,
                        "serial_and_batch_bundle": serial_and_batch_bundle,
                    },
                )
        else:
            for item in items:
                serial_and_batch_bundle = self.create_serial_and_batch_bundle(
                    item["item"], "Inward"
                )
                # Check if create_serial_and_batch_bundle returned an error dict
                if isinstance(serial_and_batch_bundle, dict):
                    frappe.throw(
                        f"Failed to create Serial and Batch Bundle for item {item['item']}: {serial_and_batch_bundle.get('error', 'Unknown error')}"
                    )
                # Skip items that returned None (serial number items or non-batch items)
                if serial_and_batch_bundle is None:
                    continue
                purchase_receipt.append(
                    "items",
                    {
                        "item_code": item["item"],
                        "item_name": item["item"],
                        "qty": item["batch_qty"],
                        "stock_uom": item["stock_uom"],
                        "warehouse": "Finished Goods - MC",
                        "allow_zero_valuation_rate": 1,
                        "rate": 100,
                        "price_list_rate": 100,
                        "received_qty": item["batch_qty"],
                        "conversion_factor": 1,
                        "use_serial_batch_fields": 0,
                        "serial_and_batch_bundle": serial_and_batch_bundle,
                    },
                )

        # Save and submit the Purchase Receipt
        try:
            # Check if no items were added
            if not purchase_receipt.items:
                if is_return:
                    # For returns, just skip silently
                    return None
                else:
                    # For regular submit, throw error
                    frappe.throw(
                        f"No valid items for Purchase Receipt from Container {self.name}. "
                        "Please ensure all batches have valid Item and Batch ID."
                    )

            purchase_receipt.flags.ignore_mandatory = True
            purchase_receipt.save()
            purchase_receipt.submit()
            frappe.db.commit()

            # After Purchase Receipt submission, update_batch_qty() may have recalculated
            # batch_qty incorrectly. Restore the correct batch_qty from container batches
            self.correct_batch_qty_after_pr_submit(batch_qty_map)

            return purchase_receipt.name

        except Exception as e:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), "create_purchase_receipt")
            frappe.msgprint(
                {"message": "Failed to create Purchase Receipt", "error": str(e)}
            )

    def correct_batch_qty_after_pr_submit(self, batch_qty_map):
        """Correct batch_qty after Purchase Receipt submission to prevent duplication"""
        try:
            for batch_id, correct_qty in batch_qty_map.items():
                if frappe.db.exists("Batch", batch_id):
                    frappe.db.set_value("Batch", batch_id, "batch_qty", correct_qty)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(
                frappe.get_traceback(), "correct_batch_qty_after_pr_submit"
            )

    # def autoname(self):
    # 	# frappe.msgprint("autoname")
    # 	self.name = self.container_no

    @frappe.whitelist()
    def resubmit_container(self):
        """
        Resubmit the container - cleans up existing batches/PR and recreates them.
        Used when submit didn't create batches/PR properly.
        """
        # Check if container is submitted
        if self.docstatus != 1:
            frappe.throw("Container must be submitted to use Resubmit")

        # Check if any Delivery Notes have been created against this container's batches
        consumed_batches = self.get_consumed_batches()
        if consumed_batches:
            delivery_notes = self.get_delivery_notes_for_batches(consumed_batches)
            dn_list = ", ".join(delivery_notes[:5]) if delivery_notes else "Unknown"
            if len(delivery_notes) > 5:
                dn_list += f" and {len(delivery_notes) - 5} more"

            frappe.throw(
                f"Cannot resubmit Container {self.name} because Delivery Notes have already been created "
                f"against this container's batches.<br><br>"
                f"Delivery Notes: {dn_list}"
            )

        # Step 1: Cancel and delete existing Purchase Receipts and Serial and Batch Bundles
        purchase_receipts = frappe.get_all(
            "Purchase Receipt",
            filters={"custom_container_no": self.name, "docstatus": ["in", [0, 1]]},
            fields=["name", "docstatus"],
        )

        for pr in purchase_receipts:
            try:
                pr_doc = frappe.get_doc("Purchase Receipt", pr.name)

                # Get all Serial and Batch Bundles from this PR
                bundle_names = []
                for item in pr_doc.items:
                    if item.serial_and_batch_bundle:
                        bundle_names.append(item.serial_and_batch_bundle)

                # Cancel if submitted
                if pr_doc.docstatus == 1:
                    pr_doc.cancel()
                    frappe.db.commit()

                # Delete the PR
                frappe.db.sql("DELETE FROM `tabPurchase Receipt Item` WHERE parent = %s", pr.name)
                frappe.db.sql("DELETE FROM `tabPurchase Receipt` WHERE name = %s", pr.name)

                # Delete the Serial and Batch Bundles
                for bundle_name in bundle_names:
                    if frappe.db.exists("Serial and Batch Bundle", bundle_name):
                        frappe.db.sql("DELETE FROM `tabSerial and Batch Entry` WHERE parent = %s", bundle_name)
                        frappe.db.sql("DELETE FROM `tabSerial and Batch Bundle` WHERE name = %s", bundle_name)

                frappe.db.commit()

            except Exception as e:
                frappe.log_error(
                    f"Failed to cleanup PR {pr.name} for container {self.name}: {str(e)}",
                    "Container Resubmit"
                )
                frappe.throw(f"Failed to cleanup Purchase Receipt {pr.name}: {str(e)}")

        # Step 2: Delete all existing batches for this container
        for batch in self.batches:
            if batch.batch_id and frappe.db.exists("Batch", batch.batch_id):
                frappe.db.sql("DELETE FROM `tabBatch` WHERE name = %s", batch.batch_id)

        frappe.db.commit()

        # Step 3: Recreate batches (same as on_submit)
        self.create_batches()

        # Step 4: Recreate purchase receipt (same as on_submit)
        pr_name = self.create_purchase_receipt()

        frappe.db.commit()

        return {
            "message": f"Container {self.name} resubmitted successfully",
            "purchase_receipt": pr_name
        }
