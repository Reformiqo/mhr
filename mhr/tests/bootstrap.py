import frappe
from frappe.utils import getdate, nowdate


def before_tests():
	"""Bootstrap a fully set-up India company before the test suite runs.

	Running `bench run-tests` against a freshly installed ERPNext site does not
	complete the Setup Wizard, so base records the test factories rely on
	(Warehouse Types like "Transit", default accounts) do not exist yet.
	Completing setup here creates those base records.

	erpnext's own test records (e.g. Item opening stock) submit stock entries
	dated *today* against `_Test Company`, which needs an active Fiscal Year
	covering today. mhr runs on an Indian fiscal year (Apr-Mar), so create a
	company-independent Fiscal Year for the current Apr-Mar year — global (no
	company link) so it also applies to erpnext's `_Test Company`.
	"""
	frappe.clear_cache()

	today = getdate(nowdate())
	# Indian fiscal year (Apr-Mar) containing today
	fy_start_year = today.year if today.month >= 4 else today.year - 1
	fy_start = f"{fy_start_year}-04-01"
	fy_end = f"{fy_start_year + 1}-03-31"

	if not frappe.db.a_row_exists("Company"):
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

		setup_complete(
			{
				"currency": "INR",
				"full_name": "Test Admin",
				"company_name": "Meher Test",
				"company_abbr": "MT",
				"timezone": "Asia/Kolkata",
				"country": "India",
				"chart_of_accounts": "Standard",
				"fy_start_date": fy_start,
				"fy_end_date": fy_end,
				"language": "english",
				"company_tagline": "Test",
				"email": "test@example.com",
				"password": "test",
			}
		)

	_ensure_global_fiscal_year(fy_start, fy_end)
	frappe.db.commit()


def _ensure_global_fiscal_year(fy_start: str, fy_end: str):
	"""Create a company-independent Fiscal Year covering today, if missing."""
	if frappe.db.exists("Fiscal Year", {"year_start_date": fy_start}):
		return

	frappe.get_doc(
		{
			"doctype": "Fiscal Year",
			"year": f"{fy_start[:4]}-{int(fy_start[:4]) + 1}",
			"year_start_date": fy_start,
			"year_end_date": fy_end,
		}
	).insert(ignore_if_duplicate=True, ignore_permissions=True)
