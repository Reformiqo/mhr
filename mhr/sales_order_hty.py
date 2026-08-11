"""MI1-I90 — Sales Order HTY mode (server side).

Ports the Delivery Note HTY behaviour onto Sales Order. Everything here
is a no-op unless `transaction_type == 'HTY'`, so the existing VFY Sales
Order flow — and Delivery Note in every mode — is untouched.

The heavy lifting (lot / container / batch lookups) is reused from
`mhr.utilis` rather than duplicated; this module only adds the Sales
Order-specific field mapping and the validate-time guards.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from mhr.utilis import (
	get_container_batches_with_stock,
	get_hty_batches_for_containers,
)

HTY = "HTY"
HTY_SO_SERIES = "HTY-SO-.YYYY.-"


# ---------------------------------------------------------------------------
# validate hook
# ---------------------------------------------------------------------------


def validate_hty_sales_order(doc, method=None):
	"""Sales Order validate hook (HTY-aware).

	Mirrors `validate_hty_stock_entry` / `validate_hty_delivery_trip`, plus
	the two server-side guards the Delivery Note flow never got:

	  1. naming series — an HTY Sales Order always lands on the HTY series,
	     even when it was created via API / import / duplicate, where the
	     client-side auto-switch never fires.
	  2. batch mode consistency — an HTY Sales Order may only reference HTY
	     batches. The client filters the dropdown, but nothing stopped an
	     API caller from mixing modes until now.

	Also recomputes `custom_total_cone` so the header total is authoritative
	rather than whatever the browser last wrote.
	"""
	if getattr(doc, "docstatus", 0) != 0:
		return
	if (getattr(doc, "transaction_type", None) or "VFY") != HTY:
		return

	series = getattr(doc, "naming_series", "") or ""
	if not series.startswith("HTY-"):
		doc.naming_series = HTY_SO_SERIES

	_validate_batch_transaction_type(doc)
	_set_total_cone(doc)


def _validate_batch_transaction_type(doc):
	"""Refuse VFY batches on an HTY Sales Order.

	Batch.custom_transaction_type is maintained by
	`set_batch_transaction_type_from_container`; every Batch row on the site
	carries a value, so an empty result means the batch itself is missing.
	"""
	batch_nos = [
		row.custom_batch_no
		for row in (getattr(doc, "items", None) or [])
		if getattr(row, "custom_batch_no", None)
	]
	if not batch_nos:
		return

	rows = frappe.get_all(
		"Batch",
		filters={"name": ["in", list(set(batch_nos))]},
		fields=["name", "custom_transaction_type"],
	)
	by_name = {r["name"]: (r["custom_transaction_type"] or "") for r in rows}

	wrong = sorted({b for b in batch_nos if by_name.get(b, HTY) != HTY})
	if wrong:
		frappe.throw(
			_("These batches are not HTY batches and cannot be used on an HTY Sales Order: {0}").format(
				", ".join(wrong)
			),
			title=_("Batch / Transaction Type mismatch"),
		)


def _set_total_cone(doc):
	doc.custom_total_cone = sum(
		cint(getattr(row, "custom_cone", 0) or 0)
		for row in (getattr(doc, "items", None) or [])
	)


# ---------------------------------------------------------------------------
# whitelisted endpoints used by public/js/sales_order_hty.js
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_company_hty_defaults(company, customer=None):
	"""Defaults to prefill on an HTY Sales Order when Company changes.

	MI1-I90 P2 (2026-08-11): the superseded MI1-I39 Client Script asked for
	`Company.default_price_list`, which does not exist — ERPNext does not
	scope Price Lists by Company — so frappe.client.get_value rejected the
	whole query with "Field not permitted in query: default_price_list".
	That line had never run before, because no Sales Order had ever been set
	to HTY.

	Cost Center genuinely IS a Company field. The selling price list is
	resolved through ERPNext's real chain instead:

	  1. Company.custom_default_price_list  (only if Meher adds that field)
	  2. Customer -> Customer Group default (stock ERPNext behaviour)
	  3. Selling Settings.selling_price_list (system default)

	Every read is guarded so this endpoint can never raise the way the
	client-side query did.
	"""
	frappe.has_permission("Sales Order", "write", throw=True)
	if not company:
		return {}

	out = {"cost_center": frappe.db.get_value("Company", company, "cost_center")}

	# Optional per-company override — only queried if the column exists.
	if frappe.db.has_column("Company", "custom_default_price_list"):
		out["selling_price_list"] = frappe.db.get_value(
			"Company", company, "custom_default_price_list"
		)

	if not out.get("selling_price_list") and customer:
		out["selling_price_list"] = frappe.db.get_value(
			"Customer", customer, "default_price_list"
		)
		if not out["selling_price_list"]:
			group = frappe.db.get_value("Customer", customer, "customer_group")
			if group:
				out["selling_price_list"] = frappe.db.get_value(
					"Customer Group", group, "default_price_list"
				)

	if not out.get("selling_price_list"):
		out["selling_price_list"] = frappe.db.get_single_value(
			"Selling Settings", "selling_price_list"
		)

	return out


@frappe.whitelist()
def get_so_rows_for_containers(container_names):
	"""4-step lot picker, step 4: turn the selected Containers into rows
	ready for the Sales Order items table.

	`mhr.utilis.get_hty_batches_for_containers` already produces this payload
	keyed for Delivery Note Item. Sales Order Item spells three of those
	fields differently, so remap rather than duplicate the query:

	    DN `batch_no`            -> SO `custom_batch_no`
	    DN `custom_container_no` -> SO `custom_container_number`
	    DN `custom_lot_no`       -> SO `custom_lot_number`

	`use_serial_batch_fields` is intentionally absent: Sales Order does not
	post to the stock ledger, so it has no Serial and Batch Bundle.
	"""
	frappe.has_permission("Sales Order", "write", throw=True)

	if isinstance(container_names, str):
		try:
			container_names = json.loads(container_names)
		except (ValueError, TypeError):
			container_names = [container_names]
	if not container_names:
		return []

	rows = get_hty_batches_for_containers(container_names)

	payload = []
	for r in rows:
		payload.append(
			{
				"item_code": r.get("item_code"),
				"qty": flt(r.get("qty")),
				"warehouse": r.get("warehouse"),
				"custom_batch_no": r.get("batch_no"),
				"custom_container_number": r.get("custom_container_no"),
				"custom_lot_number": r.get("custom_lot_no"),
				"custom_cone": cint(r.get("custom_cone")),
				"custom_sr_no": r.get("custom_sr_no") or "",
				"custom_gross_weight": flt(r.get("custom_gross_weight")),
				"custom_supplier_batch_no": r.get("custom_supplier_batch_no") or "",
			}
		)
	return payload


@frappe.whitelist()
def get_hty_batches_for_container_no(container_no):
	"""Stock-aware batch list for the 'Select Batch' popup.

	Thin pass-through to the Delivery Note helper so both forms show the
	same, already-clamped balances (MI1-I71). Kept as its own endpoint so
	the Sales Order client never has to know which module the query lives
	in, and so permissions are checked against Sales Order.
	"""
	frappe.has_permission("Sales Order", "write", throw=True)
	return get_container_batches_with_stock(container_no)


@frappe.whitelist()
def get_container_spec_for_batch(batch_no):
	"""Resolve the Product / Type / Colour spec for a batch.

	The Delivery Note client looks this up with
	`get_list('Container', {container_no: ..., transaction_type: 'HTY'}, limit=1)`.
	That is ambiguous: `Batch.custom_container_no` stores the container
	NUMBER, and one number maps to many Container documents (e.g. MCZH-34
	has 18), which disagree with each other — so the DN header picks up
	whichever row the default ordering happens to surface.

	Here we go through the Batch Items child row instead, which points at
	exactly one Container document, and fall back to the container-number
	lookup only when the batch predates that link.
	"""
	frappe.has_permission("Sales Order", "write", throw=True)

	if not batch_no:
		return {}

	fields = [
		"name", "product", "type", "colour", "glue", "pulp", "lusture",
		"grade", "fsc", "merge_no", "cross_section", "notes",
	]

	container = frappe.db.get_value(
		"Batch Items",
		{"batch_id": batch_no, "parenttype": "Container"},
		"parent",
	)
	if container:
		spec = frappe.db.get_value("Container", container, fields, as_dict=True)
		if spec:
			return _clean_spec(spec)

	# Fallback: no Batch Items link (legacy rows). Narrow by container
	# number + HTY and take the most recent submitted Container.
	container_no = frappe.db.get_value("Batch", batch_no, "custom_container_no")
	if not container_no:
		return {}

	rows = frappe.get_all(
		"Container",
		filters={
			"container_no": container_no,
			"transaction_type": HTY,
			"docstatus": 1,
		},
		fields=fields,
		order_by="posting_date desc, creation desc",
		limit=1,
	)
	return _clean_spec(rows[0]) if rows else {}


def _strip_label_prefix(val):
	"""Drop the `Label-` prefix from an Item Specification docname.

	Deliberately splits on the FIRST hyphen, matching the popup's
	`so_hty_strip_label_prefix()` in sales_order_hty.js — not
	`mhr.utilis.strip_prefix`, which splits on the LAST one. The two
	disagree for multi-hyphen values (`Colour-OFF-WHITE` -> `OFF-WHITE`
	here, `WHITE` there), and the header must store exactly what the popup
	showed.
	"""
	if not val:
		return ""
	s = str(val)
	idx = s.find("-")
	return s[idx + 1:] if idx >= 0 else s


def _clean_spec(spec):
	"""Container spec fields are Links into Item Specification, whose names
	carry a `Label-` prefix (e.g. `Product-HTY`). The batch popup already
	strips that for display; strip it here too so the Sales Order header
	stores `HTY`, not `Product-HTY`, and the two never disagree."""
	prefixed = ("product", "type", "colour", "glue", "pulp", "lusture", "grade", "fsc")
	out = {}
	for key, value in (spec or {}).items():
		if key == "name":
			out["container"] = value
			continue
		out[key] = _strip_label_prefix(value) if key in prefixed else value
	return out
