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
- `DN` and `Delivery Note Lot-Wise` — **Merge No comes from the Container master per (container, lot)** via `mhr.mhr.report.dn.dn._merge_numbers_by_container_and_lot` (0b370d3, MI1-I116), never from `dn.custom_merge_no`, which is a note-level aggregate and showed the first container's value on every row.
- `Delivery Trip Simplified` — MI1-I35; one row per Delivery Stop. MI1-I122 added a Transaction Type filter (VFY / HTY, blank = both) and column, read from the stop's Delivery Note, else the Trip, else `VFY` for legacy documents — the same `IFNULL → VFY` rule as Delivery Challan.

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
- `Delivery Note.validate` → `set_delivery_note_user`, `set_return_cone_from_original`, `calculate_delivery_note_totals`, `fetch_notes_from_container` (MI1-I83), `validate_so_delivery_qty` (MI1-I120)
- `Batch.validate` → `mhr.batch_qr_code.set_si_qrcode`
- `Stock Entry.validate` → `mhr.utilis.update_stock_entry`, `mhr.utilis.validate_hty_stock_entry`, `mhr.utilis.validate_subcontract_receipt` (MI1-I50 P3)
- `Stock Entry.before_submit` → `mhr.utilis.create_receive_batches` (MI1-I50)
- `Stock Entry.on_submit` → `mhr.utilis.apply_subcontract_receipt` (MI1-I50 P3)
- `Stock Entry.on_cancel` → `mhr.utilis.revert_subcontract_receipt` (MI1-I50 P3)
- `Sales Order.validate` → `mhr.utilis.validate_so_available_qty`, `mhr.sales_order_hty.validate_hty_sales_order` (MI1-I90)

**A stock movement never rewrites a Container's inward attributes** (MI1-I103).
`update_batch_warehouse_on_stock_entry` and its `on_cancel` twin used to write
the entry's warehouse into `Container.set_warehouse` — what Container Inward
posts its **Purchase Receipt** and every Serial and Batch Bundle to, and the
"Accepted Warehouse" column of Stock Sheet (Balance Report) — plus the
free-text "Location" notes (`Container.warehouse`, `Batch.custom_warehouse`,
`Batch Items.warehouse`). Both hooks are gone;
`heal_container_accepted_warehouse` repairs `set_warehouse` from the Purchase
Receipt, and `heal_container_location_notes` restores the free-text notes from
the container's own batches that were never moved. **Live stock location comes from Serial
and Batch Bundle** (`mhr.note._clamp_batch_qty_to_available`,
`mhr.utilis.get_all_batches_with_stock`), never off these fields.

**`mhr.note.fetch_batches` is warehouse-scoped on request** (MI1-I103). Without
`warehouse=` the clamp counts a batch's SBB balance in ANY warehouse, so a batch
already sent to a subcontractor still looks available. The Stock Entry form
passes its source (`from_warehouse` → a row's `s_warehouse` → the Container's
Accepted Warehouse) and refuses to fetch without one; Delivery Note passes none
and is unchanged. It also orders by `custom_supplier_batch_no` and re-sorts
numerically, because the column is Data and SQL puts `'10'` before `'9'`.

**`Batch.batch_qty` is not a live quantity.** ERPNext only updates it from
`Batch.recalculate_batch_qty()`, a whitelisted method behind a button on the
form — never from a stock transaction. The Batch list's "Status: Active" is a
list-view indicator derived from that stale value plus `disabled`, not a field.
Never filter availability on either.

### Sales Order HTY mode (MI1-I90)

Delivery Note's HTY behaviour, ported onto Sales Order. Lives entirely in the
app — no Desk Client Script / Server Script — and every entry point returns
early unless `transaction_type == 'HTY'`, so VFY Sales Orders and Delivery
Note in all modes are untouched.

- Fields: **`mhr/fixtures/custom_field.json`**, not a patch. A `custom_hty_tab`
  Tab Break (visible only in HTY) plus the ported spec / fetch fields, and five
  HTY fields on Sales Order Item (`custom_supplier_batch_no`, `custom_sr_no`,
  `custom_gross_weight`, `custom_cone_copy`, `custom_qty_manual_edit`).
  `transaction_type`, `custom_container_no`, `custom_lot_no` and `custom_cone`
  already existed and are reused in place, gaining only `fetch_from`.
- **Never put field definitions in a patch.** A patch runs once — frappe records
  the whole `patches.txt` line in Patch Log and skips it thereafter
  (`frappe/modules/patch_handler.py :: executed`), so later edits are a silent
  no-op on exactly the sites that already migrated. `sync_fixtures()` runs on
  every migrate. This cost two round trips on MI1-I90.
- **Picking a Batch fills the form via `fetch_from`, not script.** Twelve fields
  declare `fetch_from = custom_batch.<x>`; frappe populates them when the link
  resolves. `custom_product` / `custom_type` / `custom_colour` /
  `custom_cross_section` are deliberately excluded — they come from the
  Container, via `get_container_spec_for_batch`.
- **The HTY Tab Break is anchored at `party_account_currency`**, the last field
  before `connections_tab`. A Tab Break claims everything until the next one, so
  anchoring it earlier (e.g. at `more_info`) pulls Status / Commission / Auto
  Repeat into the HTY tab.
- Client: `public/js/sales_order_hty.js` — mode toggle, naming-series switch,
  batch dropdown filters, 4-step "Pick Containers by Lot" picker, the Select
  Batch popup (container / denier triggers), count-driven Fetch Batches, barcode
  scan, total cone, and the cone -> qty rule (`qty = Batch.batch_qty * cone /
  cone_copy`, ported from the Delivery Note's 'Cone Qty Calcuation').
- Server: `mhr/sales_order_hty.py` — the validate hook plus four whitelisted
  endpoints (`get_company_hty_defaults`, `get_so_rows_for_containers`,
  `get_hty_batches_for_container_no`, `get_container_spec_for_batch`).
- Sales Order -> Delivery Note: `mhr/sales_order_to_delivery_note.py`, wired via
  `override_whitelisted_methods`. Wraps ERPNext's `make_delivery_note`, maps the
  three item fieldnames the two DocTypes spell differently, and flags mapped rows
  `custom_qty_manual_edit` so 'Cone Qty Calcuation' cannot replace the ordered
  qty with the batch's.
- The Desk Client Script `MI1-I39 — Sales Order HTY Mode` is superseded and is
  disabled **in `mhr/fixtures/client_script.json`** — disabling it only in a
  patch does not stick, because `sync_fixtures()` runs after patches.
- **MI1-I91 reopen (Raj 2026-09-03): HTY uses the VFY booking flow**, not the
  DN-style batch popup, on Container entry. Container No -> Lot popup (Lot No +
  Item, only lots with SBB stock > 0) -> Lot No + Denier filled -> Fetch By
  (`Cone & Pallet` | `Weight`) -> `mhr.sales_order.get_so_batches`. Boxes ->
  Pallet (`custom_no_of_pallet`, hidden by default). Server reuse is literal:
  `get_container_details(transaction_type, with_stock)` and
  `get_so_batches(pallets, transaction_type)` are additive, default-off args.
  `custom_fetch_by`'s DocField options are the union of both modes (frappe
  validates a Select on save); `so_hty_apply_fetch_by_options` narrows the
  visible list per mode and is the one call in `sales_order_hty.js` that also
  runs on a VFY doc — it trims a dropdown, never a value. The VFY "Sales Order
  Booking" Client Script is untouched.
- **Lot popup offers only bookable lots (MI1-I96, both modes).**
  `get_container_details(container_no, with_stock=1)` keeps a (lot, item)
  only if its batches' Serial and Batch Bundle balance minus what open Sales
  Orders already hold (`_booked_qty_by_batch`, the same rule as
  `_get_available_qty`) is > 0, and returns that `available_qty` per row.
  The VFY "Sales Order Booking" script passes `with_stock: 1` and, when
  nothing is left, says whether the number is unknown or merely delivered /
  booked out. The plain call still lists every lot.

**An `override_whitelisted_methods` target must keep the overridden function's
exact signature** (MI1-I108). frappe calls it positionally, and there are two
entry points that disagree on the count (`frappe/model/mapper.py`):
`make_mapped_doc()` — Sales Order > Create > Delivery Note — calls
`method(source_name)`; `map_docs()` — Delivery Note > Get Items From > Sales
Order — calls `method(src, target_doc, args)`. The Get Items From dialog always
sends args, so a `**kwargs` tail (which absorbs nothing by position) is a hard
`TypeError` on that button while the Create button keeps working. Mirror the
upstream parameter list verbatim, names included, and forward positionally.

**Company has no `default_price_list` field.** ERPNext does not scope Price
Lists by Company. The old Client Script queried it anyway and every HTY Sales
Order threw `Field not permitted in query: default_price_list`. Resolve selling
price lists through `get_company_hty_defaults` (Company override → Customer →
Customer Group → Selling Settings), never with a direct client-side query.

### HTY Delivery Note "Select Batch" popup (MI1-I71 / MI1-I114)

Two triggers in the `HTY & VFY` Client Script feed one dialog
(`show_hty_batch_dialog`): Container No → `mhr.utilis.get_container_batches_with_stock(container_no, transaction_type='HTY')`,
Denier → `mhr.note.get_hty_batches_by_item(item, ..., only_available=1, transaction_type='HTY')`.
Both read the Serial and Batch Bundle balance, overwrite `batch_qty` with it
and name the `warehouse` holding it; the Select handler builds rows from that
response (never from `fetch_batches`, whose result it discards) and writes
that warehouse on the row. The extra arguments are default-off, so other
callers see the historical result. **A container whose batches exist but hold
no stock is announced, not silent** (MI1-I114: MCDL-07 had been delivered in
full, the popup did not open, and the still-filling Notes field made it look
broken); MI1-I71's silence survives only for a number that matches no HTY
batch at all. Select never closes with nothing added.

### HTY Delivery Note Batch dropdown (MI1-I76 / MI1-I85 / MI1-I118)

`mi1_i76_apply_batch_query_filters` (Client Script `MI1-I39 — Delivery Note
HTY Mode`) is the last `set_query` on `custom_batch` and `items.batch_no`.
VFY (and a blank mode) keep client-side filters: `custom_transaction_type`
plus `custom_cone > 0`. **HTY asks the server:** `mhr.note.hty_batch_query`
lists HTY batches with a Serial and Batch Bundle balance > 0 and requires
cone > 0 **unless the batch is Chips** (`CHIPS_SQL` / `is_chips_batch`:
plain `custom_product`, or the canonical `custom_glue` HTY inward folds
Product into — `Product-Chips`, older data `Glue-CHIPS`). Chips ship as bags,
cone 0, and on prod their master `batch_qty` is 0 while the bundle holds 25,
so every quantity in this flow is the bundle balance. `mhr.note.fetch_batches`
applies the same gate through `or_filters`. Picking a header Batch on an HTY
note (`mi1_i118_on_hty_batch_pick`) fills Colour / Product / Type / Supplier
Batch No from `get_item_batch(batch, with_available=1)`; rows still come from
the "HTY & VFY" Select Batch popup, which the fetch_from write of Container No
opens (qty = bundle balance, cone as the batch says). The Supplier Batch No
path (`get_delivery_note_batch`) falls back to the bundle balance in the
resolved warehouse when the master `batch_qty` is 0.

### Delivery Note ↔ Sales Order quantity cap (MI1-I120)

Two optional header fields on Delivery Note: `custom_sales_order` (Link →
Sales Order) and read-only `custom_so_total_qty` (`fetch_from
custom_sales_order.total_qty`). When a note names an order,
`validate_so_delivery_qty` blocks it if this note's qty exceeds
`ordered − already delivered`. "Already delivered" is the sum of submitted
Delivery Note rows linked to the order by **either** the header field **or**
ERPNext's per-row `against_sales_order` (the MI1-I90 / MI1-I117 mapper) —
OR'd on the row so nothing double-counts, net of returns (negative rows).
No Sales Order → the hook is a no-op and the note behaves exactly as before.
The DN reports (`dn`, `delivery_note_lot_wise`, `delivery_challan`) carry
Sales Order / SO Total Qty / SO Delivered / SO Remaining columns for linked
notes and stay blank for unlinked ones.

### Subcontract receipt flow (MI1-I50)

A "Receive entry" is any Stock Entry whose `custom_original_send_entry` points
back at a submitted Send-to-Subcontractor entry. The three hooks above are
fast-no-op for every other Stock Entry. Flow:

1. On a submitted Send entry, JS adds a **"Receive from Subcontractor"**
   button (gated on docstatus=1 + purpose=Send to Subcontractor + at least
   one item with `qty - custom_received_qty > 0`). Click → calls
   `mhr.utilis.make_receive_from_subcontractor(source_name)` which builds a
   Draft "Job Work Received" entry: sent rows with source = the
   subcontractor warehouse, **the sent batch auto-fetched**, target blank
   (header target defaults to where it was sent from), and the two editable
   header fields `custom_received_container_no` / `custom_received_lot_no`
   defaulted from the Send (MI1-I50, Raj 2026-09-03 — the subcontractor may
   return material in a different container / lot).
1b. **Purpose is per document** (`set_receive_purpose`, before_validate):
   the user may add new / finished rows with a target warehouse only.
   Material Transfer cannot hold such a row; Repack can but throws when no
   row is finished. So: Repack when any row is target-only, Material
   Transfer for a pure return. The "Job Work Received" Stock Entry Type
   keeps its stored purpose — `StockEntry.set_purpose_for_stock_entry()`
   only fills `purpose` when it is empty.
1c. On submit, `create_receive_batches` names a Batch for every row that
   has none — the new / finished items:
   `received_container-received_lot-supplier_batch_no`. The Batch gets the
   entry's transaction type (HTY / VFY parity), `manufacturing_date` =
   posting date (Aging), and the header container information (glue / pulp
   / lusture / grade / fsc / merge no / notes / cross section) — that is
   what the Delivery Note popups, Fetch Batches and stock sheets read, so a
   Delivery Challan against the received container finds it with its SBB
   balance. Duplicate ID = hard block. Stock Entry Detail has no
   container / lot columns, which is why the earlier row-based derivation
   never resolved.
2. On validate of that Draft, `validate_subcontract_receipt` refuses
   over-receipts beyond `custom_overreceipt_tolerance_pct` on the source
   (aggregated by item + supplier batch) — **only for rows carrying a batch
   the Send knows**; new / finished rows have no pending qty and are exempt.
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

- `doctype_js = { "Sales Order": "public/js/sales_order_hty.js", "Stock Entry": "public/js/stock_entry.js" }`
- `public/js/sales_order.js` was deleted upstream (it duplicated the "Sales Order Booking" Client Script's handlers). `sales_order_hty.js` is HTY-gated throughout and that Client Script is VFY-gated, so the two never act on the same document and load order does not matter.
- Stock Entry button "Submit in Background" added for **MI1-I26** to dodge gunicorn HTTP timeouts on large transfers (e.g. 245 batches in one Material Transfer).

### Whitelisted endpoints

`mhr/print.py`, `mhr/batch.py`, `mhr/container.py`, `mhr/note.py`, `mhr/sales_order.py`, `mhr/sales_order_hty.py`. All HTTP-callable functions must keep `@frappe.whitelist()` and validate permissions explicitly — don't rely on the decorator alone.

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

**Editing a fixture record means moving its `modified` forward.**
`frappe/modules/import_file.py :: import_file_by_path` skips any non-DocType
record whose `modified` in the database is not older than the one in the JSON.
Sites that already hold the record therefore never see the edit — it applies
only to fresh installs, which is the worst possible failure mode because local
testing passes. Bump `modified` in the same commit as the change.

**Never write to the form from a `refresh` handler without checking
`docstatus`** (MI1-I106). `frm.set_value` marks the form `__unsaved` for *any*
difference, a rounding artefact included, so a submitted document opens
reading "Not Saved" with an Update button. `Delivery Note V2` recomputed
`total_qty` from a raw JS float sum on every refresh: rows of
500.3 + 526.3 + 179.3 give 1205.8999999999999 while the stored value is
1205.9. Two rules for any derived total: return early unless
`docstatus === 0`, and compare at `frm.precision(fieldname)` before writing.

**`frm.add_child()` fires no grid event.** Neither `items_add` nor ERPNext's
`calculate_taxes_and_totals` runs, so a row appended by script lands with
`total_qty`, `conversion_factor` and every `fetch_from` field unset. Any flow
that builds the items table itself must recalculate afterwards. Delivery Note
and Sales Order both do, and both fall back on the server via
`mhr.utilis.ensure_total_qty`, which fills `total_qty` only when the save
arrives with it still 0.

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
