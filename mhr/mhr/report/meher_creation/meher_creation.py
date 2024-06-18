# Copyright (c) 2024, reformiqo and contributors
# For license information, please see license.txt

# import frappe


import frappe
from frappe import _


def execute(filters=None):
	columns, data = get_columns(filters=filters), get_datas(filters=filters)
	return columns, data



def get_columns(filters=None):
    return [
  		{
			"label": _("Date"),
        		"fieldname": "date",
          		"fieldtype": "Date",
	           	"width": 100
	        },
  		{
			"label": _("Container"),
	        	"fieldname": "container",
	          	"fieldtype": "Link",
	           	"width": 150
	        },
  		{
			"label": _("Log Type"),
	        	"fieldname": "log_type",
	          	"fieldtype": "Data",
	           	"width": 100,
			"align": 'center',
			"dropdown": False
	        },
  		{
			"label": _("Time"),
	        	"fieldname": "time",
	          	"fieldtype": "Datetime",
	           	"width": 200
	        },
  		{
			"label": _("Auto Attenadnce"),
	        	"fieldname": "auto_attendance",
	          	"fieldtype": "Check",
	           	"width": 150
	        },
	]


def get_datas(filters=None):
    
	data = frappe.get_all(
		'Employee Checkin',
		filters={
			'employee': filters.employee
		},
		fields=['employee', 'employee_name', 'log_type', 'time', 'skip_auto_attendance']
	)

	return data