# Org and server administration

`portal_admin` (most actions need an administrator; a 403 returns `code="forbidden"`, never worked around):
- `org` - name, platform, version, credits (Online), subscription, users by type.
- `credits`, `usage` (credit + user reports, ArcGIS Online), `history` (activity log, Online).
- `audit_csv` - users, user types, roles, last login, groups (+ item counts with `include_items`) to a CSV.
- `license` - licenses (Online: per product, including Pro extensions).
- Enterprise: `servers`, `services` (status per service), `service_start` / `service_stop` (confirm),
  `logs` (`hours=1`, `level=WARNING`).
- `notebooks` (your notebooks + scheduled tasks), `notebook_run` (confirm; advanced runtimes spend credits).
- `audit_log` - the last entries of THIS server's audit log (every portal write it made).

Not curated on purpose: federation, security settings, portal mode, identity providers, license changes.
Use `portal_api` + `portal_python mode=write confirm=true` only when the user explicitly asks.
