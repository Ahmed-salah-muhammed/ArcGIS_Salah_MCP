"""Build llms.txt and llms-full.txt (https://llmstxt.org) at the repository root.

The guidance below is written by hand; the counts, the add-in version and the whole tool reference are
read from the code at run time, so the files stay exact. Re-run after adding or changing a tool:

    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" scripts\\build_llms.py

Needs the `mcp` package (installed with this project); ArcGIS itself is not imported.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arcgis_pro_salah_mcp import server  # noqa: E402

ORDER = ["core", "live", "egdb", "geoai", "unet", "schema", "validate", "authoring", "portal", "webapp"]
GROUP_BLURB = {
    "core": "everyday ArcPy on .aprx/.gdb files, one-call project context, pipelines, background jobs (always kept)",
    "live": "the OPEN ArcGIS Pro session through the SalahAIBridge add-in",
    "egdb": "enterprise geodatabases on SQL Server / PostgreSQL / Oracle: create, connect, users and privileges, "
            "versioning, archiving, replication, administration",
    "geoai": "deep learning, vision-language and foundation models, interview-first notebook generator",
    "unet": "Utility Network",
    "schema": "domains, subtypes, attribute rules",
    "validate": "topology and geometry checks",
    "authoring": ".pyt Python toolboxes and ModelBuilder models",
    "portal": "ArcGIS Online / ArcGIS Enterprise portal via the ArcGIS API for Python",
    "webapp": "static ArcGIS Maps SDK for JavaScript 5.0 web apps and dashboards, GitHub deploy",
}


def _type(schema: dict) -> str:
    if "anyOf" in schema:
        return " | ".join(_type(s) for s in schema["anyOf"])
    kind = schema.get("type", "any")
    if kind == "array":
        return f"list[{_type(schema.get('items', {}))}]"
    if kind == "object":
        return "dict"
    return {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "null": "None"}.get(kind, kind)


def _tools() -> dict[str, list]:
    groups: dict[str, list] = {g: [] for g in ORDER}
    for tool in asyncio.run(server.mcp.list_tools()):
        groups.setdefault(server._toolset_of(tool.name), []).append(tool)
    return {g: sorted(ts, key=lambda t: t.name) for g, ts in groups.items() if ts}


def _addin_version() -> str:
    daml = (ROOT / "SalahAIBridge" / "Config.daml").read_text(encoding="utf-8")
    match = re.search(r'<AddInInfo[^>]*version="([^"]+)"', daml)
    return match.group(1) if match else "?"


def _reference(groups: dict[str, list]) -> str:
    out = ["## Tool reference", "",
           "Every tool returns `{\"ok\": true, \"data\": ...}` or `{\"ok\": false, \"error\": \"...\"}`, usually with a `code` (e.g. `confirm_required`, `readonly`, `missing_secret`, `exception`).",
           "Parameters are listed as `name: type = default`; parameters without a default are required.", ""]
    for group, tools in groups.items():
        out += [f"### Toolset `{group}` ({len(tools)} tools) - {GROUP_BLURB.get(group, '')}", ""]
        for tool in tools:
            props = (tool.inputSchema or {}).get("properties", {})
            required = set((tool.inputSchema or {}).get("required", []))
            out.append(f"#### `{tool.name}`")
            out.append((tool.description or "").strip())
            if props:
                out.append("")
                for name, spec in props.items():
                    default = "" if name in required else f" = {spec.get('default')!r}"
                    out.append(f"- `{name}: {_type(spec)}{default}`")
            out.append("")
    return "\n".join(out)


def main() -> None:
    groups = _tools()
    total = sum(len(t) for t in groups.values())
    addin = _addin_version()
    counts = ", ".join(f"{g} {len(t)}" for g, t in groups.items())

    summary = (
        f"> MCP server (FastMCP, stdio) that lets an AI agent drive the Esri ArcGIS stack end to end: ArcPy "
        f"geoprocessing on .aprx projects and geodatabases (`pro_*`), the OPEN ArcGIS Pro session through a .NET "
        f"add-in (`live_*`), publishing to ArcGIS Online / Enterprise (`portal_*`), and static ArcGIS Maps SDK for "
        f"JavaScript 5.0 web apps and dashboards (`webapp_*`). {total} tools in {len(groups)} toolsets. Every tool "
        f"returns `{{\"ok\": true, \"data\": ...}}` or `{{\"ok\": false, \"error\": ...}}` (usually with a `code`)."
    )

    key_facts = f"""Key facts for an agent using these tools:

- The server runs under ArcGIS Pro's own Python (`arcgispro-py3`). ArcPy cannot attach to a running ArcGIS Pro, so
  `pro_*` tools work on files on disk. `"CURRENT"` as a project path is resolved to the project open in Pro through
  the live bridge; saves made from the server to that `.aprx` are overwritten when Pro saves, so change the open
  session with `live_*` tools. `live_*` needs ArcGIS Pro running with the SalahAIBridge add-in ({addin}) listening on
  127.0.0.1:2026; call `live_ping` first.
- Start a session with `pro_context` (project, maps, layer schemas, counts and sample rows in one call).
- Long work - creating a geodatabase, maintenance, replicas, many versions or users - can outlive the MCP client's
  tool timeout (about a minute was observed). Run it with `pro_run_background` and poll `pro_job_status`.
  `pro_server_status` answers at once and reports what the ArcPy thread is busy with. The first ArcPy call after
  the server starts waits for the arcpy import (~25 s; ~47 s measured with a PostgreSQL connection).
- Irreversible or disruptive operations refuse with `code: "confirm_required"` until they are called again with
  `confirm=True`. Ask the user before passing it. `ARCGIS_BRIDGE_READONLY` makes every mutating `live_*` call
  refuse with `code: "readonly"`.
- Never put a password in a tool argument: tools take the NAME of an environment variable (`password_env`,
  `dba_password_env`, ...). Enterprise geodatabase creation and enabling need the user's own ArcGIS Server /
  Enterprise keycodes file: `authorization_file`, or `ARCGIS_EGDB_AUTHORIZATION_FILE` in the server environment.
- Every tool schema is sent with every request ({total} tools here). `ARCGIS_SALAH_TOOLSETS` keeps only some
  groups, e.g. `core,egdb` or `core,portal,webapp` (`core` is always kept).
- To turn a workflow into a reusable tool with parameters, use `pro_create_python_toolbox` (.pyt). `pro_create_model`
  writes ModelBuilder models but only linear chains of feature-class tools are verified; it refuses the rest.
- For deep-learning notebooks call `pro_dl_notebook` with only a goal first: it returns the questions to ask the
  user (batch size, epochs, ...) and refuses to write the notebook until they are answered."""

    llms = f"""# ArcGIS Pro Salah MCP

{summary}

{key_facts}

## Docs

- [README](README.md): install, MCP client setup (Claude Desktop, Google Antigravity), the ribbon, every toolset with examples, troubleshooting
- [llms-full.txt](llms-full.txt): this file plus workflows, verified pitfalls, configuration and the complete tool reference
- [CLAUDE.md](CLAUDE.md): architecture and verified facts for contributors (threading, enterprise geodatabase findings, toolbox authoring)
- [docs/PROTOCOL.md](docs/PROTOCOL.md): wire contract of the live bridge (`live_*` <-> SalahAIBridge add-in)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): system architecture and component map

## Toolsets

{chr(10).join(f"- `{g}` ({len(t)} tools): {GROUP_BLURB.get(g, '')}" for g, t in groups.items())}

## Source

- [server.py](src/arcgis_pro_salah_mcp/server.py): every MCP tool wrapper; its one-line docstring is the tool's spec
- [pro/ops.py](src/arcgis_pro_salah_mcp/pro/ops.py): core ArcPy operations, `pro_execute_code`, `"CURRENT"` resolution
- [pro/egdb.py](src/arcgis_pro_salah_mcp/pro/egdb.py): enterprise geodatabase toolset
- [arcpy_thread.py](src/arcgis_pro_salah_mcp/arcpy_thread.py): the single thread that owns ArcPy
- [live/ops.py](src/arcgis_pro_salah_mcp/live/ops.py) and [live/policy.py](src/arcgis_pro_salah_mcp/live/policy.py): live bridge client and its safety rails
- [SalahAIBridge/](SalahAIBridge/): the ArcGIS Pro .NET add-in (bridge server + Salah MCP ribbon)

## Optional

- [ROADMAP.md](ROADMAP.md): what is planned
- [tests/test_core.py](tests/test_core.py): backend-free tests (run without ArcGIS)
- [demos/setup_sample.py](demos/setup_sample.py): builds a sample geodatabase to try the tools
"""

    guides = f"""## Install and run

```bat
:: install into ArcGIS Pro's interpreter (the only one with ArcPy)
"C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" -m pip install -e .
:: run (normally the MCP client starts it)
"C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" -m arcgis_pro_salah_mcp.server
```

MCP client entry (Claude Desktop `claude_desktop_config.json`; Antigravity takes the same block):

```json
"mcpServers": {{
  "arcgis-pro-salah": {{
    "command": "C:\\\\Program Files\\\\ArcGIS\\\\Pro\\\\bin\\\\Python\\\\envs\\\\arcgispro-py3\\\\python.exe",
    "args": ["-m", "arcgis_pro_salah_mcp.server"],
    "env": {{"ARCGIS_SALAH_TOOLSETS": "core,egdb", "ARCGIS_EGDB_AUTHORIZATION_FILE": "C:\\\\path\\\\to\\\\your\\\\keycodes"}}
  }}
}}
```

After updating the code, fully quit and reopen the MCP client: it keeps the server process it started, and an
editable install only takes effect in a new process. The live bridge needs the SalahAIBridge add-in ({addin}):
build `SalahAIBridge\\SalahAIBridge.csproj`, close ArcGIS Pro, double-click the `.esriAddinX`.

## How the server runs tools

- One dedicated thread imports and runs ArcPy (like ArcGIS Pro's Main CIM Thread). With PostgreSQL geodatabases,
  ArcPy calls made on any other thread failed (`arcpy.ListVersions` ERROR 87395, `ArcSDESQLExecute` ERROR 87413).
- Tools never block the server: ArcPy tools queue on that thread, the others run on worker threads, so status and
  live calls are answered while a long ArcPy call runs. `pro_run_background(tool, arguments)` runs any `pro_*` tool
  as a job; `pro_job_status(job_id)` returns its state and, when finished, the tool's normal result.

## Workflows

### Analyze, publish, visualize
1. `pro_context` on the project or geodatabase, then geoprocessing (`pro_buffer`, `pro_clip`, `pro_pipeline`, ...).
2. `portal_connect` once, then `portal_publish_layer` and `portal_create_webmap`.
3. `webapp_create` or `webapp_create_dashboard`, then `webapp_github_pipeline` to deploy to GitHub Pages.

### Enterprise geodatabase, start to finish
1. Create: `pro_egdb_create` (SQL Server with Windows authentication and `schema_type="DBO_SCHEMA"` needs no
   password; otherwise pass `dba_password_env` / `gdb_admin_password_env`). In a cloud service, run
   `pro_egdb_prepare_cloud_database` (returns Esri's required SQL; `execute=True, confirm=True` runs it) and then
   `pro_egdb_enable`.
2. Connect: `pro_egdb_create_connection` writes the `.sde` file and adds it to the open project's Databases;
   `pro_egdb_describe` shows what is inside.
3. People: `pro_egdb_create_user`, `pro_egdb_create_role`, `pro_egdb_change_privileges` (view = SELECT, edit =
   INSERT+UPDATE+DELETE together), `pro_egdb_table_privileges` (each privilege separately, simple tables only),
   `pro_egdb_manage_principal` (drop user/role, set password).
4. Data: `pro_egdb_prepare_dataset` (register with the geodatabase, Global IDs, editor tracking, attachments),
   `pro_egdb_create_view`.
5. Versioning: `pro_egdb_register_versioned` (traditional through a TRANSACTIONAL connection, branch through a BRANCH
   one), `pro_egdb_create_version`, `pro_egdb_reconcile_post` (check `reconciled` / `posted` in its result),
   `pro_egdb_delete_version`.
6. History: `pro_egdb_enable_archiving`; read the past with `pro_egdb_create_connection(version_type="POINT_IN_TIME",
   date=...)`.
7. Replication: `pro_egdb_create_replica`, `pro_egdb_synchronize`, or disconnected `pro_egdb_export_changes` ->
   `pro_egdb_import_message` -> `pro_egdb_export_acknowledgement`; `pro_egdb_replica_schema` after schema changes.
8. Upkeep: `pro_egdb_connected_users`, `pro_egdb_disconnect_users`, `pro_egdb_accept_connections`,
   `pro_egdb_maintenance`, `pro_egdb_upgrade`, `pro_egdb_version_health`, `pro_egdb_config_keywords`.

### Workflow -> reusable tool
`pro_create_python_toolbox` with `steps` (keyword arguments; `{{{{param}}}}` = a tool parameter, `{{{{stepN}}}}` = an
earlier output path, `{{{{memory}}}}` = an in-memory intermediate deleted when the tool ends) or `execute_code`. It is
added to the open project. The first time Pro loads a `.pyt` it shows a "trust the code source" dialog; until the
user clicks Trust, every `live_*` call times out.

### Deep-learning notebook
`pro_dl_notebook(goal="detect buildings")` returns the recommended `arcgis.learn` model and the questions; ask the
user, then call again with the answers. The `.ipynb` is written to the open project's folder; the user adds it in
Pro (Catalog > Notebooks > Add and Open Notebook) and runs it cell by cell.

## Verified pitfalls (and how the tools handle them)

- `ReconcileVersions` compares version names case-sensitively and silently skips a mismatch while reporting success;
  names are resolved to their stored spelling and the reconcile log decides `reconciled` / `posted`.
- The DEFAULT version is `dbo.DEFAULT` in a DBO-schema SQL Server geodatabase; it is looked up, not assumed.
- Enabling a SQL Server database needs READ_COMMITTED_SNAPSHOT; when other connections block it, the error carries the
  `ALTER DATABASE` statement for a DBA.
- On a point-in-time connection `GetCount` returns today's count while a cursor returns the historical rows; the date
  is read as UTC for non-versioned archives and as server-local time for traditional-versioned ones.
- `UpgradeGDB` fails on a current geodatabase; `pro_egdb_upgrade` reports `up_to_date` instead.
- A SQL Server login that still has sessions cannot be dropped; `pro_egdb_manage_principal` refuses before dropping
  anything and returns the `KILL` statements for a DBA (ArcSDESQLExecute cannot run KILL).
- Esri requires dataset privileges to be managed through ArcGIS, so `pro_egdb_table_privileges` refuses versioned,
  archived and attachment-enabled datasets.
- ModelBuilder models with multi-input tools (Intersect, Union, Merge) or folder/file/layer intermediates did not
  validate; `pro_create_model` refuses them with `code: "unsupported_model"`.
- Registering a notebook in the open project is not possible from outside Pro; the user adds it by hand.

## Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ARCGIS_SALAH_TOOLSETS` | all | Comma list of toolsets to keep (`core` always kept) |
| `ARCGIS_EGDB_AUTHORIZATION_FILE` | - | Your own keycodes file for `pro_egdb_create` / `pro_egdb_enable` |
| `ARCGIS_BRIDGE_PORT` | `2026` | Live bridge port (server and add-in) |
| `ARCGIS_BRIDGE_READONLY` | `false` | Refuse every mutating `live_*` command |
| `ARCGIS_PORTAL_URL` | `https://www.arcgis.com` | ArcGIS Online or an Enterprise portal |
| `ARCGIS_PROFILE` / `ARCGIS_USERNAME` | - | Credentials for `portal_connect` |
| `ARCGIS_JS_SDK_VERSION` | `5.0` | Maps SDK for JavaScript version in generated apps |
| `ARCGIS_WEBAPP_OUT` | `webapp-build` | Output folder for generated apps |
| `ARCGIS_SALAH_CACHE_TTL` | `300` | Schema-cache TTL in seconds (`0` disables) |
| `ARCGIS_SALAH_NO_WARMUP` | `0` | Skip the background arcpy import |
| `ARCGIS_SALAH_NO_REEXEC` / `CLI_ANYTHING_ARCGIS_PYTHON` | - | Non-standard ArcGIS Pro Python location |
"""

    full = (f"# ArcGIS Pro Salah MCP - full reference\n\n{summary}\n\n"
            f"Generated by `scripts/build_llms.py` from the code ({total} tools: {counts}). Re-run it after changing tools.\n\n"
            f"{key_facts}\n\n{guides}\n{_reference(groups)}")

    (ROOT / "llms.txt").write_text(llms, encoding="utf-8")
    (ROOT / "llms-full.txt").write_text(full, encoding="utf-8")
    print(f"llms.txt: {len(llms):,} chars | llms-full.txt: {len(full):,} chars | {total} tools ({counts})")


if __name__ == "__main__":
    main()
