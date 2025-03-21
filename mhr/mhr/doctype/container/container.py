# Copyright (c) 2024, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt,  now


class Container(Document):
	def on_submit(self):
		# frappe.msgprint("on_submit"
		self.create_batches()
		self.create_purchase_receipt()
		
		
	def enqueue_create_batches(self):
		# frappe.msgprint("enqueue_create_batches")
		frappe.enqueue('mhr.utilis.create_batches', container=self.name, queue='long', timeout=1500000000)
		frappe.db.commit()
		
	def on_cancel(self):
		pr = frappe.get_all("Purchase Receipt", filters={"custom_container_no": self.name}, fields=["name"])
		if pr:
			# create pr return
			for pr_doc in pr:
				self.create_purchase_receipt(is_return=1, pr=pr_doc.name)
				frappe.db.commit()
    # delete all batches
		# for batch in self.batches:
		# 	if frappe.db.exists("Batch", batch.batch_id):
		# 		frappe.db.sql("DELETE FROM `tabBatch` WHERE name = %s", batch.batch_id)
		# 		# delte serial ad abtch budnle
		# pr = frappe.get_all("Purchase Receipt", filters={"custom_container_no": self.name}, fields=["name"])
		# if pr:
		# 	# create pr return
		# 	for pr_doc in pr:
				# self.create_purchase_receipt(is_return=1, pr=pr_doc.name)
				# frappe.db.commit()



	def on_trash(self):
		for batch in self.batches:
			if frappe.db.exists("Batch", {"name": batch.batch_id, "custom_container_no": self.container_no, 'custom_lot_no': self.lot_no}):
				frappe.db.sql("DELETE FROM `tabBatch` WHERE name = %s AND custom_container_no = %s AND custom_lot_no = %s", (batch.batch_id, self.container_no, self.lot_no))
		pr = frappe.get_all("Purchase Receipt", filters={"custom_container_no": self.name}, fields=["name"])
		if pr:
			for p in pr:
				doc  = frappe.get_doc("Purchase Receipt", p.name)
				for item in doc.items:
					if item.serial_and_batch_bundle:
						frappe.db.sql("DELETE FROM `tabSerial and Batch Bundle` WHERE name = %s", item.serial_and_batch_bundle)
				frappe.db.sql("DELETE FROM `tabPurchase Receipt` WHERE name = %s", p.name)
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
		# frappe.msgprint("create_batches"
			for batch in self.batches:
				if frappe.db.exists("Batch", {"name": batch.batch_id, "custom_container_no": self.container_no, 'custom_lot_no': self.lot_no}):
					frappe.throw(f"Batch {batch.batch_id} of row {batch.idx} already exists in the system")
				
				batch_doc = frappe.new_doc("Batch")
				batch_doc.item = batch.item
				batch_doc.batch_qty = batch.qty
				batch_doc.stock_uom = batch.uom
				batch_doc.batch_id = batch.batch_id
				batch_doc.custom_supplier_batch_no= batch.supplier_batch_no
				batch_doc.custom_container_no = self.container_no
				batch_doc.custom_cone = batch.cone
				batch_doc.custom_glue = self.glue
				batch_doc.custom_lusture = self.lusture
				batch_doc.custom_grade = self.grade
				batch_doc.custom_pulp = self.pulp
				batch_doc.custom_fsc = self.fsc
				batch_doc.cross_section = batch.cross_section
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
			# Check if the item is already in the list
			existing_item = next((item for item in items if item["item"] == batch.item), None)
			if existing_item:
				# If the item exists, increase the batch_qty
				existing_item["batch_qty"] += float(batch.qty)
			else:
				# If the item does not exist, add it to the list
				items.append({
					"item": batch.item,
					"batch_qty": float(batch.qty),
					"stock_uom": batch.uom,
					"name": batch.batch_id,
				})
		return items
	def get_item_batches(self, item_code):
		items = self.get_items()
		batches = []
		for item in items:
			for batch in self.batches:
				if item["item"] == item_code:
					batches.append({
					"batch_id": batch.batch_id,
					"qty": float(batch.qty),
					"uom": batch.uom,
					"cone": batch.cone,
					"supplier_batch_no": batch.supplier_batch_no,
					"warehouse": batch.warehouse

					})
		return batches
	def create_serial_and_batch_bundle(self, item_code, transaction_type):
		try:
			batches  = self.get_item_batches(item_code)
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
				sb_bundle.append("entries", {
					"batch_no": batch['batch_id'],
					"qty": batch['qty'],
					"uom": batch['uom'],
					"cone": batch['cone'],
					"supplier_batch_no": batch['supplier_batch_no'],
					"warehouse": batch['warehouse'],

				})
				
			sb_bundle.save()
			frappe.db.commit()
			return sb_bundle.name
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), "create_serial_and_batch_bundle")
			return {"message": "Failed to create Serial and Batch Bundle", "error": str(e)}

	def create_purchase_receipt(self, is_return=0, pr=None):
		items = self.get_items()

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
				serial_and_batch_bundle = self.create_serial_and_batch_bundle(item["item"], "Outward")
				purchase_receipt.append("items", {
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
					"serial_and_batch_bundle": serial_and_batch_bundle
				})
		else:
			for item in items:
				serial_and_batch_bundle = self.create_serial_and_batch_bundle(item["item"], "Inward")
				purchase_receipt.append("items", {
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
					"serial_and_batch_bundle": serial_and_batch_bundle
				})
		

		# Save and submit the Purchase Receipt
		try:
			purchase_receipt.save()
			purchase_receipt.submit()
			frappe.db.commit()
			return purchase_receipt.name
			
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), "create_purchase_receipt")
			frappe.msgprint({"message": "Failed to create Purchase Receipt", "error": str(e)})

	# def autoname(self):
	# 	# frappe.msgprint("autoname")
	# 	self.name = self.container_no