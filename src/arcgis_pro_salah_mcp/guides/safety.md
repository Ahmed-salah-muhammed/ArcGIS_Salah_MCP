# Safety rails

- **confirm_required**: irreversible, bulk, public or admin writes refuse with `code="confirm_required"` and a
  `preview`. Show the preview, ask the user, then repeat the call with `confirm=true`. Additive low-risk writes
  (upload, add field, create folder/group/view, web map) run directly and are audited.
- **dry_run** (`portal_layer_edit`): before/after diff without writing.
- **Admin grants** are highlighted (`grants_admin`, `admin_privileges`) and never implicit.
- **Secrets**: only env-var NAMES (`password_env`, `api_key_env`, `dba_password_env`). Values are masked in
  outputs; tokens/passwords are scrubbed from errors, stdout and the audit log. Never paste a secret in chat.
- **Audit log**: JSON lines at `%LOCALAPPDATA%\arcgis_pro_salah_mcp\audit.jsonl` (env `ARCGIS_SALAH_AUDIT_LOG`;
  `off` disables): time, connection, tool, action, target, before/after, result; full code for write-mode
  portal_python. `portal_admin action=audit_log` reads it.
- **Limits**: `ARCGIS_SALAH_MAX_EDITS` (5000 feature edits / call), `ARCGIS_SALAH_MAX_USERS` (200 rows / run),
  `ARCGIS_SALAH_CREDIT_LIMIT` (100 credits / call), `ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC` (true).
- **Permissions**: the signed-in user's real privileges apply; 403s are reported, never worked around.
- **Live bridge**: `ARCGIS_BRIDGE_READONLY=true` blocks every mutating live_* command; `live_run_gp` needs confirm.
- **Enterprise geodatabase**: irreversible operations need confirm; keycodes are the user's own
  (`authorization_file` or `ARCGIS_EGDB_AUTHORIZATION_FILE`), never stored in the repo.
