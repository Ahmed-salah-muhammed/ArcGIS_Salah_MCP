"""App items: Survey123 export, dashboard clone-and-remap, StoryMap creation, item dependency graph.

Anything else (Experience Builder, Workforce, Hub, Tracker) goes through portal_python with the
apps guide - the APIs are large and change between releases.
"""
from __future__ import annotations

import tempfile
import time
from typing import Any

from .._result import err, guard, ok
from . import _imports, connections, safety, serialize
from ._common import get_item

SURVEY_FORMATS = ("CSV", "Shapefile", "File Geodatabase", "GeoJson", "Excel")


@guard
def apps(action: str, item_id: str | None = None, item_ids: list[str] | None = None,
         export_format: str = "CSV", out_folder: str | None = None, title: str | None = None,
         folder: str | None = None, item_mapping: dict | None = None, blocks: list[dict] | None = None,
         tags: list[str] | None = None, publish: bool = False, outside_org: bool = False, confirm: bool = False,
         connection: str = "default") -> dict:
    action = (action or "").lower()
    gis = connections.get(connection)

    if action in ("survey_info", "survey_export"):
        SurveyManager = _imports.load("arcgis.apps.survey123").SurveyManager

        form = get_item(gis, item_id)
        if form.type != "Form":
            return err(f"Item is a {form.type}; Survey123 surveys are 'Form' items.", code="bad_argument")
        survey = SurveyManager(gis).get(form.id)
        if action == "survey_info":
            related = []
            try:
                related = [serialize.item(i, gis) for i in form.related_items("Survey2Service", "forward")]
            except Exception:  # noqa: BLE001
                pass
            return ok({"survey": serialize.item(form, gis, full=True), "feature_layers": related,
                       "export_formats": SURVEY_FORMATS})
        if export_format not in SURVEY_FORMATS:
            return err(f"export_format must be one of {', '.join(SURVEY_FORMATS)}.", code="bad_argument")
        out_folder = out_folder or tempfile.mkdtemp(prefix="survey_")
        started = time.perf_counter()
        path = survey.download(export_format, save_folder=out_folder)
        return ok({"survey": form.title, "format": export_format, "path": str(path),
                   "seconds": round(time.perf_counter() - started, 1)})

    if action == "dashboard_clone":
        dash = get_item(gis, item_id)
        if dash.type != "Dashboard":
            return err(f"Item is a {dash.type}, not a Dashboard.", code="bad_argument")
        mapping = {k: v for k, v in (item_mapping or {}).items()}
        for old, new in mapping.items():
            get_item(gis, old)
            get_item(gis, new)
        uses = []
        try:
            uses = [d.get("id") for d in (dash.dependent_upon() or {}).get("list", []) if d.get("id")]
        except Exception:  # noqa: BLE001
            pass
        preview = {"dashboard": serialize.item(dash), "remap": mapping or None, "it_uses": uses, "folder": folder,
                   "note": "items it uses that are not in item_mapping are cloned too"}
        if not confirm:
            return safety.confirm_required(f"Cloning dashboard '{dash.title}'", preview)
        created = gis.content.clone_items([dash], folder=folder, item_mapping=mapping or None,
                                          search_existing_items=False, copy_data=False)
        out = [serialize.item(c, gis) for c in created or []]
        safety.audit("portal_apps", "dashboard_clone", connection=connection, target=dash.id, ok=bool(out),
                     after=[o["id"] for o in out])
        return ok({"created": out, "new_dashboard": next((o for o in out if o["type"] == "Dashboard"), None)})

    if action == "storymap_create":
        if not title:
            return err('title is required; blocks like [{"text":"Intro"}, {"map":"<webmap id>"}].',
                       code="bad_argument")
        storymap = _imports.load("arcgis.apps.storymap")
        StoryMapMap, StoryMap, Text = storymap.Map, storymap.StoryMap, storymap.Text

        story = StoryMap(gis=gis)
        added = []
        for b in blocks or []:
            if b.get("text"):
                story.add(Text(b["text"]))
                added.append("text")
            elif b.get("map"):
                story.add(StoryMapMap(get_item(gis, b["map"])), caption=b.get("caption"))
                added.append("map")
        saved = story.save(title=title, tags=tags or ["arcgis-pro-salah-mcp"], access="private", publish=publish)
        item = saved if hasattr(saved, "id") else gis.content.search(f'title:"{title}" AND type:StoryMap',
                                                                      max_items=1)[0]
        safety.audit("portal_apps", "storymap_create", connection=connection, target=item.id, ok=True)
        return ok({"storymap": serialize.item(item, gis), "blocks": added, "published": publish})

    if action == "dependency_graph":
        ids = item_ids or ([item_id] if item_id else [])
        if not ids:
            return err("item_id or item_ids is required.", code="bad_argument")
        create_dependency_graph = _imports.load("arcgis.apps.itemgraph").create_dependency_graph

        started = time.perf_counter()
        graph = create_dependency_graph(gis, [get_item(gis, i) for i in ids], outside_org=outside_org)
        nodes = list(graph.nodes)
        titles: dict[str, Any] = {}
        for n in nodes[:200]:
            try:
                it = gis.content.get(n)
                titles[n] = {"title": it.title, "type": it.type} if it is not None else {"title": None}
            except Exception:  # noqa: BLE001
                titles[n] = {"title": None}
        return ok({"nodes": [{"id": n, **titles.get(n, {})} for n in nodes[:200]],
                   "edges": [{"from": a, "to": b} for a, b in list(graph.edges)[:500]],
                   "meaning": "an edge A -> B means A requires B", "count": len(nodes),
                   "seconds": round(time.perf_counter() - started, 1)})
    return err("action must be survey_info | survey_export | dashboard_clone | storymap_create | dependency_graph.",
               code="bad_action")
