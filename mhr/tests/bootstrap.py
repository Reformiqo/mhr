import frappe


def before_tests():
	"""Bootstrap a fully set-up India company before the test suite runs.

	Running `bench run-tests` against a freshly installed ERPNext site does not
	complete the Setup Wizard, so base records the test factories rely on
	(Warehouse Types like "Transit", Fiscal Year, default accounts) do not
	exist yet — erpnext's own Company test record then fails creating its
	default warehouses. Completing setup here creates those base records.

	mhr runs on an Indian fiscal year (Apr-Mar); set the company up the same
	way so transactional test records fall inside the active fiscal year.
	"""
	frappe.clear_cache()

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
				"fy_start_date": "2025-04-01",
				"fy_end_date": "2026-03-31",
				"language": "english",
				"company_tagline": "Test",
				"email": "test@example.com",
				"password": "test",
			}
		)

	frappe.db.commit()
