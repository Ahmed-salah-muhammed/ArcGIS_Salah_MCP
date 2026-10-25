"""Build llms.txt and llms-full.txt (https://llmstxt.org) at the repository root.

The guidance below is written by hand; the counts, the add-in version, the tool reference, the resources and the
prompts are read from the code at run time, so the files stay exact. Re-run after adding or changing a tool:

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

ORDER = ["core", "live", "egdb", "geoai", "unet", "schema", "validate", "authoring", "portal_base", "portal_data",
         "portal_maps", "portal_admin", "portal_analysis", "webapp"]
GROUP_BLURB = {
    "core": "everyday ArcPy on .aprx/.gdb files, one-call project context, pipelines, batches, background jobs, "
            "server status (always kept)",
    "live": "the OPEN ArcGIS Pro session through the SalahAIBridge add-in",
    "egdb": "enterprise geodatabases on SQL Server / PostgreSQL / Oracle: create, connect, users and privileges, "
            "versioning, archiving, replication, administration",
    "geoai": "deep learning, vision-language and foundation models, interview-first notebook generator",
    "unet": "Utility Network",
    "schema": "domains, subtypes, attribute rules",
    "validate": "topology and geometry checks",
    "authoring": ".pyt Python toolboxes and ModelBuilder models",
    "portal_base": "ArcGIS Online / Enterprise via the ArcGIS API for Python: sign-in (through ArcGIS Pro first), "
                   "search, items, folders, publish, share, clone, the installed-API knowledge tool, portal_python "
                   "and portal_rest escape hatches",
    "portal_data": "hosted feature layers: query, edit, calculate, append, truncate, schema, views, renderers",
    "portal_maps": "web maps, web scenes, Survey123 / Dashboards / StoryMaps / dependency graph",
    "portal_admin": "users, CSV onboarding, groups, roles and user types, org and server administration",
    "portal_analysis": "geocoding and hosted spatial analysis / GP services, credits estimated first",
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


def _resources_and_prompts() -> tuple[list, list, list]:
    async def go():
        return (await server.mcp.list_resources(), await server.mcp.list_resource_templates(),
                await server.mcp.list_prompts())

    resources, templates, prompts = asyncio.run(go())
    return (sorted(resources, key=lambda r: str(r.uri)), sorted(templates, key=lambda t: t.uriTemplate),
            sorted(prompts, key=lambda p: p.name))


def _addin_version() -> str:
    daml = (ROOT / "SalahAIBridge" / "Config.daml").read_text(encoding="utf-8")
    match = re.search(r'<AddInInfo[^>]*version="([^"]+)"', daml)
    return match.group(1) if match else "?"


def _reference(groups: dict[str, list]) -> str:
    out = ["## Tool reference", "",
           "Every tool returns `{\"ok\": true, \"data\": ...}` or `{\"ok\": false, \"error\": \"...\"}`, usually with a "
           "`code` (e.g. `confirm_required`, `bad_action`, `bad_argument`, `readonly`, `forbidden`, `exception`).",
           "Related operations share a tool with an `action` (or `kind` / `what` / `task` / `operation`) argument.",
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
    resources, templates, prompts = _resources_and_prompts()
    total = sum(len(t) for t in groups.values())
    addin = _addin_version()
    counts = ", ".join(f"{g} {len(t)}" for g, t in groups.items())

    summary = (
        f"> MCP server (FastMCP, stdio) that lets an AI agent drive the Esri ArcGIS stack end to end: ArcPy "
        f"geoprocessing on .aprx projects and geodatabases (`pro_*`), the OPEN ArcGIS Pro session through a .NET "
        f"add-in (`live_*`), ArcGIS Online / Enterprise through the ArcGIS API for Python (`portal_*`, signed in "
        f"through ArcGIS Pro automatically), and static ArcGIS Maps SDK for JavaScript 5.0 web apps and dashboards "
        f"(`webapp_*`). {total} tools in {len(groups)} toolsets, {len(resources)} resources (playbooks, live project, my content) + "
        f"{len(templates)} resource template (one portal item) and "
        f"{len(prompts)} prompts. Every tool returns `{{\"ok\": true, \"data\": ...}}` or "
        f"`{{\"ok\": false, \"error\": ...}}` (usually with a `code`)."
    )

    key_facts = f"""Key facts for an agent using these tools:

- Related operations share one tool with an `action` argument (e.g. `pro_layer action=add|remove|rename|...`,
  `portal_item action=get|update|move|delete|export|...`). A wrong action returns `code: "bad_action"` listing the
  valid ones; missing arguments are reported before any backend is touched.
- Speed comes from fewer round trips: read with `pro_context` (one call), chain GP tools with `pro_pipeline`, and run
  several tools of any layer in ONE call with `pro_batch` (`{{{{call1.data.<path>}}}}` passes a result to a later
  call; `parallel=true` for independent calls). Anything long - geodatabases, maintenance, training, exports,
  clones - takes `background=true` and is polled with `pro_server(action="job", job_id=...)`.
  `pro_server(action="status")` answers at once: warm-ups, what the ArcPy thread is doing, portal connections, jobs.
- The server runs under ArcGIS Pro's own Python (`arcgispro-py3`). ArcPy cannot attach to a running ArcGIS Pro, so
  `pro_*` tools work on files on disk. `"CURRENT"` as a project path is resolved to the project open in Pro through
  the live bridge. `live_*` needs ArcGIS Pro running with the SalahAIBridge add-in ({addin}) on 127.0.0.1:2026.
- Portal tools sign in by themselves: the ArcGIS Pro sign-in first (`GIS("pro")`, Online or Enterprise, SSO
  included), then a profile, then `username` + `password_env`. `portal_connect(action="whoami")` shows the user,
  role and what it can do. Named connections (`connection="source"` / `"target"`) work across orgs.
- When no curated tool fits: `portal_api` (search / doc of the INSTALLED arcgis library, deprecations flagged) ->
  `portal_python mode=read` -> `portal_python mode=write confirm=true`. `portal_rest` for raw REST.
- Irreversible, bulk, public, admin or credit-spending operations refuse with `code: "confirm_required"` and a
  `preview` until called again with `confirm=True`. Ask the user first. Every portal write is audited.
- Never put a password in a tool argument: tools take the NAME of an environment variable (`password_env`,
  `dba_password_env`, `api_key_env`). Enterprise geodatabase creation needs the user's own keycodes
  (`authorization_file` or `ARCGIS_EGDB_AUTHORIZATION_FILE`).
- Playbooks: `pro_server(action="guide", topic=...)` (workflow, performance, portal-users, portal-layers,
  gotchas, safety, egdb, ...; no topic lists them), also MCP resources `arcgis://guide/<topic>`. Attach an item
  as context with the resource template `arcgis://portal/item/{{item_id}}`. Reusable workflows are MCP prompts
  (plan_big_task, onboard_users_from_csv, org_audit, ...).
- `ARCGIS_SALAH_TOOLSETS` keeps only some groups, e.g. `core,portal` (`portal` = every portal group) or `core,egdb`."""

    llms = f"""# ArcGIS Pro Salah MCP

{summary}

{key_facts}

## Docs

- [README](README.md): install, MCP client setup (Claude Desktop, Google Antigravity), the ribbon, every toolset with examples, troubleshooting
- [llms-full.txt](llms-full.txt): this file plus workflows, verified pitfalls, configuration and the complete tool reference
- [CLAUDE.md](CLAUDE.md): architecture and verified facts for contributors (threading, portal, enterprise geodatabase, toolbox authoring)
- [Playbooks](src/arcgis_pro_salah_mcp/guides/): served by `pro_server action=guide` and as `arcgis://guide/<topic>`
- [docs/PROTOCOL.md](docs/PROTOCOL.md): wire contract of the live bridge (`live_*` <-> SalahAIBridge add-in)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): system architecture and component map

## Toolsets

{chr(10).join(f"- `{g}` ({len(t)} tools): {GROUP_BLURB.get(g, '')}" for g, t in groups.items())}

## Source

- [server.py](src/arcgis_pro_salah_mcp/server.py): every MCP tool, resource and prompt; a tool's docstring is its spec
- [pro/ops.py](src/arcgis_pro_salah_mcp/pro/ops.py): core ArcPy operations, `pro_execute_code`, `"CURRENT"` resolution
- [pro/egdb.py](src/arcgis_pro_salah_mcp/pro/egdb.py): enterprise geodatabase toolset
- [portal/](src/arcgis_pro_salah_mcp/portal/): ArcGIS API for Python layer (connections, content, access, layers, maps, apps, analysis, admin, knowledge, pyexec, rest, safety)
- [arcpy_thread.py](src/arcgis_pro_salah_mcp/arcpy_thread.py): the single thread that owns ArcPy
- [live/ops.py](src/arcgis_pro_salah_mcp/live/ops.py) and [live/policy.py](src/arcgis_pro_salah_mcp/live/policy.py): live bridge client and its safety rails
- [SalahAIBridge/](SalahAIBridge/): the ArcGIS Pro .NET add-in (bridge server + Salah MCP ribbon)

## Optional

- [ROADMAP.md](ROADMAP.md): what is planned
- [tests/test_core.py](tests/test_core.py): backend-free tests (run without ArcGIS)
- [demos/setup_sample.py](demos/setup_sample.py): builds a sample geodatabase to try the tools
"""

    resource_lines = "\n".join([f"- `{r.uri}` - {r.title or r.name}" for r in resources]
                               + [f"- `{t.uriTemplate}` (template) - {t.description or t.title or t.name}"
                                  for t in templates])
    prompt_lines = "\n".join(f"- `{p.name}`({', '.join(a.name for a in (p.arguments or []))}) - "
                             f"{p.title or p.description}" for p in prompts)
    guides = f"""## Install and run

```bat
:: install into ArcGIS Pro's interpreter (the only one with ArcPy; it also ships the ArcGIS API for Python)
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
    "env": {{"ARCGIS_SALAH_TOOLSETS": "core,portal,egdb", "ARCGIS_EGDB_AUTHORIZATION_FILE": "C:\\\\path\\\\to\\\\your\\\\keycodes"}}
  }}
}}
```

After updating the code, fully quit and reopen the MCP client: it keeps the server process it started, and an
editable install only takes effect in a new process. The live bridge needs the SalahAIBridge add-in ({addin}):
build `SalahAIBridge\\SalahAIBridge.csproj`, close ArcGIS Pro, double-click the `.esriAddinX`.

## How the server runs tools

- One dedicated thread imports and runs ArcPy (like ArcGIS Pro's Main CIM Thread). With PostgreSQL geodatabases,
  ArcPy calls made on any other thread failed (`arcpy.ListVersions` ERROR 87395, `ArcSDESQLExecute` ERROR 87413).
- Tools never block the server: ArcPy tools queue on that thread, the others (portal, live, webapp, the batch
  runner) run on worker threads, so status and portal calls are answered while a long ArcPy call runs.
- At start-up arcpy is imported in the background (~25 s); when a portal toolset is on, `arcgis` is imported right
  after (17 s once arcpy is loaded), the ArcGIS Pro sign-in is reused (`GIS("pro")`, ~1 s) and the index of the
  installed arcgis library is loaded (13 s to build once per arcgis version, then a disk cache). A portal call that
  arrives earlier waits for that import (one lock: arcgis has internal circular imports and two threads importing
  it at once failed with "partially initialized module").
- Library `print()` output is routed to stderr so it can never corrupt the MCP stdio channel.

## Workflows

### Analyze, publish, visualize
1. `pro_context` on the project or geodatabase, then `pro_analysis` / `pro_pipeline`.
2. `pro_batch`: `portal_publish` (file, .gdb, feature class or CSV) -> `portal_layer_schema action=renderer` ->
   `portal_map action=create` with `"layers": ["{{{{call1.data.published_item.id}}}}"]`.
3. `webapp_create kind=dashboard`, then `webapp_github_pipeline` to deploy to GitHub Pages.

### Onboard users from a CSV
`portal_onboard action=template` -> fill it -> `action=validate` (every row checked; nothing changes) ->
`action=run confirm=true` (preset roles such as "GIS Specialist" created on demand, user type set before role,
never created directly as admin, each account re-read, report CSV without passwords).

### Filtered read-only view for a dashboard
`portal_layer_schema action=create_view view_where="..." view_fields=[...] updateable=false` -> share the VIEW ->
`portal_map action=create` -> `webapp_create kind=dashboard`.

### Enterprise geodatabase, start to finish
`pro_egdb_setup action=create` (background) -> `pro_egdb_connection action=create` -> `pro_egdb_principals` ->
`pro_egdb_privileges` -> `pro_egdb_dataset action=prepare|register_versioned|archiving` -> `pro_egdb_versions` ->
`pro_egdb_replica` -> `pro_egdb_admin action=maintenance`. See `arcgis://guide/egdb`.

### Workflow -> reusable tool
`pro_create_python_toolbox` with `steps` (keyword arguments; `{{{{param}}}}` = a tool parameter, `{{{{stepN}}}}` = an
earlier output path, `{{{{memory}}}}` = an in-memory intermediate deleted when the tool ends) or `execute_code`. It is
added to the open project. The first time Pro loads a `.pyt` it shows a "trust the code source" dialog.

### Deep-learning notebook
`pro_dl_notebook(goal="detect buildings")` returns the recommended `arcgis.learn` model and the questions; ask the
user, then call again with the answers.

## Resources (playbooks and live state)

{resource_lines}

## Prompts

{prompt_lines}

## Verified pitfalls (and how the tools handle them)

- arcgis 2.4.3 `create_view`: `view_layers` is annotated `list[int]` but needs layer objects, and `visible_fields` /
  `query` make it read the new view's layers before they exist ("list index out of range") - both left a view item
  behind on ArcGIS Online. The tool creates the view plain and applies the filter and visible fields itself.
- A `Group` object hydrates lazily: reading `group.id` after `group.delete()` raises 400; ids are read first.
- Non-admin accounts get 403 on `gis.users.license_types`; known user-type aliases are used instead.
- `ContentManager.add`, `Item.share`, `ContentManager.create_folder` are deprecated (removed in arcgis 3.0) and not
  used; `portal_api` flags 66 deprecated symbols in the installed version.
- `ReconcileVersions` compares version names case-sensitively and silently skips a mismatch while reporting success;
  names are resolved to their stored spelling and the reconcile log decides `reconciled` / `posted`.
- The DEFAULT version is `dbo.DEFAULT` in a DBO-schema SQL Server geodatabase; it is looked up, not assumed.
- On a point-in-time connection `GetCount` returns today's count while a cursor returns the historical rows.
- A SQL Server login that still has sessions cannot be dropped; the tool returns the `KILL` statements for a DBA.
- ModelBuilder models with multi-input tools or folder/file/layer intermediates did not validate; `pro_create_model`
  refuses them with `code: "unsupported_model"`.

## Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ARCGIS_SALAH_TOOLSETS` | all | Comma list of toolsets to keep (`core` always kept; `portal` = every portal group) |
| `ARCGIS_EGDB_AUTHORIZATION_FILE` | - | Your own keycodes file for enterprise geodatabase create/enable |
| `ARCGIS_BRIDGE_PORT` | `2026` | Live bridge port (server and add-in) |
| `ARCGIS_BRIDGE_READONLY` | `false` | Refuse every mutating `live_*` command |
| `ARCGIS_PORTAL_URL` | `https://www.arcgis.com` | Portal for non-Pro sign-ins |
| `ARCGIS_PROFILE` / `ARCGIS_USERNAME` | - | Profile / user for `portal_connect` when Pro is not signed in |
| `ARCGIS_SALAH_AUDIT_LOG` | `%LOCALAPPDATA%\\arcgis_pro_salah_mcp\\audit.jsonl` | Portal write audit log (`off` disables) |
| `ARCGIS_SALAH_MAX_EDITS` | `5000` | Feature edits per `portal_layer_edit` call |
| `ARCGIS_SALAH_MAX_USERS` | `200` | Rows per onboarding run |
| `ARCGIS_SALAH_CREDIT_LIMIT` | `100` | Most credits one confirmed call may spend |
| `ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC` | `true` | `false` disables `portal_python mode=write` |
| `ARCGIS_SALAH_REST_HOSTS` | - | Extra hosts `portal_rest` may call |
| `ARCGIS_SALAH_NO_PORTAL_WARMUP` | `0` | Skip the background arcgis import / Pro sign-in |
| `ARCGIS_SALAH_CACHE_DIR` | `%LOCALAPPDATA%\\arcgis_pro_salah_mcp` | Where the arcgis API index is cached |
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
    print(f"llms.txt: {len(llms):,} chars | llms-full.txt: {len(full):,} chars | {total} tools ({counts}) | "
          f"{len(resources)} resources + {len(templates)} template | {len(prompts)} prompts")


if __name__ == "__main__":
    main()
