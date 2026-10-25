# Users, roles, user types, groups - and CSV onboarding

## Rules (all enforced by portal_users / portal_onboard)
1. **Resolve role names to IDs** before creating: an unknown role silently falls back to the org default.
   Built-ins: `org_admin`, `org_publisher`, `org_user`, Viewer `iAAAAAAAAAAAAAAA`, Data Editor `iBBBBBBBBBBBBBBB`.
   `roles.all()` does not return the three `org_*` roles.
2. **Resolve user types to IDs**: Viewer `viewerUT`, Contributor `editorUT`, Mobile Worker `fieldWorkerUT`,
   Creator `creatorUT`, Storyteller `storytellerUT`, Professional `GISProfessionalStdUT`, Professional Plus
   `GISProfessionalAdvUT`. Non-admins get 403 on `license_types` (measured) - then the aliases are trusted.
3. **User type first, then role** (the role must be allowed for the type).
4. **Never create directly as org_admin**: create as org_user, then update_role. Admin grants are flagged in
   every preview.
5. **Verify after every write**: re-read with `users.get` and compare user type / role / groups
   (`create()` can return None on Enterprise even on success).
6. **ArcGIS Online** without a password sends an email invitation; usernames are global. **Enterprise** built-in
   accounts need a password (`password_env` / the CSV column); SAML/AD use provider="enterprise".
7. **Deleting** a user who owns content needs `reassign_to`; prefer `action=disable` (reversible).

## Onboarding from CSV (`portal_onboard`)
Columns: `username,password,firstname,lastname,email,user_type,role,groups,credits,description`
(headers are case-insensitive; "User Type" works; groups are `;`-separated titles or ids).

1. `action=template` writes a starter file.
2. `action=validate` checks EVERY row (username >= 6 chars, no spaces, no duplicates, email, password >= 8,
   password required on Enterprise, known user type, known role, groups exist and are unambiguous, credits
   numeric) and reports which users already exist. Nothing changes; one bad row blocks the run.
3. `action=run confirm=true` creates the preset roles the CSV uses (e.g. "GIS Specialist"), creates users,
   applies type -> role -> groups, re-reads each one, and writes a report CSV next to the input
   (`*_report_YYYYmmdd_HHMMSS.csv`, never containing passwords). `update_existing=true` fixes existing users.
4. Read and write CSVs as `utf-8-sig` so Arabic names round-trip through Excel.

Preset role "GIS Specialist": portal:user:joinGroup, viewOrgGroups, viewOrgItems, viewOrgUsers, createItem,
shareToGroup, features:user:edit. `portal_roles action=create preset="GIS Specialist"` makes it alone;
the result lists privileges the portal rejected.

## Groups (`portal_groups`)
`create` (title, tags, access private|org|public, invitation-only, view-only, shared_update),
`add_users` / `remove_users` (the `notAdded` / `notRemoved` lists are checked), `update`, `delete` (confirm).
