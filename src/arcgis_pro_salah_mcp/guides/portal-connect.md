# Connect and authenticate (ArcGIS API for Python 2.4.x)

`portal_connect` - and every other portal tool, which opens the `default` connection on first use - signs in
in this order (`source="auto"`):

1. **ArcGIS Pro** - `GIS("pro")` reuses whatever portal Pro is signed in to: ArcGIS Online or Enterprise,
   SSO/SAML included. Verified: 1.0 s, works from any thread. The token comes from Pro.
2. **profile** - `GIS(profile="name")` (or env `ARCGIS_PROFILE`); credentials live in the OS keyring.
3. **credentials** - `url` + `username` + `password_env` = the NAME of an env var holding the password.
4. **api_key** - `api_key_env` (env var NAME).
5. **anonymous** - public content only.

Rules:
- Never pass a password or token as an argument; use the `*_env` names. Values read that way are masked in
  every output and in the audit log.
- `verify_cert=False` only when the user says the Enterprise certificate is self-signed.
- Named connections: `portal_connect(name="source", ...)`, `portal_connect(name="target", ...)` - every portal
  tool takes `connection=`. Migration uses two.
- `portal_connect(action="whoami")` returns the user, role, user type, privileges summary (`can.*`), platform
  (`isPortal` false = ArcGIS Online), org, version and available credits.
- Expired tokens (498/499) reconnect once automatically with the same recipe.

ArcGIS Online vs Enterprise: branch on `platform`. Credits, usage reports and `history` exist only on Online;
federated servers, services and logs only on Enterprise.
