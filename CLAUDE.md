# CLAUDE.md — mhr

## Domain

- **What mhr is:** an ERPNext-based system for **Meher**, a yarn / textile manufacturer. The app extends ERPNext's Stock + Manufacturing modules with the `Container` / `Batch` model, custom Stock Sheet reports, and a Delivery Challan flow.
- **Company:** Meher (Meher Industries) — site uses Indian fiscal years (Apr–Mar).
- **Module name:** `Mhr` (single top-level module — see `mhr/modules.txt`).
- **Frappe / ERPNext version:** **v15** (`pyproject.toml` pins `frappe ~=15.0.0`, `erpnext >=15.0.0,<17.0.0`, Python `>=3.10`).
- **Production scale:** 100K+ Batch rows. Anything that touches Batch in a loop must be index-aware (see custom indexes below).

## Site / bench

- **Site:** `mhr.erpera.io`
- **Bench:** `/home/frappe/frappe-bench`
- **Nginx port:** `89` (from `sites/mhr.erpera.io/site_config.json` → `nginx_port`)
- **Local URL:** `http://mhr.erpera.io:89`
- **Branch:** `master`
- **Git remotes:** `origin` → `royalsmb/looker`, `upstream` → `Reformiqo/mhr`

## Key surface area

### Custom doctypes (`mhr/mhr/doctype/`)

- `Container` — top-level container that groups Batches; carries lot / cone / pulp / lusture / glue / grade / supplier batch metadata
- `Print Batch` — bulk print + Stock Entry helper (the "Submit in Background" button lives on Stock Entry via `public/js/stock_entry.js`)
- `Batch Items`, `Container Warehouses`, `Item Specification`, `List Batches`, `Merge And Send`, `Share Docs`, `Update Batch` — child / utility doctypes

### Custom fields on Batch (managed via fixtures + a patch for indexes)

`custom_container_no`, `custom_lot_no`, `custom_cone`, `custom_pulp`, `custom_lusture`, `custom_glue`, `custom_grade`, `custom_supplier_batch_no`.

DB indexes added via `mhr/patches/v1_0/add_batch_indexes.py`: `idx_custom_container_no`, `idx_custom_lot_no`, `idx_custom_cone`, `idx_manufacturing_date`. Use `frappe.db.sql_ddl()` for DDL operations and check `information_schema.statistics` before creating new indexes to avoid duplicates.

### Reports (`mhr/mhr/report/`)

All have `prepared_report: 1` enabled (Redis caching is handled by Frappe — do NOT add a second manual cache layer).

- `Delivery Challan`
- `Meher Creation`
- `Stock Sheet (Balance Report)` — has Company filter + Accepted Warehouse column (recent commit `fa85a96`)
- `Stock Sheet (Balance Report Simple)`
- `Stock Sheet (Inward Cone Wise)` (+ `v2`)
- `Stock Sheets (Inward Coneless Stock )`
- `Stock Sheets (Inward Rest Stock )`
- `Subcontractor Material Tracking` — MI1-I50; sent / received / pending per Send-to-Subcontractor item, filterable by date / supplier / status

**Report optimization pattern** (applied across all 4 stock reports, 2026-02-08):

- Rewrote monolithic CTE SQL → `frappe.qb` + Python aggregation (the ERPNext pattern).
- Architecture: (1) query batches with `qb`, (2) query SLE/DN in chunks of 2000, (3) aggregate in Python dicts.
- Balance report: `get_batch_balances()` queries SLE + SBE; `strip_prefix()` in Python.
- Cone wise: `get_delivered_batch_ids()` returns a `set`, uses set-intersection for `out_qty`.
- Coneless / Rest: `get_delivered_quantities()` returns a qty map, `get_merge_numbers()` for Container lookup.
- JS formatters handle bold (`sort_order >= 1`) and colors (green / red) **client-side**.
- Removed manual Redis caching from `meher_creation.py` — `prepared_report` already handles it.

### Server hooks (`mhr/hooks.py` → `doc_events`)

- `Delivery Note.on_submit` → `mhr.utilis.update_item_batch`
- `Delivery Note.on_cancel` → `mhr.utilis.reverse_item_batch`
- `Delivery Note.validate` → `set_delivery_note_user`, `set_return_cone_from_original`, `calculate_delivery_note_totals`
- `Batch.validate` → `mhr.batch_qr_code.set_si_qrcode`
- `Stock Entry.validate` → `mhr.utilis.update_stock_entry`, `mhr.utilis.validate_hty_stock_entry`, `mhr.utilis.validate_subcontract_receipt` (MI1-I50 P3)
- `Stock Entry.on_submit` → `mhr.utilis.update_batch_warehouse_on_stock_entry`, `mhr.utilis.apply_subcontract_receipt` (MI1-I50 P3)
- `Stock Entry.on_cancel` → `mhr.utilis.revert_batch_warehouse_on_stock_entry`, `mhr.utilis.revert_subcontract_receipt` (MI1-I50 P3)
- `Sales Order.validate` → `mhr.utilis.validate_so_available_qty`

### Subcontract receipt flow (MI1-I50)

A "Receive entry" is any Stock Entry whose `custom_original_send_entry` points
back at a submitted Send-to-Subcontractor entry. The three hooks above are
fast-no-op for every other Stock Entry. Flow:

1. On a submitted Send entry, JS adds a **"Receive from Subcontractor"**
   button (gated on docstatus=1 + purpose=Send to Subcontractor + at least
   one item with `qty - custom_received_qty > 0`). Click → calls
   `mhr.utilis.make_receive_from_subcontractor(source_name)` which builds a
   Draft Material Transfer with reversed warehouses and item custom fields
   carried over (cone / lot / container / supplier batch / gross weight).
2. On validate of that Draft, `validate_subcontract_receipt` refuses
   over-receipts beyond `custom_overreceipt_tolerance_pct` on the source
   (aggregated by item + batch).
3. On submit, `apply_subcontract_receipt` distributes the qty across source
   rows FIFO, writes `custom_received_qty` + `custom_pending_qty`, and
   transitions `custom_subcontract_status` (`Open` → `Partially Received`
   → `Fully Received`). All writes use `update_modified=False` so the
   source's modified ts doesn't bump.
4. On cancel, `revert_subcontract_receipt` LIFOs the qty back, clamps at 0.
5. The Stock Entry's Connections panel surfaces linked Receipts via
   `override_doctype_dashboards["Stock Entry"]` →
   `mhr.overrides.stock_entry_dashboard.get_dashboard_data` (self-referential
   link, uses `non_standard_fieldnames` to point at `custom_original_send_entry`).
6. `Subcontractor Material Tracking` report aggregates all of this for review.

### Client-side JS hooks

- `doctype_js = { "Sales Order": "public/js/sales_order.js", "Stock Entry": "public/js/stock_entry.js" }`
- Stock Entry button "Submit in Background" added for **MI1-I26** to dodge gunicorn HTTP timeouts on large transfers (e.g. 245 batches in one Material Transfer).

### Whitelisted endpoints

`mhr/print.py`, `mhr/batch.py`, `mhr/container.py`, `mhr/note.py`, `mhr/sales_order.py`. All HTTP-callable functions must keep `@frappe.whitelist()` and validate permissions explicitly — don't rely on the decorator alone.

## After making changes

After modifying custom fields, property setters, reports, or client scripts via the Desk UI, ALWAYS run:

```bash
bench --site mhr.erpera.io export-fixtures --app mhr
```

Then commit the exported fixture JSON files in `mhr/fixtures/` along with your code changes. Don't hand-edit the JSON, regenerate it.

The current fixture list (in `hooks.py`) covers:

```python
fixtures = [
    {"doctype": "Client Script",  "filters": [["module", "in", ("Mhr")]]},
    {"doctype": "Custom Field",   "filters": [["module", "in", ("Mhr")]]},
    {"doctype": "Report",         "filters": [["module", "in", ("Mhr")]]},
    {"doctype": "Property Setter","filters": [["module", "in", ("Mhr")]]},
    {"doctype": "Print Format",   "filters": [["module", "in", ("Mhr")]]},
]
```

If you add a new fixture type, add it here too with appropriate filters.

## Client Scripts

Client Scripts for standard / external doctypes (e.g. `Sales Order`, `Stock Entry`, `Delivery Note`) MUST be created as **Client Script** documents in the Desk and exported via fixtures — NOT placed in `public/js/` with `doctype_js` hooks, **unless** the script is large enough to warrant a real file (Sales Order + Stock Entry already are). When in doubt, prefer Desk Client Script + fixture so the script appears under Setup > Client Script and is deployed on `bench migrate`.

## Testing — MANDATORY

**Every task MUST be tested with frappe tests before pushing.** No exceptions.

Run all tests:
```bash
bench --site mhr.erpera.io run-tests --app mhr
```

Run a specific module:
```bash
bench --site mhr.erpera.io run-tests --module mhr.tests.test_delivery_challan_report
bench --site mhr.erpera.io run-tests --module mhr.tests.test_print_batch_get_print_batch
bench --site mhr.erpera.io run-tests --module mhr.tests.test_submit_stock_entry_in_background
bench --site mhr.erpera.io run-tests --module mhr.tests.test_delivery_note_totals
```

**Before pushing any commit:**
1. Write or update tests for the changed functionality (use `frappe.tests.IntegrationTestCase`).
2. **Self-review** — re-read every changed file. Check for:
   - Wrong field names (old vs new — e.g. `custom_container_no` vs an older alias)
   - Missing imports
   - SQL syntax errors (backticks, escaping, parameter binding)
   - Hardcoded values that should be dynamic (warehouse, company, fiscal year)
   - Edge cases (empty data, `None` values, division by zero, 0-qty batches)
   - Backward compatibility with existing data (100K+ Batch rows in prod)
   - ERPNext v15 gotchas (see below)
3. Run the full test suite: `bench --site mhr.erpera.io run-tests --app mhr`
4. ALL tests must pass.
5. Export fixtures if custom fields / property setters / reports / client scripts changed.

Site uses Indian fiscal years (Apr–Mar) which conflict with the standard Frappe test FYs (Jan–Dec). Tests that create transactional docs should set `posting_date` explicitly inside the active FY or pre-seed `frappe.local.test_objects[...]` to skip auto test record generation (mirror the pattern used in `detox_waste_management`'s `test_gate_pass.py`).

## Code conventions

- Use `frappe.get_doc` / `frappe.db.get_value` / `frappe.db.sql` with **parameters** — never f-string SQL.
- Money / qty comparisons: use `flt()` — never compare floats directly.
- Reports: use `frappe.qb` + Python aggregation, NOT monolithic CTE SQL. Chunk SLE/DN reads in batches of 2000.
- DDL: use `frappe.db.sql_ddl()`, and check `information_schema.statistics` before creating indexes to avoid duplicates.
- External API calls must be wrapped in `try` / `except` and logged via `frappe.log_error` — a sync failure must never block a doc submit.
- Secrets live in `site_config.json` and are read via `frappe.conf` — never hardcode, never commit.

## ERPNext v15 gotchas

- `frappe.get_all` does NOT allow SQL functions in `fields` (e.g. `sum(qty)`). Use `frappe.db.sql` instead.
- `batch_no` on Quality Inspection (and several stock doctypes) is a Link field to Batch — don't set arbitrary strings.
- Batch `production_date` / `manufacturing_date`: parse user input with `getdate()` before assigning (see commit `48876f9`).
- Form `refresh_field` can fire before the layout is built — guard against undefined layout on save (see commit `96519af`).
- 100K+ Batch rows in prod — any code that loops over batches without using the custom indexes (`idx_custom_container_no`, `idx_custom_lot_no`, `idx_custom_cone`, `idx_manufacturing_date`) WILL time out under gunicorn. For long-running stock operations, use the `Submit in Background` pattern on Stock Entry instead of synchronous submit.
- `prepared_report: 1` already gives you Redis-backed result caching for reports — do NOT add a second manual cache layer (we removed one from `meher_creation.py`).

## Common bench commands

Run from the bench root (`/home/frappe/frappe-bench`), NOT from the app directory.

```bash
# Migrate (ask user before running — touches schema)
bench --site mhr.erpera.io migrate

# Console (ad-hoc Python with full Frappe context)
bench --site mhr.erpera.io console

# DB shell
bench --site mhr.erpera.io mariadb

# Restart (after Python / hooks.py changes — hooks load once at process start)
bench restart

# Clear cache
bench --site mhr.erpera.io clear-cache

# Tail logs
tail -f logs/web.error.log logs/worker.error.log

# Run tests
bench --site mhr.erpera.io run-tests --app mhr

# Export fixtures (after any UI custom-field / property-setter / client-script / report edit)
bench --site mhr.erpera.io export-fixtures --app mhr
```

## FORBIDDEN COMMANDS — never run

Per the bench-wide `/home/frappe/frappe-bench/CLAUDE.md`:

- `bench build` — never run
- `bench update` — never run
- `bench reset` — never run

These break the dev environment and are reserved for manual execution by the user.

## Don't touch without asking

- `mhr/patches.txt` — only append new patches at the bottom; never reorder or delete existing entries (they've already run on prod).
- `mhr/fixtures/*.json` — regenerate via `bench export-fixtures`, don't hand-edit.
- `mhr/modules.txt` — only changes when adding/removing a top-level module.
- `sites/mhr.erpera.io/site_config.json` — contains live credentials; never commit or print its contents.

## When this file goes stale

Update the **Key surface area** section whenever a new doctype, report, hook, or whitelisted endpoint lands. A CLAUDE.md that lies is worse than no CLAUDE.md — keep it in sync with the app's actual surface.
