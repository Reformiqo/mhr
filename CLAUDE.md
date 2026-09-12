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
- `Stock Sheet (Balance Report)` — Company filter + Accepted Warehouse column. **Accepted Warehouse is the live location** since MI1-I125 (2026-09-06): the warehouse(s) holding the group's Serial and Batch Bundle balance, largest first, falling back to `Container.set_warehouse` only when nothing is stocked — a Material Transfer therefore shows its target. **Runs inline** since MI1-I119: see *Balance report performance* below.
- `Stock Sheet (Balance Report) v2` — MI1-I135 (2026-09-10); see *Stock Sheet (Balance Report) v2* below.
- `Stock Sheet (Balance Report Simple)`
- `Stock Sheet (Inward Cone Wise)` (+ `v2`)
- `Stock Sheets (Inward Coneless Stock )`
- `Stock Sheets (Inward Rest Stock )`
- `Subcontractor Material Tracking` — MI1-I50; sent / received / pending per Send-to-Subcontractor item, filterable by date / supplier / status
- `Subcontracting Stock Tracking` — MI1-I123; the whole job-work chain in one row set (Send → Job Work Received → lot in the target warehouse → Delivery Notes → balance), 17 FRD columns + optional stock-validation columns, six statuses, an unlinked-receipts section and a server-built total row. See *Subcontracting Stock Tracking report* below.
- `DN` and `Delivery Note Lot-Wise` — **Merge No is read per-row from the linked Batch** (`MAX(b.custom_merge_no)`, joined on `b.name = dni.batch_no`), exactly like the Pulp / Glue / Lusture / Grade columns next to it. Never `dn.custom_merge_no` (a note-level aggregate that showed the first container's value on every row). MI1-I116 (0b370d3) fixed that by resolving Merge No from the Container master keyed on `(container_no, lot_no)` instead — but that pair is not unique to one Container document (`MCJC-1630` carries both `MCJC-1630-2292`, Merge No `S5dx`, and `MCJC-1630-2296`, Merge No `H38x`, both at lot `11122025`), so a row for either container showed both, comma-joined ("H38x, S5dx") — MI1-I132 (2026-09-09). `Container.create_batches()` stamps `custom_merge_no` on every batch it creates from `self.merge_no`, so the Batch master resolves it exactly, per row, with no ambiguity. `delivery_note_lot_wise.py` groups by `(dn.name, container_no, lot_no)` with no item in the key — coarser than `dn.py`, which also groups by `dni.item_code` — so a single Lot-Wise row spanning batches from two different Container documents still only shows one `MAX()`-picked value; that is an existing, out-of-scope limitation of the report's own grouping, not something this fix could resolve, and is no worse than before.
- `Delivery Trip Simplified` — MI1-I35; one row per Delivery Stop. MI1-I122 added a Transaction Type filter (VFY / HTY, blank = both) and column, read from the stop's Delivery Note, else the Trip, else `VFY` for legacy documents — the same `IFNULL → VFY` rule as Delivery Challan.

**Balance report performance (MI1-I119, 2026-09-06).** A full-range run of
Stock Sheet (Balance Report) took ~85 s on the 378K-batch replica, 73 s of it
in `get_batch_balances`: every Serial and Batch Entry row joined to its bundle
through the bundle table's clustered primary key (380K wide rows, not in the
buffer pool). frappe flips `Report.prepared_report` to 1 by itself the first
time a run passes 15 s (`report.py :: enable_prepared_report`), which is why
prod showed "Rebuild" / "Generate New Report" and the HTY run never came back.
Three changes, same figures (verified cell-for-cell against the old code):

- `mhr.patches.v1_0.add_serial_batch_bundle_status_index` adds two covering
  indexes: `idx_sbb_name_status (name, docstatus, is_cancelled)` on the bundle
  table and `idx_sbe_batch_cover (batch_no, parent, warehouse, qty)` on the
  entry table, so the join runs on indexes alone. `get_batch_warehouse_balances`
  names the bundle index with `FORCE INDEX` (the optimizer prefers the primary
  key otherwise) only when it exists (`_sbb_status_index_hint`); the entry
  index the optimizer picks itself. Chunks of 5000, plain `frappe.db.sql`, and
  `HAVING ABS(SUM(qty)) > 0.0005` so only stocked (batch, warehouse) pairs
  travel back. ~4 s instead of 73 s.
- **The bundle join is not optional.** ~51K Serial and Batch Entry rows on this
  site have no bundle behind them (22.6K batches); summing entries by their own
  `docstatus` shows phantom stock. Every balance helper keeps the
  `sbb.docstatus = 1 AND sbb.is_cancelled = 0` join.
- `get_data` queries batch names first, aggregates balances, and loads full
  rows (`get_batch_rows`) and bookings only for the ~80K stocked batches the
  sheet can render.
- **Full-range runs use one whole-site balance map** (2026-09-08, after prod
  measured 16 s and flipped back). With no Container / Lot / Cone filter,
  `get_all_warehouse_balances` aggregates every batch once and keeps the map
  in Redis under `balance_cache_key()` — `COUNT(*)` + `MAX(modified)` of the
  bundle table, `MAX(modified)` of the ledger, the External Job Work set — so
  any stock movement is a new key and a repeat run (dates, Company, mode)
  skips the aggregate. Date / container filters are then applied to the ~80K
  stocked rows in Python. This is not the time-based "second cache layer"
  the note below warns about: prepared_report is off here and the key is
  content-addressed. The legacy Stock Ledger pass runs once for a large set.
  **The map is kept warm in the background**: `enqueue_balance_cache_warmup`
  runs on submit / cancel of Stock Entry, Delivery Note, Purchase Receipt and
  Stock Reconciliation (deduplicated job id `mhr::warm_balance_map`, long
  queue, after commit) and `warm_balance_cache` runs hourly, so a user's run
  finds the map ready: full range 2.9 s warm / 13.7 s cold locally. Reads go
  through `_read_cached_map` (raw Redis) because `RedisWrapper.get_value`
  remembers a miss for the rest of the request. A narrow set (Container /
  Lot / Cone filter, or fewer than `WHOLE_SITE_FROM` batches after the date
  and container filters) still takes the names-first path (~1 s).
- **Sort on `cint(cone)`.** Batch.custom_cone is an Int; Chips rows (cone 0,
  HTY) and total rows carry "", and Python cannot order 12 against "" — every
  HTY run on prod died in Step 7 (Prepared Report 3jhahn82je). That was the
  "HTY is not generating" half of the ticket.
- `mhr.patches.v1_0.set_stock_sheet_balance_report_inline` resets the flag the
  watcher had set (re-registered with a new date comment on 2026-09-08 so it
  runs again after the 16 s run had flipped it back). The standard report JSON cannot: `before_export` writes
  `prepared_report: 0` into the file, but re-importing a JSON over an existing
  Report does not apply it (verified with `import_file_by_path(force=True)`).
  **The report puts itself back inline** (`keep_inline`, 2026-09-08): if a
  run started with the flag at 0 and frappe's 15 s watcher flipped it while
  the run was going (the cold build after a stock movement is the one run that
  can), the flip is undone when the run completes; a flag that was already 1
  at the start is an administrator's choice and is respected.

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
- `Stock Entry.validate` → `mhr.utilis.update_stock_entry`, `mhr.utilis.validate_hty_stock_entry`, `mhr.utilis.validate_subcontract_receipt` (MI1-I50 P3), `mhr.utilis.calculate_received_totals` (MI1-I133 follow-up)
- `Stock Entry.before_submit` → `mhr.utilis.create_receive_batches` (MI1-I50)
- `Stock Entry.on_submit` → `mhr.utilis.apply_subcontract_receipt` (MI1-I50 P3)
- `Stock Entry.on_cancel` → `mhr.utilis.revert_subcontract_receipt` (MI1-I50 P3)
- `Sales Order.validate` → `mhr.utilis.validate_so_available_qty`, `mhr.sales_order_hty.validate_hty_sales_order` (MI1-I90)
- `Sales Order.before_submit` → `mhr.sales_order.validate_so_source_warehouse` (MI1-I128)

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
and is unchanged. The scan window is ordered
`CAST(custom_supplier_batch_no AS UNSIGNED)` (MI1-I124, 2026-09-05) and the
result re-sorted numerically: the column is Data, and a text order put `'1',
'10', '100', '101'` first, so "Count 10" fetched 1, 10, 11, 12, 13, 14, 100,
101, 102, 103 instead of 1 to 10.

**`Batch.batch_qty` is not a live quantity.** ERPNext keeps it
*incrementally* (`serial_batch_bundle.update_batch_qty` adds each posted
qty), so it drifts, and `Batch.recalculate_batch_qty()` behind a form button
is the only reset. **Never preset it on a Batch a stock transaction is about
to post** — `create_receive_batches` did, and the posted qty landed on top
(prod MCL-32-.-1: 20 received, master 40; healed by
`heal_receive_batch_qty`). The "Cone Qty Calcuation" Client Script, which
rewrites a batch+cone row on save to `master × cone / cone_copy`, is capped
at the bundle balance since 2026-09-06 (`mi1_cone_qty_from_batch` via
`get_item_batch(batch, with_available=1)`). The Batch list's "Status: Active"
is a list-view indicator derived from that stale value plus `disabled`, not a
field. Never filter availability on either.

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
- **Container No is looked up once the field is left (MI1-I129, both modes).**
  `custom_container_no` is a Data field and frappe's Data control runs the
  field handler 500 ms after every pause in typing, so the VFY lot lookup ran
  on "MC" and announced "No lots found for container MC" mid-entry.
  `mi1_so_still_typing` (Sales Order Booking script) and `so_hty_still_typing`
  (`sales_order_hty.js`) return while the input has focus and arm a one-shot
  blur listener that re-runs the handler on the final value; Enter blurs the
  input. Script writes (`frm.set_value`) never have focus and run at once.
- **Set Source Warehouse must be the Container's inward warehouse (MI1-I128,
  both modes).** `mhr.sales_order.validate_so_source_warehouse` runs on
  `before_submit`: with a Container on the header, `set_warehouse` is
  mandatory and it (and every row's warehouse) must be `Container.set_warehouse`
  of that container / lot — else the warehouse its non-return Purchase Receipt
  posted to — **or** a warehouse currently holding the container's Serial and
  Batch Bundle balance (stock moved by a Material Transfer, MI1-I125, is not at
  the inward warehouse any more). Nothing known on either count → no
  comparison; the availability check decides. Both lot pickers call
  `get_container_source_warehouse` to fill a blank Set Source Warehouse with
  the inward warehouse (else the one holding most stock). Orders without a
  Container are untouched.
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
callers see the historical result. **The container is looked up once the field is left, not while typing**
(MI1-I127, 2026-09-07): Container No is a Data field and frappe's Data control
runs the handler 500 ms after every pause in typing, so `hty_still_typing`
(and `mi1_i101_still_typing` in the Container Notes script) return while the
input has focus and re-run the handler on blur; Enter blurs. The explainer's
`frappe.db.count('Batch', {filters: {...}})` counts this container only — the
filters used to be passed as the args object, so "MCF" was announced with the
site's whole Batch count. **A container whose batches exist but hold
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

Two header fields on Delivery Note: `custom_sales_order` (Link → Sales
Order) and read-only `custom_so_total_qty`, labelled "Total Quantity" but
holding the **remaining** order quantity since 2026-09-05: ordered total minus
what other submitted notes delivered (this note excluded), computed by
`mhr.utilis.get_so_remaining_qty` on pick / draft refresh (Client Script
`MI1-I120 — Delivery Note Sales Order by Customer`) and rewritten by
`validate_so_delivery_qty` on every save — no `fetch_from`, which would put
the order total back. Frozen at submission. When a note names an order,
`validate_so_delivery_qty` blocks it if this note's qty exceeds
`ordered − already delivered`. "Already delivered" is the sum of submitted
Delivery Note rows linked to the order by **either** the header field **or**
ERPNext's per-row `against_sales_order` (the MI1-I90 / MI1-I117 mapper) —
OR'd on the row so nothing double-counts, net of returns (negative rows).
No Sales Order → the hook is a no-op and the note behaves exactly as before.
The DN reports (`dn`, `delivery_note_lot_wise`, `delivery_challan`) carry
Sales Order / SO Total Qty / SO Delivered / SO Remaining columns for linked
notes and stay blank for unlinked ones.

**Revision (Raj 2026-09-05) — VFY: booking and delivery meet only at the
Sales Order number.** HTY is unchanged throughout.

- `require_vfy_sales_order` (validate): the Sales Order is **optional**
  (2026-09-06 — the revision had made it mandatory for one day). When a VFY
  note names one, it must be a submitted, open VFY order of the same
  customer. Without one the note saves and submits exactly as before: no
  allocation, no caps, no booking sync. Returns are exempt (they inherit).
- `allocate_delivery_note_to_sales_order` (**before_validate**): a VFY note
  links to its order on the **header only**. Rows stay exactly as entered and
  any ERPNext per-row link (`so_detail` / `against_sales_order`) is cleared.
  A VFY order carries one row per BOOKED batch, so linking / splitting rows
  against those chopped every shipped batch into "booked weight + remainder"
  and showed the same batch two or three times (TEST-CHALLAN-DN00006,
  2026-09-05) — and ERPNext's per-row over-delivery check would trip on the
  weight difference. Every row's item must be on the order; the item-level
  cap is the "row level" rule. Never on batch — any stocked batch may ship.
- `sync_sales_order_delivery` (on_submit / on_cancel): recomputes the
  order's row `delivered_qty` (item-wise, top-down —
  `_delivered_by_sales_order_row`), `per_delivered` and status from every
  submitted note linked to it. Idempotent, so amend / cancel need nothing.
- Caps: total level (`_delivered_against_sales_order`) and item level
  (`_delivered_by_sales_order_item`), both within the standard Over Delivery
  Allowance (`_over_delivery_allowance` → Item's, else Stock Settings').
- **Booking is released Sales-Order-wise** (`sales_order_booking_state`,
  `effective_booking_by_batch`): effective booking = ordered − delivered
  (sum over submitted notes linked to the order, whichever batches), floored
  at zero, applied down the order's rows in order; closed / cancelled / fully
  delivered orders book nothing. `_get_available_qty`, `_get_available_cones`,
  `_booked_qty_by_batch` (lot popup), `validate_so_available_qty` (Sales
  Order validate) and the Stock Sheet's `get_booked_quantities` all read it.
  HTY orders keep ERPNext's per-row `qty − delivered_qty`.
- Stock Sheet (Balance Report): per-Sales-Order rows carry Delivered Qty /
  Delivered Weight / Pending Qty / Pending Weight; Available Qty uses the
  effective booking.
- Client Script `MI1-I120 — Delivery Note Sales Order by Customer`: the
  Sales Order dropdown lists only the selected Customer's submitted open
  orders, is read-only until a Customer is chosen, and drops an order of
  another customer when the Customer changes.
- `carry_sales_order_details` stamps the header Sales Order on a VFY note
  mapped from an order (the mapper has no header field to copy it from).

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
7. `Subcontracting Stock Tracking` (MI1-I123) follows the chain past the
   receipt to the Delivery Notes — see the next section.
8. **Glue / Pulp / Lusture / Grade / FSC are dropdowns on the receive header
   too** (MI1-I130, 2026-09-08). These five Stock Entry custom fields were
   Data; they are now Link(Item Specification), the same fieldtype
   Container's own glue/pulp/lusture/grade/fsc already use, so Job Work
   Received offers the identical dropdown Container Inward does — on data
   that was already compatible (every existing non-blank value on this
   bench matched an Item Specification docname before the change). Merge No
   and Cross Section stay Data: Container's own fields of those names are
   Data too, so there is nothing to link them to. `mhr.utilis.
   get_received_container_spec(container_no, lot_no, transaction_type)`
   (client: "Stock Entry Container Info") refetches all eight Container
   fields — the five links plus Merge No / Cross Section / Notes — the
   moment the user leaves Received Container Number / Received Lot No
   having picked a different one than the Send entry's (Raj 2026-09-03),
   replacing the defaults; `setup()` scopes each dropdown to its own
   `specification_type`, mirroring Container.js (MI1-I80).
   **`mhr_bind_leave` binds straight to the input's own native `blur` and
   `keydown` events, once, eagerly (from `refresh()`) — not to
   `frm.trigger(fieldname)` or a lazily-armed listener.** Three walkthrough
   rounds each found a different way the obvious version breaks, because
   Received Container Number / Received Lot No are plain Data fields and
   frappe's Data control (`data.js :: bind_change_event`) fires the same
   field's own doc_event handler through TWO independent, unsynchronized
   paths — a native (non-debounced) `'change'` listener that only fires on
   blur, and a 500 ms-debounced `'input'` listener that fires mid-typing,
   still focused:
     1. A listener ARMED LAZILY (only from inside a guard that itself only
        ever runs via one of those two triggers) is never armed at all if
        the user types continuously and hits Enter before either ever
        fires — Enter did nothing, no blur, no fetch (MI1-I127's original
        pattern, inherited here at first).
     2. Relying on `frm.trigger(fieldname)` firing again on blur breaks
        ordinary typing: the mid-typing debounce already wrote the in-
        progress value into the model, so frappe's own model-diff
        (`validate_and_set_in_model :: is_value_same`) skips re-invoking
        the handler on the later blur — nothing "changed" as far as the
        model is concerned. Tab silently stopped refetching.
     3. Even a directly-bound native `'blur'` listener that reads
        `frm.doc.<fieldname>` can fire with a STALE value: `'blur'` fires
        *before* the native `'change'` event that writes the model, and
        that write is itself async (`frappe.run_serially`) — a fast type-
        then-Enter sent the previous lot number to the server. Fixed by
        reading `field.$input.val()` directly instead of the model.
   The net design: bind to blur/keydown directly, unconditionally, once;
   never rely on `frm.trigger` or the model being current; read the DOM.
   **The same three-part flaw is latent in MI1-I127's `hty_still_typing` /
   `mi1_i101_still_typing` and MI1-I129's `mi1_so_still_typing` /
   `so_hty_still_typing`** (Delivery Note, Sales Order Booking,
   `sales_order_hty.js`) — those shipped before this was understood and are
   unchanged by this ticket; flagged, not fixed, pending a decision on
   whether to touch already-deployed tickets.
   **Frappe validates every Link field inside
   `insert()`/`save()` before any `doc_events` hook runs** — `_validate_links()`
   precedes `run_before_save_methods()` in both — so a `validate` hook can
   never repair a bad Link value in time; `mhr.utilis.spec_link_value`
   normalizes at the one place that still needs it instead: the
   `carry_header` copy in `make_receive_from_subcontractor`, defensively, in
   case a Send entry's grade ever arrived as MI1-I107's bare HTY form
   ('AA EVEN') rather than the Item Specification docname
   ('Grade-AA EVEN') every Container.grade and VFY Batch holds.
9. **Received Item / Received Total Qty / Received Total Cone** (MI1-I133,
   2026-09-09; corrected 2026-09-10). Three header fields, VFY and HTY
   alike: `custom_received_item` (Link -> Item, plain, unfiltered — the
   same fieldtype and lack of query Container's own Item field has) is the
   FRD's "pick which item this is" control; `custom_received_total_qty`
   (Float) / `custom_received_total_cone` (Int) are read-only totals that
   **no longer read `custom_received_item` at all**.
   **Round 1 (shipped 2026-09-09)** read the live Serial and Batch Bundle
   balance of the picked Received Item in the document's Default Target
   Warehouse. **Raj's follow-up (2026-09-10, screenshot: MAT-GD-2026-00016,
   both totals reading 0 despite Target Warehouse rows plainly carrying
   Qty/Cone) corrected this**: a still-draft receipt has not posted
   anything to the ledger yet, so a first-time item's live balance reads 0
   regardless of what the draft's own rows already say. **The real,
   intended rule**: `mhr.utilis.calculate_received_totals` (Stock Entry
   `validate`, unconditional — no stock_entry_type gate) sums `qty` and
   `custom_cone` across THIS document's own Item rows that carry a Target
   Warehouse; a row's own Source Warehouse, if also set (an ordinary
   single-pair Material Transfer row), does not disqualify it — only a
   pure source-only row (Target Warehouse blank) is excluded. The Client
   Script mirrors this exactly client-side (no server round trip), wired to
   `items_add` / `items_remove` / a row's own `qty` / `custom_cone` /
   `t_warehouse` changes, plus `custom_received_item` / `to_warehouse` /
   load-of-a-draft for good measure (MI1-I106: never on a submitted doc).
   `get_item_warehouse_totals` / `get_received_item_totals` (the Round 1
   warehouse-balance query and its whitelisted wrapper) are deleted, not
   deprecated — nothing else called them.

### Subcontracting Stock Tracking report (MI1-I123)

`mhr/mhr/report/subcontracting_stock_tracking/`. Built on what MHR records,
not on the standard links the FRD assumed (`outgoing_stock_entry` /
`ste_detail` are never filled on this site; they are honoured if present).

- **Receipts are found by the header link** `custom_original_send_entry` and
  their rows matched to Send rows on `mhr.utilis._subcontract_match_key`
  (item + supplier batch), replaying `apply_subcontract_receipt`'s FIFO over
  the *submitted* receipts in posting order. Cancelled receipts drop out by
  themselves; a Send row whose replay disagrees with its stored
  `custom_received_qty` is flagged rather than silently trusted.
- **A lot is a receipt row that lands stock** (`t_warehouse` set): the sent
  batch coming back, or the new batch `create_receive_batches` named. ERPNext
  cannot post a both-warehouse row inside a Repack with bundles, so a Repack
  receipt is source-only rows + target-only finished rows; a finished row is
  attached to the Send row with the same supplier batch, else to the single
  row the receipt consumed, else to its first consumed row (flagged). A
  consumed row with nothing landed gets a receipt-only row.
- **Row grain** = Send row × receipt × batch. Rows that share a lot (the same
  batch returned in two instalments) form a group: Delivery Note lines are
  allocated across the group in receipt order (returns drain the last row),
  Stock in Hand is the lot's Serial and Batch Bundle balance shown on the
  group's first row only, and the reconciliation flags compare the lot as a
  whole. Nothing is counted twice.
- **Delivered** = submitted Delivery Note rows for item + batch shipped from
  the target warehouse (FRD J5); shipments from elsewhere are flagged, not
  counted. Returns are negative lines.
- **Status** (sheet 7, first match wins): Fully Delivered > Partially
  Delivered > Pending > Partially Received > Stock Available > Fully
  Received. `derive_status` is the single place.
- **Totals**: `add_total_row` is 0 — Sent / Pending / Subcontractor balance
  are Send-row values repeated on every lot row, so the total row is built
  in `build_total_row` on distinct Send rows; the JS renders it bold without
  a link. `prepared_report` is 0 on purpose: nineteen interactive filters
  need an inline run (0.6 s on the local data).
- Sends come through `frappe.get_list`, so Company / Warehouse user
  permissions apply (FR-17). Business Line is `transaction_type`, blank =
  VFY like every legacy document. Unlinked job-work receipts
  (`stock_entry_type LIKE 'Job Work%'`, no link) are listed after the linked
  rows with a flag, never dropped.

### Core ERPNext reports kept inline (MI1-I131)

The 15 s auto-prepared-report watcher documented under *Balance report
performance* above (`frappe.core.doctype.report.report ::
enable_prepared_report`) isn't scoped to mhr's own reports — it flips
`Report.prepared_report` to 1 for **any** Script Report, mhr-owned or core,
the first time one run takes longer than 15 s. ERPNext's standard "Stock
Ledger" report got flipped that way on this site on 2025-05-30
(`modified_by: Administrator` — the watcher's own background thread, never
a deliberate admin choice), so every open of it since showed "This is a
background report. Please set the appropriate filters and then generate a
new one." instead of running, however complete the filters were — reported
as "the Stock Ledger Report is not getting generated."

`mhr.patches.v1_0.set_stock_ledger_report_inline` reset the flag once, the
same shape as `set_stock_sheet_balance_report_inline`. But Stock Ledger's
`execute()` lives in ERPNext, not mhr, so it cannot be given the balance
report's own self-healing `keep_inline` check (that needs to run *inside*
the single report call that might trip the watcher). `mhr.utilis.
keep_core_reports_inline` — hourly, alongside `warm_balance_cache` — is the
only available safety valve: it resets `prepared_report` back to 0 for every
name in `REPORTS_TO_KEEP_INLINE` (renamed from `CORE_REPORTS_TO_KEEP_INLINE`
when MI1-I135 added both mhr-owned balance reports to it too) if a future
slow run flips it again. This is the exact flapping the balance report
needed a manually re-registered patch to fix on 2026-09-08 after one slow
run undid the first fix — the hourly job exists so that never needs a
second ticket here. **This hourly job is a second-line backstop only** — it
does nothing at all if the scheduler is disabled (true on this local
bench), and even where it runs can leave a report stuck for up to an hour.
See *Stock Sheet (Balance Report) v2*'s own note on `keep_inline`'s
REPEATABLE READ race for the fix that actually closes this for the
mhr-owned reports without depending on the scheduler.

### Stock Sheet (Balance Report) v2 (MI1-I135)

`mhr/mhr/report/stock_sheet_(balance_report)_v2/`. "Create an exact replica
of Stock Sheet (Balance Report) ... as a new report without modifying the
existing report," with the changes in an FRD reviewed and corrected by
Reformiqo's own analyst (`MHR_Stock_Sheet_Book2_Reviewed_v3.0.xlsx`) — the
original report is untouched (`git diff` on it is empty) and stays the
trusted, live-balance-sourced figure; this is a second, separate view.

- **Four new columns, immediately after Glue and before Balance Qty**: In
  Qty (Container Inward + Job Work Received produce rows), Out Qty
  (non-return deliveries), GR Received (return deliveries, `ABS()`'d —
  return qty is stored negative), Job work Send Qty (Send to Subcontractor
  issue rows). **Balance Qty itself changes formula**: In Qty − Out Qty +
  GR Received − Job work Send Qty (the FRD's own sheet 1 marks this "accepted
  as given"), replacing the live Serial and Batch Bundle balance — Balance
  Box follows the same shape over row counts. Everything else (Booked Qty,
  Available Qty — now Balance minus Booked using the new Balance —
  Delivered/Pending, Accepted Warehouse, HTY/VFY column swaps, per-Sales-
  Order row expansion, lot/container/grand totals) is untouched: `get_data`
  is the original's, byte for byte, with the group's `balance`/`balance_box`
  fed from the movement ledger instead of the live balance at the one point
  they are set, and the four raw movement figures riding along on every row
  and total the same way `balance`/`balance_box` already did.
- **Real schema, not the FRD's placeholder field names** (its own sheet 1
  flagged them as needing confirmation): there is no "Container Item"
  doctype — a Container's items are `Batch Items`, joined through
  `tabContainer` for `container_no`/`lot_no`. Stock Entry Detail carries no
  container/lot columns at all (see the Subcontract receipt flow note
  above) — Send to Subcontractor's container/lot are its own header fields
  (`custom_container_number` / `custom_lot_no`); Job Work Received's are the
  *received* header fields (`custom_received_container_no` /
  `custom_received_lot_no`). `get_movement_totals()` in the report module
  builds all five sources as separate parametrized queries merged into one
  Python dict (the established "chunk per source, aggregate in Python"
  pattern), not the FRD's single UNION ALL CTE.
- **Item code casing drifts between sources on this bench** — `50D/8F` on
  `Batch`, `50D/8f` on `Batch Items`, the identical pattern MI1-I132 hit on
  Delivery Note batches. `_movement_key()` upper-cases item_code (and
  `cint()`s cone) on both sides of every lookup so a Container Inward row
  and its Batch always land in the same bucket — first shipped with the
  wrong assumption that these agreed, caught by a real-data test that
  returned nothing until the fix.
- **Batch attribute loading is no longer gated on live SBB balance for a
  Container/Lot/Cone-filtered (or otherwise narrow) run.** The original
  loads only batches with a live balance because ITS visibility rule IS the
  live balance (a pure optimization). Here the visibility rule is the
  movement ledger, so that same gate would silently drop a batch whose
  movement-ledger balance is positive but whose live SBB balance happens to
  be zero — exactly what a Job Work Received produce-only batch (no
  Container Inward record, MI1-I50) looks like, and caught the same way, by
  a test that returned zero rows until the fix. The **whole-site
  (unfiltered) path still gates on the live-balance map** (`get_all_
  warehouse_balances`, imported from the original) — there is no whole-site
  index of "batches touched by any of the five tracked movements" to name
  the candidate set without a 500K-row `Batch` scan, so an unfiltered run of
  this report can under-report a lot whose stock left the live-balance set
  through a movement type this report does not track. A Container/Lot
  filter always gets the ungated, correct answer.
- **Known limitation, carried verbatim from the FRD's own "Issues to Settle"
  sheet (Issue 7)**: Balance Qty here covers only the five movement sources
  above. It does not cover Purchase Receipt, Material Issue, Stock
  Reconciliation, transfers between the company's own warehouses, or scrap.
  If any of those touch VFY/HTY stock, this report's Balance Qty can
  disagree with the original report and with Stock Ledger for the same row
  — "Accepted Warehouse" (still live-SBB-sourced, unchanged) can therefore
  show a location on a row whose movement-ledger Balance Qty is small, zero,
  or disagrees with what is actually in that warehouse.
- Every other "Issues to Settle" item resolved to the FRD's own recommended
  fix rather than a fresh guess: Out Qty and Delivered Qty stay genuinely
  different (Out Qty = every non-return delivery; Delivered Qty = only
  deliveries against the row's own Sales Order, unchanged from the
  original); Available Qty keeps reading "Total Booked" exactly as the
  original does today (the FRD's own fallback when Book2's "Booked Qty"
  wording was ambiguous); FSC/Denier/Count get no new columns — Denier is
  already the existing "Item" column in VFY, and the FRD's own finalized
  "RESULTING COLUMN ORDER" never lists the other two.
- `mhr.patches.v1_0.add_dn_item_container_lot_index` — `idx_dni_container_lot
  (custom_container_no, custom_lot_no)` on Delivery Note Item. Neither
  column carried an index, so a single-container narrow run of the Out
  Qty / GR Received query full-scanned the whole 327K-row table (~3.5 s);
  with the index, a Container/Lot-filtered run of this report is ~1-2 s.
- **The whole-site (unfiltered) movement map is cached** (2026-09-12, "still
  the report is not fixed" — an unfiltered run's own aggregation alone took
  ~6-7 s on top of the original report's whole-site balance scan, and a live
  stress test caught the report stuck showing "This is a background
  report..." on several of a handful of unfiltered opens in a row).
  `get_movement_totals()` is cached the same way `get_all_warehouse_balances`
  caches the balance map — content-addressed on `MAX(modified)` of
  Container / Stock Entry / Delivery Note (a child row bumps its own
  parent's `modified` on save, so tracking the three parents is enough),
  warmed by `enqueue_movement_cache_warmup` on submit/cancel of those three
  doctypes and hourly (`warm_movement_cache`). A Container/Lot/Cone-filtered
  call is never cached — already fast, and caching every distinct filter
  combination would grow unbounded.
- **`keep_inline`'s self-heal had a real, confirmed race — fixed by making
  the reset a single conditional `UPDATE`, not a read-then-write.** frappe's
  15 s watcher (`report.py :: enable_prepared_report`) runs on an
  independent connection and commits `prepared_report=1` mid-run. The
  original shape — `frappe.db.get_value(...)` then `frappe.db.set_value(...)`
  — reads that flag with a plain SELECT inside the SAME REPEATABLE READ
  transaction as the run it protects; if that transaction's snapshot
  predates the watcher's commit, the SELECT still sees the pre-flip 0 and
  silently no-ops (no Error Log entry either way), and the flip survives
  until a later request happens to open a fresh transaction, or the hourly
  job fires — nothing at all if the scheduler is disabled, as it is on this
  local bench (`bench scheduler status`). Proven live with two real
  connections: a plain SELECT on a transaction opened before an external
  commit cannot see that commit, but `UPDATE \`tabReport\` SET
  prepared_report=0 WHERE name=%s AND prepared_report=1` issued on that
  SAME stale-snapshot transaction *does* find and reset the row — InnoDB
  always evaluates a DML statement's WHERE clause against the latest
  committed data ("current read"), never an older consistent-read snapshot.
  Confirmed end-to-end: a genuinely slow (~20 s) unfiltered run still
  returns real data (frappe never aborts the run in progress), and the
  very next request runs inline and fast — the flip never survives past the
  run that caused it. **The original report's own `keep_inline` has the
  identical latent race** — not touched, since MI1-I135 must not modify the
  existing report, but the same fix would apply there too if it is ever
  seen to stick.
- **Round 3 (2026-09-12, "this is not happening please check" — a live
  screenshot of the report stuck showing "This report was generated just
  now... click Rebuild"): the conditional-UPDATE fix above was necessary but
  not sufficient — it was gated on `started_inline`, and that gate itself
  was a closed loop with no way out.** The gate only let `keep_inline()`
  reset the flag when THIS SAME call had observed `prepared_report` as 0 at
  its own start. But `frappe.desk.query_report.run()` only calls a report
  module's `execute()` directly while the flag reads 0 *at the moment a
  request starts* — the instant it reads 1, every subsequent open is routed
  around `execute()` entirely into "check for a completed Prepared Report"
  handling, and even the "Rebuild" button doesn't call `execute()` inline —
  it just enqueues `frappe.core.doctype.prepared_report.prepared_report.
  generate_report` as a background job, which eventually calls the SAME
  `execute()`, which ALSO sees the flag already at 1 at ITS start and so,
  under the old gate, ALSO declined to reset it. Once flipped, nothing
  short of the hourly `keep_core_reports_inline` job (needs the scheduler,
  disabled on this bench for two months — see MI1-I138) or a manual DB
  write could ever clear it — reproduced directly: forcing the flag to 1
  and then calling `execute()` in-process left it at 1 afterward, proving
  the gate itself was the bug, not just a slow scheduler. Fixed by dropping
  `started_inline` entirely — `keep_inline()` now unconditionally resets
  the flag after any successful `execute()`, whatever it read at the start.
  There is no real "administrator deliberately wants this report to stay in
  background mode" case for either balance report to protect: every ticket
  on this flag (MI1-I119, MI1-I131, MI1-I135 three times now) has been a
  client complaint about it getting stuck, never a request to keep it that
  way. Re-verified live: flag forced to 1, then a normal `execute()` call
  (mirroring what a Rebuild-triggered background worker does) cleared it
  back to 0. The original report's own `keep_inline` carries this identical
  closed-loop gate too — not touched here for the same reason as above, but
  it has the same bug and would need the same fix if it is ever seen stuck
  with no scheduler running to clear it.
- `REPORTS_TO_KEEP_INLINE` (`mhr.utilis`, MI1-I131's hourly job, renamed
  from `CORE_REPORTS_TO_KEEP_INLINE`) now also covers both mhr-owned
  balance reports, as a second-line backstop behind the `keep_inline` fix
  above — useful chiefly where the scheduler actually runs (this local
  bench's does not).
- Added to the "Meher" workspace's Reports card, right after the original
  report — it was missing from Desk navigation entirely (discoverable only
  via Awesomebar search or the Report List) until this ticket's own
  stability follow-up caught it.

### Client-side JS hooks

- `doctype_js = { "Sales Order": "public/js/sales_order_hty.js", "Stock Entry": "public/js/stock_entry.js" }`
- `public/js/sales_order.js` was deleted upstream (it duplicated the "Sales Order Booking" Client Script's handlers). `sales_order_hty.js` is HTY-gated throughout and that Client Script is VFY-gated, so the two never act on the same document and load order does not matter.
- Stock Entry button "Submit in Background" added for **MI1-I26** to dodge gunicorn HTTP timeouts on large transfers (e.g. 245 batches in one Material Transfer).

**`mhr.utilis` no longer runs anything at import time** (2026-09-08). It
used to call `update_pr_with_container_details()` at module level — an
UPDATE over every Purchase Receipt plus `frappe.db.commit()` on every worker
start, test process and first report request (~0.8 s, and a commit inside
whatever transaction was open). The function is still callable; nothing may
be executed at import in this module.

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
