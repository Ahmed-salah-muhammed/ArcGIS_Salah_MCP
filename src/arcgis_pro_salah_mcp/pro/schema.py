"""Geodatabase schema authoring: subtypes, domains and attribute rules.

This is the "make the data behave" layer. A geodatabase that merely *holds*
features is a shapefile with extra steps; what makes it a geodatabase is the
behaviour attached to it:

* **Domains** constrain what a field may contain (a coded list, or a range).
* **Subtypes** split one feature class into behavioural categories, each with
  its own default values and its own domain assignments per field.
* **Attribute rules** are Arcade expressions the geodatabase evaluates itself —
  ``CALCULATION`` rules populate fields, ``CONSTRAINT`` rules reject bad edits,
  and ``VALIDATION`` rules flag existing rows as errors when evaluated.

Design notes
------------
* Read operations (``describe_*``) are cheap and are what the agent should reach
  for first — they let it discover the existing schema instead of guessing.
* Every write invalidates the schema cache for the affected table, because
  adding a domain or a subtype changes what ``ListFields`` reports.
* ``apply_schema`` is the round-trip killer: a whole schema (many domains, their
  coded values, subtypes and rules) applied in ONE call rather than dozens.
"""
from __future__ import annotations

from typing import Any

from .._result import err, guard, ok
from . import cache, ops

# Attribute-rule types, and which triggering events each one accepts. Kept here
# so the tool can reject an impossible combination locally with a clear message
# instead of surfacing an opaque ArcGIS error 40 seconds later.
_RULE_TYPES = {"CALCULATION", "CONSTRAINT", "VALIDATION"}
_TRIGGERS = {"INSERT", "UPDATE", "DELETE"}


# --- Domains ---------------------------------------------------------------

@guard
def list_domains(workspace: str) -> dict:
    """Every domain in a geodatabase, with its coded values or range."""
    arcpy = ops._arcpy()
    out = []
    for d in arcpy.da.ListDomains(workspace):
        entry = {
            "name": d.name,
            "description": d.description,
            "domain_type": d.domainType,
            "field_type": d.type,
            "split_policy": getattr(d, "splitPolicy", None),
            "merge_policy": getattr(d, "mergePolicy", None),
            "owner": getattr(d, "owner", None),
        }
        if d.domainType == "CodedValue":
            entry["coded_values"] = d.codedValues
            entry["value_count"] = len(d.codedValues)
        elif d.domainType == "Range":
            entry["range"] = {"min": d.range[0], "max": d.range[1]}
        out.append(entry)
    return ok({"workspace": workspace, "domains": out, "count": len(out)})


@guard
def create_domain(
    workspace: str,
    domain_name: str,
    field_type: str = "TEXT",
    domain_type: str = "CODED",
    description: str | None = None,
    coded_values: dict | None = None,
    range_min: float | None = None,
    range_max: float | None = None,
    split_policy: str = "DEFAULT",
    merge_policy: str = "DEFAULT",
) -> dict:
    """Create a coded-value or range domain and populate it in one call.

    ``coded_values`` is ``{code: description}``; codes are written with the
    field type's own Python type so a SHORT domain gets ints, not strings.
    """
    arcpy = ops._arcpy()
    domain_type = domain_type.upper()
    if domain_type not in {"CODED", "RANGE"}:
        return err("domain_type must be CODED or RANGE.")

    arcpy.management.CreateDomain(
        workspace,
        domain_name,
        description or domain_name,
        field_type.upper(),
        domain_type,
        split_policy.upper(),
        merge_policy.upper(),
    )

    added = 0
    if domain_type == "CODED":
        if not coded_values:
            return err(
                "A CODED domain needs coded_values={code: description}.",
                code="missing_coded_values",
            )
        numeric = field_type.upper() in {"SHORT", "LONG", "BIGINTEGER", "FLOAT", "DOUBLE"}
        for code, desc in coded_values.items():
            value: Any = code
            if numeric:
                try:
                    value = int(code) if field_type.upper() in {
                        "SHORT", "LONG", "BIGINTEGER"
                    } else float(code)
                except (TypeError, ValueError):
                    return err(
                        f"Coded value {code!r} is not valid for a {field_type} domain."
                    )
            arcpy.management.AddCodedValueToDomain(
                workspace, domain_name, value, str(desc)
            )
            added += 1
    else:
        if range_min is None or range_max is None:
            return err("A RANGE domain needs range_min and range_max.")
        arcpy.management.SetValueForRangeDomain(
            workspace, domain_name, range_min, range_max
        )

    return ok(
        {
            "domain": domain_name,
            "domain_type": domain_type,
            "field_type": field_type.upper(),
            "coded_values_added": added,
            "range": None if domain_type == "CODED" else {"min": range_min, "max": range_max},
        }
    )


@guard
def assign_domain(
    table: str,
    field_name: str,
    domain_name: str,
    subtype_codes: list[Any] | None = None,
) -> dict:
    """Attach a domain to a field, optionally only for specific subtypes."""
    arcpy = ops._arcpy()
    arcpy.management.AssignDomainToField(
        table, field_name, domain_name,
        [str(c) for c in subtype_codes] if subtype_codes else None,
    )
    cache.invalidate(table)
    return ok(
        {
            "table": table,
            "field": field_name,
            "domain": domain_name,
            "subtypes": subtype_codes,
        }
    )


@guard
def delete_domain(workspace: str, domain_name: str) -> dict:
    """Delete a domain. Fails (by design) while any field still uses it."""
    arcpy = ops._arcpy()
    arcpy.management.DeleteDomain(workspace, domain_name)
    return ok({"deleted": domain_name})


# --- Subtypes --------------------------------------------------------------

@guard
def describe_subtypes(table: str) -> dict:
    """The subtype field, every subtype code, and per-subtype field behaviour."""
    arcpy = ops._arcpy()
    subtypes = arcpy.da.ListSubtypes(table)
    if not subtypes:
        return ok({"table": table, "subtype_field": None, "subtypes": []})

    subtype_field = None
    out = []
    for code, info in subtypes.items():
        subtype_field = info.get("SubtypeField") or subtype_field
        fields = {}
        for fname, finfo in (info.get("FieldValues") or {}).items():
            default, domain = finfo if isinstance(finfo, tuple) else (finfo, None)
            fields[fname] = {
                "default": ops._scalar(default),
                "domain": getattr(domain, "name", None),
            }
        out.append(
            {
                "code": code,
                "name": info.get("Name"),
                "default": info.get("Default", False),
                "fields": fields,
            }
        )
    # A domain assigned without subtype_codes lives on the FIELD, and
    # ListSubtypes reports only per-subtype overrides — so report both, or the
    # caller wrongly concludes no domain was attached.
    field_domains = {}
    try:
        for f in arcpy.ListFields(table):
            if getattr(f, "domain", None):
                field_domains[f.name] = f.domain
    except Exception:  # noqa: BLE001
        pass

    return ok(
        {
            "table": table,
            "subtype_field": subtype_field or None,
            "subtypes": out,
            "count": len(out),
            "field_domains": field_domains,
        }
    )


@guard
def set_subtypes(
    table: str,
    subtype_field: str,
    subtypes: dict,
    default_code: Any | None = None,
) -> dict:
    """Turn a field into the subtype field and create every subtype at once.

    ``subtypes`` is ``{code: "Description"}``. Codes must be integers — the
    geodatabase only allows SHORT/LONG subtype fields.
    """
    arcpy = ops._arcpy()
    if not subtypes:
        return err("Provide subtypes={code: description}.")

    arcpy.management.SetSubtypeField(table, subtype_field)
    created = []
    for code, desc in subtypes.items():
        try:
            icode = int(code)
        except (TypeError, ValueError):
            return err(f"Subtype code {code!r} is not an integer.")
        arcpy.management.AddSubtype(table, icode, str(desc))
        created.append({"code": icode, "description": str(desc)})

    if default_code is not None:
        arcpy.management.SetDefaultSubtype(table, int(default_code))

    cache.invalidate(table)
    return ok(
        {
            "table": table,
            "subtype_field": subtype_field,
            "created": created,
            "default": default_code,
        }
    )


# --- Attribute rules -------------------------------------------------------

@guard
def describe_attribute_rules(table: str) -> dict:
    """Every attribute rule on a table, with its Arcade expression."""
    arcpy = ops._arcpy()
    desc = arcpy.Describe(table)
    rules = getattr(desc, "attributeRules", None) or []
    out = []
    for r in rules:
        out.append(
            {
                "name": getattr(r, "name", None),
                "type": getattr(r, "type", None),
                "field": getattr(r, "fieldName", None) or None,
                "subtype": getattr(r, "subtypeCode", None),
                "is_enabled": getattr(r, "isEnabled", None),
                "is_editable": getattr(r, "isEditable", None),
                "triggering_events": getattr(r, "triggeringEvents", None),
                "script_expression": getattr(r, "scriptExpression", None),
                "error_number": getattr(r, "errorNumber", None),
                "error_message": getattr(r, "errorMessage", None),
                "description": getattr(r, "description", None),
                "evaluation_order": getattr(r, "evaluationOrder", None),
                "exclude_from_client_evaluation": getattr(
                    r, "excludeFromClientEvaluation", None
                ),
            }
        )
    return ok({"table": table, "rules": out, "count": len(out)})


@guard
def add_attribute_rule(
    table: str,
    name: str,
    rule_type: str,
    script_expression: str,
    field: str | None = None,
    triggering_events: list[str] | None = None,
    error_number: int | None = None,
    error_message: str | None = None,
    description: str | None = None,
    subtype: str | None = None,
    is_editable: bool = True,
    exclude_from_client_evaluation: bool = False,
    batch: bool = False,
    severity: int | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Add a CALCULATION / CONSTRAINT / VALIDATION rule (Arcade).

    The combination rules ArcGIS enforces, checked here first so a mistake costs
    milliseconds instead of a failed geoprocessing run:
      * CALCULATION needs a target ``field``; CONSTRAINT and VALIDATION must not
        have one.
      * CALCULATION and CONSTRAINT need ``triggering_events``; VALIDATION rules
        are evaluated on demand and take none.
      * CONSTRAINT and VALIDATION need an ``error_number`` + ``error_message``.
    """
    # Validation first: these checks need no backend, so a malformed rule fails
    # in microseconds (and stays unit-testable with no ArcGIS installed).
    rule_type = rule_type.upper()
    if rule_type not in _RULE_TYPES:
        return err(f"rule_type must be one of {sorted(_RULE_TYPES)}.")

    if rule_type == "CALCULATION" and not field:
        return err("A CALCULATION rule needs the field it populates.", code="missing_field")
    if rule_type != "CALCULATION" and field:
        return err(
            f"A {rule_type} rule must not target a field — it evaluates the whole row.",
            code="unexpected_field",
        )

    events = [e.upper() for e in (triggering_events or [])]
    bad = set(events) - _TRIGGERS
    if bad:
        return err(f"Unknown triggering event(s): {sorted(bad)}. Use INSERT/UPDATE/DELETE.")
    if rule_type in {"CALCULATION", "CONSTRAINT"} and not events:
        return err(
            f"A {rule_type} rule needs triggering_events (e.g. ['INSERT','UPDATE']).",
            code="missing_triggers",
        )
    if rule_type == "VALIDATION" and events:
        return err(
            "A VALIDATION rule is evaluated on demand and takes no triggering_events.",
            code="unexpected_triggers",
        )
    # A VALIDATION rule must carry a severity (1-5); ArcGIS rejects None with
    # "ERROR 002709: The severity value should be 1, 2, 3, 4, or 5." Default to
    # 3 (medium) rather than making every caller remember an ArcGIS detail.
    if rule_type == "VALIDATION":
        if severity is None:
            severity = 3
        # A VALIDATION rule is evaluated in batch by definition; ArcGIS rejects
        # NOT_BATCH with "ERROR 002713: Attribute rule has invalid batch
        # property", so force it rather than surfacing that as the caller's problem.
        batch = True
    if severity is not None and int(severity) not in (1, 2, 3, 4, 5):
        return err("severity must be 1-5 (1 = highest).", code="bad_severity")

    if rule_type in {"CONSTRAINT", "VALIDATION"} and (
        error_number is None or not error_message
    ):
        return err(
            f"A {rule_type} rule needs error_number and error_message so editors "
            f"see why the edit was rejected.",
            code="missing_error",
        )

    arcpy = ops._arcpy()

    # Attribute rules require a GlobalID field on the table ("ERROR 002710"),
    # and Arcade's $feature.GlobalID resolves to nothing without one. Check here
    # so the fix is obvious instead of arriving as an opaque ArcGIS code.
    try:
        if not any(f.type == "GlobalID" for f in arcpy.ListFields(table)):
            return err(
                f"{table} has no GlobalID field, which attribute rules require. "
                f"Run arcpy.management.AddGlobalIDs on it first "
                f"(pro_run_gp tool='management.AddGlobalIDs').",
                code="missing_globalid",
            )
        # Batch rules (every VALIDATION rule is one) additionally need editor
        # tracking: "ERROR 003324: Batch calculation and validation rules are
        # only supported when editor tracking is enabled."
        if batch:
            desc = arcpy.Describe(table)
            if not getattr(desc, "editorTrackingEnabled", False):
                return err(
                    f"{rule_type} rules are evaluated in batch, which requires "
                    f"editor tracking on {table}. Enable it first "
                    f"(pro_run_gp tool='management.EnableEditorTracking').",
                    code="missing_editor_tracking",
                )
    except Exception:  # noqa: BLE001 - if we cannot check, let ArcGIS decide
        pass

    arcpy.management.AddAttributeRule(
        in_table=table,
        name=name,
        type=rule_type,
        script_expression=script_expression,
        is_editable="EDITABLE" if is_editable else "NONEDITABLE",
        triggering_events=events or None,
        error_number=error_number,
        error_message=error_message,
        description=description,
        subtype=subtype,
        field=field,
        exclude_from_client_evaluation=(
            "EXCLUDE" if exclude_from_client_evaluation else "INCLUDE"
        ),
        batch="BATCH" if batch else "NOT_BATCH",
        severity=severity,
        tags=tags,
    )
    cache.invalidate(table)
    return ok(
        {
            "table": table,
            "rule": name,
            "type": rule_type,
            "field": field,
            "triggering_events": events,
        }
    )


@guard
def evaluate_rules(
    workspace: str,
    evaluation_types: list[str] | None = None,
    extent: str | None = None,
) -> dict:
    """Run batch CALCULATION / VALIDATION rules over a workspace.

    This is how VALIDATION rules actually produce errors — they do nothing until
    evaluated. Requires a branch-versioned or non-versioned enterprise gdb for
    some types; file geodatabases support the common cases.
    """
    arcpy = ops._arcpy()
    types = [t.upper() for t in (evaluation_types or ["CALCULATION_RULES", "VALIDATION_RULES"])]
    result = arcpy.management.EvaluateRules(workspace, types, extent)
    return ok(
        {
            "workspace": workspace,
            "evaluation_types": types,
            "messages": result.getMessages() if hasattr(result, "getMessages") else None,
        }
    )


@guard
def toggle_attribute_rules(
    table: str, rule_names: list[str], enable: bool = True
) -> dict:
    """Enable or disable named rules (useful before a bulk load)."""
    arcpy = ops._arcpy()
    fn = (
        arcpy.management.EnableAttributeRules
        if enable
        else arcpy.management.DisableAttributeRules
    )
    fn(table, rule_names)
    cache.invalidate(table)
    return ok({"table": table, "rules": rule_names, "enabled": enable})


@guard
def delete_attribute_rule(table: str, rule_names: list[str], rule_type: str | None = None) -> dict:
    """Remove rules from a table."""
    arcpy = ops._arcpy()
    arcpy.management.DeleteAttributeRule(table, rule_names, rule_type)
    cache.invalidate(table)
    return ok({"table": table, "deleted": rule_names})


@guard
def export_attribute_rules(table: str, out_csv: str) -> dict:
    """Dump a table's rules to CSV so they can be reviewed or replayed."""
    arcpy = ops._arcpy()
    arcpy.management.ExportAttributeRules(table, out_csv)
    return ok({"table": table, "output": out_csv})


@guard
def import_attribute_rules(table: str, csv_files: list[str]) -> dict:
    """Apply rules previously exported with :func:`export_attribute_rules`."""
    arcpy = ops._arcpy()
    arcpy.management.ImportAttributeRules(table, csv_files)
    cache.invalidate(table)
    return ok({"table": table, "imported_from": csv_files})


# --- Whole-schema application ---------------------------------------------

@guard
def apply_schema(workspace: str, schema: dict, stop_on_error: bool = True) -> dict:
    """Apply an entire schema — domains, subtypes and rules — in ONE call.

    ``schema`` looks like::

        {
          "domains": [
            {"name": "PipeMaterial", "field_type": "TEXT", "domain_type": "CODED",
             "coded_values": {"PVC": "PVC", "DI": "Ductile Iron"}}
          ],
          "tables": [
            {"table": "Water/Mains",
             "field_domains": [{"field": "MATERIAL", "domain": "PipeMaterial"}],
             "subtype_field": "TYPE",
             "subtypes": {1: "Distribution", 2: "Transmission"},
             "default_subtype": 1,
             "rules": [
               {"name": "AutoID", "rule_type": "CALCULATION", "field": "ASSETID",
                "script_expression": "return $feature.GLOBALID;",
                "triggering_events": ["INSERT"]}
             ]}
          ]
        }

    Declaring a schema this way instead of issuing thirty separate tool calls is
    the difference between an agent that feels instant and one that does not.
    """
    steps: list[dict] = []

    def record(action: str, target: str, fn) -> bool:
        try:
            payload = fn()
            envelope = payload if isinstance(payload, dict) and "ok" in payload else ok(payload)
            entry = {"action": action, "target": target, "ok": bool(envelope.get("ok"))}
            if not envelope.get("ok"):
                entry["error"] = envelope.get("error")
            steps.append(entry)
            return bool(envelope.get("ok"))
        except Exception as exc:  # noqa: BLE001
            steps.append({"action": action, "target": target, "ok": False, "error": str(exc)})
            return False

    for d in schema.get("domains", []) or []:
        okd = record(
            "create_domain",
            d.get("name", "?"),
            lambda d=d: create_domain(
                workspace,
                d["name"],
                d.get("field_type", "TEXT"),
                d.get("domain_type", "CODED"),
                d.get("description"),
                d.get("coded_values"),
                d.get("range_min"),
                d.get("range_max"),
                d.get("split_policy", "DEFAULT"),
                d.get("merge_policy", "DEFAULT"),
            ),
        )
        if not okd and stop_on_error:
            return _schema_result(steps, stopped=True)

    for t in schema.get("tables", []) or []:
        table = t.get("table")
        if not table:
            steps.append({"action": "table", "target": "?", "ok": False, "error": "missing 'table'"})
            if stop_on_error:
                return _schema_result(steps, stopped=True)
            continue

        if t.get("subtype_field") and t.get("subtypes"):
            okd = record(
                "set_subtypes",
                table,
                lambda t=t, table=table: set_subtypes(
                    table, t["subtype_field"], t["subtypes"], t.get("default_subtype")
                ),
            )
            if not okd and stop_on_error:
                return _schema_result(steps, stopped=True)

        for fd in t.get("field_domains", []) or []:
            okd = record(
                "assign_domain",
                f"{table}.{fd.get('field')}",
                lambda fd=fd, table=table: assign_domain(
                    table, fd["field"], fd["domain"], fd.get("subtypes")
                ),
            )
            if not okd and stop_on_error:
                return _schema_result(steps, stopped=True)

        for r in t.get("rules", []) or []:
            okd = record(
                "add_attribute_rule",
                f"{table}.{r.get('name')}",
                lambda r=r, table=table: add_attribute_rule(
                    table,
                    r["name"],
                    r["rule_type"],
                    r["script_expression"],
                    r.get("field"),
                    r.get("triggering_events"),
                    r.get("error_number"),
                    r.get("error_message"),
                    r.get("description"),
                    r.get("subtype"),
                    r.get("is_editable", True),
                    r.get("exclude_from_client_evaluation", False),
                    r.get("batch", False),
                    r.get("severity"),
                    r.get("tags"),
                ),
            )
            if not okd and stop_on_error:
                return _schema_result(steps, stopped=True)

    return _schema_result(steps, stopped=False)


def _schema_result(steps: list[dict], stopped: bool) -> dict:
    applied = sum(1 for s in steps if s["ok"])
    payload = {
        "steps": steps,
        "applied": applied,
        "attempted": len(steps),
        "stopped_early": stopped,
    }
    if applied == len(steps):
        return ok(payload)
    return {"ok": False, "error": f"{len(steps) - applied} schema step(s) failed.", "data": payload}
