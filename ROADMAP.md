# Zeroflame — Roadmap

Working notes on planned features. Nothing here is committed to a specific
release — this is a backlog to pull from.

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

## Other system improvements (backlog, not yet scheduled)

- **Production WSGI server** — replace Flask's dev server (currently
  logs "WARNING: this is a development server") with gunicorn behind the
  existing reverse proxy.
- **Automated backups on the production host** — cron job + rotation
  (the first production data load was a manual one-off backup/restore).
- **CSV/JSON export** from the dashboard for external reporting.
- **Role-based accounts** — admin vs. read-only viewer, needed once
  barista accounts get added (currently all users have full access).
- **Low-stock alerts** — notify when a specific green-bean lot drops
  below a threshold.
- **Cost tracking (COGS)** per green-bean lot/batch, possibly synced
  from GoStock purchase data.
