# Copyright (c) 2024, reformiqo and contributors
# For license information, please see license.txt

# import frappe


import frappe
from frappe import _
from frappe.utils import cint


def execute(filters=None):
	data_col = [

	]
	columns, data = get_columns(filters=filters), get_datas(filters=filters)
	return columns, data



def get_columns(filters=None):
    return [
  		{
			"label": _("Date"),
        		"fieldname": "date",
          		"fieldtype": "Date",
	           	"width": 150
	        },
  		{
			"label": _("Container"),
	        	"fieldname": "container",
	          	"fieldtype": "Data",
	           	"width": 150
	        },
			{
			"label": _("Product Name"),
	        	"fieldname": "item",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			# //pulp
			{
			"label": _("Pulp"),
	        	"fieldname": "pulp",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			#lusture
			{
			"label": _("Lusture"),
	        	"fieldname": "lusture",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			#glue
			{
			"label": _("Glue"),
	        	"fieldname": "glue",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			#total opening
			{
			"label": _("Total Opening"),
	        	"fieldname": "total_opening",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			#total closing
			{
			"label": _("Total Closing"),
	        	"fieldname": "total_closing",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			#mer no
			{
			"label": _("Mer No"),
	        	"fieldname": "mer_no",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			#lot no
			{
			"label": _("Lot No"),
	        	"fieldname": "lot_no",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			#cone
			{
			"label": _("Cone"),
	        	"fieldname": "cone",
	          	"fieldtype": "Data",
	           	"width": 100
			},
			#stock
			{
			"label": _("Stock"),
	        	"fieldname": "stock",
	          	"fieldtype": "Data",
	           	"width": 100
			},
  		
  		
	]


def get_datas(filters=None):
    
	containers =  frappe.get_all("Container", fields=["*"])
	data = []
	for container in containers:
		for cone in get_multiple_variable_of_cone(container.name):
			data.append({
				"date": container.posting_date if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"container": container.name if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"item": container.item if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"pulp": container.pulp if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"lusture": container.lusture if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"glue": container.glue if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"total_opening": container.total_opening if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"total_closing": container.total_closing if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"mer_no": container.merge_no if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"lot_no": container.lot_no if cone == get_multiple_variable_of_cone(container.name)[0] else "",
				"cone": cone,
				"stock": get_cone_total(container.name, cone)
			})
		# data.append({
		# 	"date": container.posting_date,
		# 	"container": container.name,
		# 	"item": container.item,
		# 	"pulp": container.pulp,
		# 	"lusture": container.lusture,
		# 	"glue": container.glue,
		# 	"total_opening": container.total_opening,
		# 	"total_closing": container.total_closing,
		# 	"mer_no": container.merge_no if container.merge_no else "",
		# 	"lot_no": container.lot_no,
		# 	"cone": calculate_cone_total(container.name)[0]["cone"],

		# })
	return data
def get_multiple_variable_of_cone(container):
	con = frappe.get_doc("Container", container)
	cone = []
	#if cone is 6 for in a batch of container, record six and skip next if the same cone
	for batch in con.batches:
		if batch.cone not in cone:
			cone.append(batch.cone)
	return cone

def get_cone_total(container, cone):
	con = frappe.get_doc("Container", container)
	total = 0
	for batch in con.batches:
		if batch.cone == cone:
			total += cint(batch.cone)
	return total
def calculate_cone_total(container):
	cone = get_multiple_variable_of_cone(container)
	cone_total = []
	for c in cone:
		cone_total.append({
			"cone": c,
			"stock": get_cone_total(container, c)
		})
	return cone_total