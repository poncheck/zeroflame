# Zeroflame — Roadmap

Working notes on planned features. Nothing here is committed to a specific
release — this is a backlog to pull from.

## Edit bean/lot after roasting

Currently the green-bean assigned to a roast (`bean_name`/`bean_cd`) comes
straight from Boost and is effectively fixed — there's no way to fix it
from the Zeroflame UI if it was wrong or missing at roast time (e.g. the
historical "9301st roasting" case with an empty bean field, or a roast
synced before someone picked the correct bean in Boost).

### Why this matters now

- Inventory usage (`stock_movements`) is auto-derived from each roast's
  bean assignment — if the bean is wrong/missing, stock deduction is
  wrong/missing too. Right now the only fix is a manual DB edit.
- Needed before the cupping module and IKAWA import land, since both
  rely on a roast having a correct `bean_id`/`bean_cd` to link against.

### Approach

- Add an editable bean field to the roast detail page, reusing the
  existing quick-edit modal pattern already used for color/weight.
- Dropdown/autocomplete sourced from `beans` (green bean inventory)
  rather than free text, to avoid creating accidental duplicate/typo'd
  bean codes.
- On change: re-run the same stock-movement logic `inventory_sync.py`
  already uses for auto-tracking usage — reverse the old bean's
  deduction (if any) and apply the new one, so inventory stays correct
  after a correction instead of double-counting or leaving stale usage.
- Keep an audit trail (who changed it, old value, new value, when) —
  same reasoning as the role/permissions work: this edits data that
  feeds inventory and future cupping links, so silent overwrites aren't
  acceptable once more than one person can make this change.

## Cupping module

The core idea: cupping sessions should link directly to existing roast
and green-bean data (`roasts.id`, `beans.id`), so sensory scores can be
correlated with actual roast-curve parameters (DTR, charge temp, drying
time, etc.) and with green-bean origin/lot — not just logged in isolation
like most cupping apps do.

### Data model (draft)

- `cupping_sessions` — id, date, location, protocol (`sca_100` / `custom`),
  notes, created_by
- `cupping_samples` — id, session_id, label (blind code, e.g. "A"),
  roast_id (nullable FK -> roasts), bean_id (nullable FK -> beans),
  real_name (hidden until reveal, for blind cupping)
- `cupping_scores` — id, sample_id, taster_user_id, fragrance_aroma,
  flavor, aftertaste, acidity, acidity_intensity, body, body_level,
  balance, uniformity, clean_cup, sweetness, overall, defect_cups,
  defect_intensity, total_score, descriptors (tags), free_notes
- `flavor_descriptors` — lookup table (coffee flavor wheel categories)

### Features

1. **SCA 100-point scoring form** — standard categories, mobile-friendly
   layout for use at the cupping table (tablet/phone).
2. **Multi-taster sessions** — each logged-in user submits their own
   scorecard for the same samples; dashboard shows per-sample average +
   spread between tasters (panel calibration — flags an outlier taster).
3. **Blind coding** — samples get randomized labels (A/B/C...); real
   roast/bean identity hidden until a "reveal" action after scoring closes.
4. **Flavor descriptor tagging** — multi-select tags per sample from a
   flavor-wheel-style vocabulary (fruity/floral/nutty/etc.), separate from
   the numeric score.
5. **Roast-curve correlation** — for samples linked to a `roast_id`, show
   the score next to that roast's curve metadata (DTR%, charge temp,
   total time) directly on the cupping result page. Lets you answer
   "which roast profile of this lot actually tasted best" without manual
   cross-referencing.
6. **Supplier/lot quality trend** — aggregate scores over time per
   green-bean origin or supplier, for purchasing decisions (quality
   consistency check between deliveries of "the same" lot).
7. **PDF export** — printable cupping sheet per session (for sharing with
   a client or green-bean supplier).
8. **DiFluid Omni reuse** — same BLE integration already built for roast
   color could optionally capture TDS/extraction readings during cupping
   (Omni supports refractometer mode too).

### Why this instead of a generic cupping app

Cupping apps normally live completely separate from roast-log data. Since
Zeroflame already has the full roast curve + green-bean inventory, tying
cupping scores directly to `roast_id`/`bean_id` turns it into a feedback
loop: roast → taste → adjust next roast of that lot — without re-typing
anything.

## IKAWA sample roaster integration

IKAWA Pro sample roasters are commonly used for small-batch test roasts
before scaling a profile up to the production roaster (the Stronghold/
Boost machine Zeroflame already tracks) — exactly the kind of "dial in a
new lot" workflow that should feed into both the roast database and the
future cupping module.

### What's available

- IKAWA doesn't expose a public developer API, but the IKAWA Pro app
  exports full per-roast telemetry (timestamped bean temp, programmed vs.
  actual setpoint, fan %, heater %, roast state) as CSV/JSON per roast.
- IKAWA already ships an official integration with Cropster (roast data
  pushed from the IKAWA app to Cropster's cloud platform, matched against
  registered green-coffee lots/samples) — confirms this kind of
  integration is something IKAWA actively supports via export, even
  without a public API for third parties.

### Approach

- Start with **CSV import**, following the same pattern already used for
  historical Boost data (`scripts/boost_import_csv.py`): a
  `scripts/ikawa_import_csv.py` that reads an IKAWA roast-graph export and
  inserts it as a roast record, tagged with a `source` field
  (`boost` vs. `ikawa`) so the two machines' roasts are distinguishable in
  the dashboard/roast list but share the same schema/views.
- Treat IKAWA roasts explicitly as **sample/test roasts**, not production
  roasts — link them to a `bean_id` (green lot) the same way production
  roasts are, so a lot's full history (sample roasts on IKAWA → chosen
  profile → production roast on Stronghold → cupping score) stays
  traceable end to end in one place.
- If/when the cupping module exists, an IKAWA sample roast becomes a
  natural `cupping_samples.roast_id` target — this is the main reason to
  prioritize IKAWA import before or alongside the cupping module rather
  than as a fully separate feature.
- Longer term, if IKAWA ever opens a local-network API (some sample
  roasters expose a LAN API for live telemetry), revisit automatic sync
  instead of manual CSV export — not assumed available today, needs
  re-checking before committing to that approach.

## Mobile / PWA + responsive design

Currently the UI is desktop-oriented (built for the roasting station
screen). Two related but separate pieces of work:

### 1. Responsive design (prerequisite)

- Audit every template (`base.html`, `index.html`, `roasts.html`,
  `detail.html`, `inventory.html`, `login.html`) for fixed-width layouts,
  tables that don't wrap, and the temperature chart (likely needs a
  touch-friendly/zoomable chart library swap or at least a mobile
  breakpoint that simplifies it).
- Target breakpoints: phone (iPhone, ~390-430px wide), tablet (iPad, used
  at the cupping table / roasting station), desktop.
- Quick-edit modal (color/weight) and the DiFluid Omni BLE flow already
  need to work well on a phone screen — BLE pairing from a phone is
  actually a more natural use case than from a desktop.
- Navigation: hamburger/bottom-tab nav on narrow screens instead of the
  current top bar.

### 2. "Add to Home Screen" on iPhone (PWA basics)

iOS Safari turns a site into something that looks/feels like an app icon
on the home screen if it has:

- A `manifest.json` (name, short_name, icons at multiple sizes, 
  `display: standalone`, theme/background color using the Zeroflame
  logo/palette).
- `<link rel="apple-touch-icon" href="...">` + the standard favicon set
  (iOS ignores `manifest.json`'s icons for the home-screen icon itself —
  needs the explicit `apple-touch-icon` tag).
- `<meta name="apple-mobile-web-app-capable" content="yes">` and
  `apple-mobile-web-app-status-bar-style` for a native-feeling status bar
  instead of showing Safari's UI chrome.
- A minimal service worker (even just for offline-fallback/caching static
  assets) — strictly needed for Chrome/Android's "Install app" prompt;
  iOS Safari will add to home screen without one, but a service worker is
  still worth having for basic offline tolerance (e.g. viewing the last
  loaded roast if wifi drops at the roaster).
- HTTPS is required for most PWA features — already satisfied in
  production (`roasting.cukru.cafe` has a valid Let's Encrypt cert).

Once both are done: on iPhone, Safari → Share → "Add to Home Screen" gives
a proper app icon + launches full-screen without browser chrome, which is
the practical goal here (no App Store submission needed).

## GoPos stock (stock.gopos.io) integration

Goal: stop tracking green-bean inventory in two disconnected places.
Right now Zeroflame has its own `green_bean_inventory.db` (stock levels,
tags, batches, moisture readings, auto-synced usage from roasts), while
cukru.cafe's actual stock/purchasing system of record is
**stock.gopos.io** (GoStock — org 5637; already integrated elsewhere for
cash reports and calorie lookups via `/ajax/5637/products`,
`/ajax/5637/purchase_orders`, `/ajax/5637/bundles`).

### 1. Inventory sync (GoStock → Zeroflame, and back)

- Pull green coffee stock levels/lots from GoStock
  (`/ajax/5637/products` for raw green beans as line items) so
  Zeroflame's bean inventory reflects the same source of truth instead of
  being tracked manually a second time.
- Match by product name/SKU or a stored GoStock product ID per bean
  record (same pattern already used for calorie lookups when a product
  isn't found by name — fall back to an explicit ID mapping).
- Auth: reuse the existing Playwright-based OAuth token flow (headless
  Firefox → intercept `oauth/token`) already built for GoStock API calls
  elsewhere in the cukru.cafe stack — no new auth mechanism needed.
- Decide direction of truth: likely GoStock stays authoritative for
  on-hand stock quantity (purchasing happens there), Zeroflame stays
  authoritative for per-roast green-bean *usage* (it already auto-tracks
  consumption from each roast) — sync is usage flowing one way
  (Zeroflame → GoStock stock deduction) rather than a two-way merge.

### 2. End-of-day production push (Zeroflame → GoStock bundles)

- At end of day, generate a GoStock "production" entry (the `bundles`
  endpoint — same mechanism already used for `sling-gostock-produkcja`
  packaging sync) summarizing that day's roasts: green bean consumed →
  roasted coffee produced, by lot/profile.
- This turns daily roasting into a proper GoStock production record
  (green bean stock decremented, roasted-coffee stock incremented) instead
  of a manual entry Anna or Marcin has to do separately.
- Needs a batch/trigger: either a manual "close the day" button on the
  Zeroflame dashboard, or a scheduled end-of-day cron (similar to the
  existing minute-by-minute Boost sync, but once/day after roasting
  hours).
- Edge cases to handle: partial/failed roasts (don't count toward
  production), blending multiple green lots into one roasted batch,
  and weight shrinkage during roasting (green weight in vs. roasted
  weight out — Zeroflame already captures post-roast weight per roast,
  so the shrink % is available for the production record).

## User accounts, roles & permissions

Currently auth is minimal: one flat `users` table (username + password
hash), no self-service account management, no distinction between
accounts beyond who's logged in. Needed before barista accounts get
added for real (Magda already has bot access; this is about the
Zeroflame web UI itself).

### Self-service password change

- A simple "my account" page where any logged-in user changes their own
  password (old password required, new password hashed with werkzeug —
  same mechanism already used for login).
- No admin involvement needed for a routine password change — currently
  the only way to change a password is a direct DB edit (as done
  manually during the production deploy test).

### Admin profile / user management panel

- An admin-only page to list all users, create new accounts (replacing
  the current `ADMIN_USERNAME`/`ADMIN_PASSWORD` one-time bootstrap-only
  flow), reset another user's password, deactivate/remove an account.
- Needed once there's more than one or two people using the system
  (Magda, baristas, Anna) — right now account creation requires editing
  `auth.db` directly via a script.

### Roles with specific permissions

Replace the current "every logged-in user can do everything" model with
a small set of roles, each with a defined permission set:

- **Admin** — full access: user management, all roast/inventory data,
  configuration.
- **Roaster** — full access to roast log + green bean inventory (create/
  edit roasts, manual color/weight entries, inventory adjustments) but no
  user management.
- **Viewer** — read-only: browse roasts, dashboard, inventory, but can't
  edit anything. Useful for Anna (purchasing) or anyone who just needs to
  check numbers without risk of accidental edits.
- Permission checks enforced server-side (decorator on each Flask route,
  not just hiding UI elements) so this is a real access boundary, not
  cosmetic.
- Role assignment lives on the `users` table (a `role` column) and is
  only editable from the admin panel above.

## Bag/batch label printing

At the end of each roasting day, generate simple printable labels for
each roasted batch of a given green bean — one label per batch (or per
bag), covering what's needed on the packaging: bean/origin name, roast
date, batch/lot reference, and optionally the roast profile name.

- Trigger: same end-of-day "close the day" action as the GoStock
  production push above — natural to generate both from the same batch
  summary (one pass over "what got roasted today, grouped by bean").
- Output: PDF, sized for a basic label printer (or just letter/A4 sheet
  of multiple labels to cut by hand, for a cheap start without needing a
  dedicated label printer).
- Reuse existing PDF tooling already used elsewhere in the cukru.cafe
  stack (WeasyPrint + Josefin Sans, same approach as the beer tap label
  generator) rather than introducing a new PDF library.
- Content kept intentionally simple for v1: bean name, roast date,
  batch number. Can grow later (e.g. a QR/barcode linking back to the
  roast record or a cupping score, once the cupping module exists) but
  that's explicitly out of scope for the first version.
- Needs a decision on label size/printer hardware before implementation
  (thermal label printer vs. regular printer + adhesive label sheets) —
  open question, not yet settled.

## Multilanguage support (PL / EN / KO)

Currently everything is hardcoded in Polish (UI labels, button text,
flash messages, date/number formatting). Needs to support English and
Korean as well, with Polish as default.

### Approach

- Introduce Flask-Babel (or a lighter custom dict-based i18n helper if
  Babel feels heavy for this app's size) — wrap every user-facing string
  in templates/Python with a translation function (`{{ _('Wypały') }}` /
  `_('Wypały')`).
- Extract strings into `.po`/`.json` translation files per locale
  (`pl`, `en`, `ko`) — start from the Polish strings already in the
  templates as the source set.
- Language switcher in the header (next to the Zeroflame logo/name) —
  persisted per-user (stored on the `users` row or in the session) so
  each person sees their own preferred language, not a site-wide toggle.
- Korean needs font coverage check — current UI font (Josefin Sans, used
  elsewhere in the cukru.cafe stack for PDFs) doesn't cover Hangul;
  webapp will need a separate web font with Korean glyphs loaded
  conditionally for `ko` locale.
- Numbers/dates: roast time is already formatted as M:SS (locale-neutral),
  but dates and decimal separators (comma vs. period) need locale-aware
  formatting once a non-Polish locale is selected.
- Scope note: this only covers the Zeroflame UI itself — Boost
  (stronghold.coffee) roast data pulled via the scraper stays in whatever
  language it was entered in (notes field, bean names typed by whoever
  logged the roast) and won't be auto-translated.

## Other system improvements (backlog, not yet scheduled)

- **Production WSGI server** — replace Flask's dev server (currently
  logs "WARNING: this is a development server") with gunicorn behind the
  existing reverse proxy.
- **Automated backups on the production host** — cron job + rotation
  (the first production data load was a manual one-off backup/restore).
- **CSV/JSON export** from the dashboard for external reporting.
- **Low-stock alerts** — notify when a specific green-bean lot drops
  below a threshold.
- **Cost tracking (COGS)** per green-bean lot/batch, possibly synced
  from GoStock purchase data.
