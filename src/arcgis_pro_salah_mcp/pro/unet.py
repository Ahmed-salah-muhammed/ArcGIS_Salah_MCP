"""Utility Network: build the model, keep it valid, and trace it.

``arcpy.un`` exposes 124 functions. Wrapping all of them one-to-one would double
this server's tool count for very little gain, so this module is organised the
way an actual utility-network project runs:

**Inspect**   ``describe`` — the whole model (domain networks, tiers, terminals,
              categories, attributes, rules) in one read. Always start here.
**Author**    ``create`` / ``add_domain_network`` / ``add_tier`` /
              ``add_terminal_configuration`` / ``add_category`` /
              ``add_network_attribute`` / ``add_rule``. Plus ``build`` — a whole
              network authored from one declarative dict.
**Validate**  ``enable_topology`` / ``validate_topology`` / ``verify_topology`` /
              ``repair_topology`` / ``analyze``. A utility network that is not
              topologically valid cannot be traced, so this is not optional.
**Operate**   ``trace`` (all 10 trace types), ``update_subnetwork``,
              ``export_subnetwork``, ``set_subnetwork_definition``.

Two things that reliably bite people, handled here:

* **Topology must be disabled to change the schema.** Adding a tier or a rule to
  a network with an enabled topology fails. ``build`` sequences this correctly.
* **Traces need a validated topology and a defined subnetwork.** ``trace``
  reports that precondition clearly rather than surfacing a bare ArcGIS error.
"""
from __future__ import annotations

from typing import Any

from .._result import err, guard, ok
from . import cache, ops

TRACE_TYPES = [
    "CONNECTED", "SUBNETWORK", "SUBNETWORK_CONTROLLERS", "UPSTREAM", "DOWNSTREAM",
    "LOOPS", "SHORTEST_PATH", "ISOLATION", "PATH", "CIRCUIT",
]

TIER_DEFINITIONS = ["HIERARCHICAL", "PARTITIONED"]
SUBNETWORK_CONTROLLER_TYPES = ["SOURCE", "SINK"]
TOPOLOGY_TYPES = ["RADIAL", "MESH"]

# Junction/edge connectivity rule types accepted by arcpy.un.AddRule.
RULE_TYPES = [
    "JUNCTION_EDGE_CONNECTIVITY",
    "JUNCTION_JUNCTION_CONNECTIVITY",
    "CONTAINMENT",
    "STRUCTURAL_ATTACHMENT",
    "EDGE_JUNCTION_EDGE_CONNECTIVITY",
]


def _un():
    """arcpy.un — present whenever ArcPy is, but guarded for a clear message."""
    ops._arcpy()
    try:
        import arcpy.un as un  # type: ignore

        return un
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "arcpy.un (Utility Network toolbox) is unavailable on this install."
        ) from exc


# --- Inspect ---------------------------------------------------------------

@guard
def describe(utility_network: str, include_rules: bool = False) -> dict:
    """The whole utility-network model in ONE read.

    Domain networks, their tiers, terminal configurations, network attributes and
    categories. ``include_rules=True`` also pulls the connectivity rule table,
    which can be large — off by default so the common case stays fast.
    """
    arcpy = ops._arcpy()
    desc = arcpy.Describe(utility_network)

    def _terminals(cfgs) -> list[dict]:
        out = []
        for t in cfgs or []:
            out.append(
                {
                    "name": getattr(t, "terminalConfigurationName", None),
                    "id": getattr(t, "terminalConfigurationId", None),
                    "directional": getattr(t, "isDirectional", None),
                    "terminals": [
                        {
                            "id": getattr(term, "terminalId", None),
                            "name": getattr(term, "terminalName", None),
                            "upstream": getattr(term, "isUpstreamTerminal", None),
                        }
                        for term in getattr(t, "terminals", None) or []
                    ],
                }
            )
        return out

    domain_networks = []
    for dn in getattr(desc, "domainNetworks", None) or []:
        tiers = []
        for tier in getattr(dn, "tiers", None) or []:
            tiers.append(
                {
                    "name": getattr(tier, "name", None),
                    "rank": getattr(tier, "rank", None),
                    "topology_type": getattr(tier, "tierTopology", None),
                    "subnetwork_field": getattr(tier, "subnetworkFieldName", None),
                    "supports_disjoint": getattr(tier, "supportDisjointSubnetwork", None),
                }
            )
        domain_networks.append(
            {
                "name": getattr(dn, "domainNetworkName", None),
                "alias": getattr(dn, "domainNetworkAliasName", None),
                "id": getattr(dn, "domainNetworkId", None),
                "tier_definition": getattr(dn, "tierDefinition", None),
                "subnetwork_controller_type": getattr(
                    dn, "subnetworkControllerType", None
                ),
                "is_structure_network": getattr(dn, "isStructureNetwork", None),
                "tiers": tiers,
                "tier_count": len(tiers),
                "source_count": len(getattr(dn, "edgeSources", None) or [])
                + len(getattr(dn, "junctionSources", None) or []),
            }
        )

    payload: dict[str, Any] = {
        "utility_network": str(utility_network),
        "schema_version": getattr(desc, "schemaGeneration", None),
        "creation_version": getattr(desc, "creationVersion", None),
        "domain_networks": domain_networks,
        "domain_network_count": len(domain_networks),
        "terminal_configurations": _terminals(
            getattr(desc, "terminalConfigurations", None)
        ),
        "network_attributes": [
            {
                "name": getattr(a, "name", None),
                "type": getattr(a, "fieldType", None),
                "is_inline": getattr(a, "isInline", None),
                "is_apportionable": getattr(a, "isApportionable", None),
                "bitset": getattr(a, "isNetworkAttributeBitset", None),
            }
            for a in getattr(desc, "networkAttributes", None) or []
        ],
        # Categories arrive as describe objects; pull the readable name rather
        # than letting "<geoprocessing describe data object>" reach the agent.
        "categories": [
            getattr(c, "categoryName", None) or getattr(c, "name", None) or str(c)
            for c in (getattr(desc, "categories", None) or [])
        ],
    }

    if include_rules:
        payload["rules"] = [
            {
                "id": getattr(r, "ruleId", None),
                "type": getattr(r, "ruleType", None),
                "from_class": getattr(r, "fromNetworkSourceId", None),
                "to_class": getattr(r, "toNetworkSourceId", None),
            }
            for r in getattr(desc, "rules", None) or []
        ]
        payload["rule_count"] = len(payload["rules"])

    return ok(payload)


@guard
def list_trace_types() -> dict:
    """The valid trace types and what each answers. Pure lookup — no ArcPy."""
    return ok(
        {
            "trace_types": TRACE_TYPES,
            "guidance": {
                "CONNECTED": "Everything electrically/hydraulically connected to the start.",
                "SUBNETWORK": "The full subnetwork (circuit/zone/pressure zone) the start belongs to.",
                "SUBNETWORK_CONTROLLERS": "Find the source(s) feeding the start point.",
                "UPSTREAM": "Toward the source — 'what feeds this?'",
                "DOWNSTREAM": "Away from the source — 'who loses service if this fails?'",
                "LOOPS": "Cycles in the network (usually a design error).",
                "SHORTEST_PATH": "Least-cost path between two points by a network attribute.",
                "ISOLATION": "The valves/switches to operate to isolate a feature.",
                "PATH": "Any connected path between two points.",
                "CIRCUIT": "Electric circuit tracing.",
            },
        }
    )


# --- Author ----------------------------------------------------------------

@guard
def create(
    feature_dataset: str,
    name: str,
    service_territory: str,
    version: str = "CURRENT",
) -> dict:
    """Create a utility network inside a feature dataset."""
    un = _un()
    result = un.CreateUtilityNetwork(feature_dataset, name, service_territory, version)
    return ok(
        {
            "utility_network": str(result),
            "feature_dataset": feature_dataset,
            "name": name,
            "version": version,
            "next_step": "add_domain_network, then tiers, then rules, then enable_topology",
        }
    )


@guard
def add_domain_network(
    utility_network: str,
    name: str,
    tier_definition: str = "HIERARCHICAL",
    subnetwork_controller_type: str = "SOURCE",
    alias: str | None = None,
) -> dict:
    """Add a domain network (Electric, Water, Gas, …).

    ``HIERARCHICAL`` suits networks with nested tiers (transmission ->
    distribution); ``PARTITIONED`` suits peer zones. ``SOURCE`` networks are fed
    from a source (electric, water); ``SINK`` networks drain to one (sewer,
    stormwater).
    """
    if tier_definition.upper() not in TIER_DEFINITIONS:
        return err(f"tier_definition must be one of {TIER_DEFINITIONS}.")
    if subnetwork_controller_type.upper() not in SUBNETWORK_CONTROLLER_TYPES:
        return err(f"subnetwork_controller_type must be one of {SUBNETWORK_CONTROLLER_TYPES}.")

    un = _un()
    un.AddDomainNetwork(
        utility_network, name, tier_definition.upper(),
        subnetwork_controller_type.upper(), alias,
    )
    cache.invalidate(utility_network)
    return ok(
        {
            "utility_network": str(utility_network),
            "domain_network": name,
            "tier_definition": tier_definition.upper(),
            "subnetwork_controller_type": subnetwork_controller_type.upper(),
        }
    )


@guard
def add_tier(
    utility_network: str,
    domain_network: str,
    name: str,
    rank: int,
    topology_type: str = "RADIAL",
    tier_group: str | None = None,
    subnetwork_field: str | None = None,
) -> dict:
    """Add a tier. Rank 1 is the highest (most upstream) tier.

    ``RADIAL`` = one path from the source (typical distribution); ``MESH`` =
    multiple paths (typical transmission).
    """
    un = _un()
    if topology_type.upper() not in TOPOLOGY_TYPES:
        return err(f"topology_type must be one of {TOPOLOGY_TYPES}.")

    # A HIERARCHICAL domain network refuses a tier with no group ("ERROR 002515:
    # Tier Group Name is required"), so create the group on demand. Verified
    # against ArcGIS Pro 3.x. PARTITIONED networks must NOT have one.
    created_group = None
    if tier_group:
        try:
            un.AddTierGroup(utility_network, domain_network, tier_group)
            created_group = tier_group
        except Exception:
            pass  # already exists — reusing it is the intent
    else:
        try:
            desc = ops._arcpy().Describe(utility_network)
            hierarchical = any(
                getattr(dn, "domainNetworkName", None) == domain_network
                and str(getattr(dn, "tierDefinition", "")).upper().startswith("HIER")
                for dn in getattr(desc, "domainNetworks", None) or []
            )
        except Exception:
            hierarchical = False
        if hierarchical:
            # One group PER TIER on purpose: tiers inside a single group must
            # all share the same topology type, so pooling them breaks the
            # moment a MESH transmission tier meets a RADIAL distribution tier
            # ("ERROR 002082: Invalid Tier Topology").
            tier_group = f"{name.replace(' ', '')}Group"
            try:
                un.AddTierGroup(utility_network, domain_network, tier_group)
                created_group = tier_group
            except Exception:
                pass

    # A HIERARCHICAL tier also demands a subnetwork field ("ERROR 002515:
    # Subnetwork Field Name is required"). Esri's own data model names it after
    # the tier, so do the same rather than failing on a detail the caller
    # should not have to know.
    if tier_group and not subnetwork_field:
        subnetwork_field = f"{name.replace(' ', '')}Subnetwork"

    un.AddTier(
        utility_network, domain_network, name, int(rank),
        topology_type.upper(), tier_group, subnetwork_field,
    )
    cache.invalidate(utility_network)
    return ok(
        {
            "domain_network": domain_network,
            "tier": name,
            "rank": int(rank),
            "topology_type": topology_type.upper(),
            "tier_group": tier_group,
            "tier_group_created": created_group,
            "subnetwork_field": subnetwork_field,
        }
    )


@guard
def add_terminal_configuration(
    utility_network: str,
    name: str,
    terminals: list[str],
    directional: bool = True,
    valid_paths: list | None = None,
    default_path: str | None = None,
) -> dict:
    """Define how a device's terminals connect (e.g. a transformer's HIGH/LOW).

    ``default_path`` is the literal ``"ALL"`` or ``"NONE"``, not a path name.
    """
    un = _un()
    if len(terminals) < 1:
        return err("Provide at least one terminal name.")

    # The terminal list goes into a DIFFERENT parameter depending on the
    # traversability model — there is no shared `terminals` argument.
    kwargs = {
        "in_utility_network": utility_network,
        "terminal_configuration_name": name,
        "traversability_model": "DIRECTIONAL" if directional else "BIDIRECTIONAL",
        "valid_paths": valid_paths,
        "default_path": default_path,
    }
    if directional:
        # ArcGIS wants [[name, is_upstream], ...] here and rejects bare names
        # ("ERROR 002024: A directional terminal configuration must contain at
        # least one upstream and one downstream terminal"). Accept either shape:
        # a list of names is paired as first=upstream, rest=downstream.
        rows = []
        for i, t in enumerate(terminals):
            if isinstance(t, (list, tuple)) and len(t) == 2:
                rows.append([t[0], bool(t[1])])
            else:
                rows.append([t, i == 0])
        if len(rows) < 2:
            return err(
                "A directional terminal configuration needs at least two "
                "terminals (one upstream, one downstream).",
                code="too_few_terminals",
            )
        kwargs["terminals_directional"] = rows
    else:
        kwargs["terminals_bidirectional"] = list(terminals)
    un.AddTerminalConfiguration(**kwargs)
    cache.invalidate(utility_network)
    return ok({"terminal_configuration": name, "terminals": terminals,
               "directional": directional})


@guard
def add_category(utility_network: str, name: str) -> dict:
    """Add a network category (used by traces as barriers/filters)."""
    un = _un()
    un.AddNetworkCategory(utility_network, name)
    cache.invalidate(utility_network)
    return ok({"category": name})


@guard
def add_network_attribute(
    utility_network: str,
    name: str,
    field_type: str = "LONG",
    inline: bool | None = None,
    apportionable: bool | None = None,
    domain: str | None = None,
    nullable: bool | None = None,
    substitution: bool | None = None,
) -> dict:
    """Add a network attribute (the values traces can filter and sum on).

    Every optional argument is omitted unless you set it. That is deliberate:
    ArcGIS rejects ``is_inline`` with "ERROR 000112: Domain does not exist" on a
    network that has no matching domain, even when no domain is supplied, so
    sending defaults we were never asked for breaks calls that would otherwise
    succeed.
    """
    un = _un()
    kwargs = {
        "in_utility_network": utility_network,
        "attribute_name": name,
        "attribute_type": field_type.upper(),
    }
    if inline is not None:
        kwargs["is_inline"] = "INLINE" if inline else "NOT_INLINE"
    if apportionable is not None:
        kwargs["is_apportionable"] = (
            "APPORTIONABLE" if apportionable else "NOT_APPORTIONABLE"
        )
    if domain:
        kwargs["domain"] = domain
    if nullable is not None:
        kwargs["is_nullable"] = "NULLABLE" if nullable else "NOT_NULLABLE"
    if substitution is not None:
        kwargs["is_substitution"] = (
            "SUBSTITUTION" if substitution else "NOT_SUBSTITUTION"
        )
    un.AddNetworkAttribute(**kwargs)
    cache.invalidate(utility_network)
    return ok({"network_attribute": name, "type": field_type.upper()})


@guard
def add_rule(
    utility_network: str,
    rule_type: str,
    from_class: str,
    from_assetgroup: str,
    from_assettype: str,
    to_class: str,
    to_assetgroup: str,
    to_assettype: str,
    from_terminal: str | None = None,
    to_terminal: str | None = None,
) -> dict:
    """Add a connectivity/containment/attachment rule.

    Rules are what make a utility network refuse invalid edits — without them any
    asset can connect to any other, and the model is decorative.
    """
    if rule_type.upper() not in RULE_TYPES:
        return err(f"rule_type must be one of {RULE_TYPES}.", valid=RULE_TYPES)
    un = _un()
    un.AddRule(
        in_utility_network=utility_network,
        rule_type=rule_type.upper(),
        from_class=from_class,
        from_assetgroup=from_assetgroup,
        from_assettype=from_assettype,
        to_class=to_class,
        to_assetgroup=to_assetgroup,
        to_assettype=to_assettype,
        from_terminal=from_terminal,
        to_terminal=to_terminal,
    )
    cache.invalidate(utility_network)
    return ok(
        {
            "rule_type": rule_type.upper(),
            "from": f"{from_class}/{from_assetgroup}/{from_assettype}",
            "to": f"{to_class}/{to_assetgroup}/{to_assettype}",
        }
    )


@guard
def set_subnetwork_definition(
    utility_network: str,
    domain_network: str,
    tier: str,
    subnetwork_controller_type: str = "SOURCE",
    valid_devices: list[str] | None = None,
    valid_lines: list[str] | None = None,
    aggregated_line: list[str] | None = None,
    diagram_template: str | None = None,
    include_barriers: bool = True,
    traversability_scope: str = "BOTH_JUNCTIONS_AND_EDGES",
) -> dict:
    """Define how a tier's subnetworks are discovered and what they aggregate.

    Required before ``update_subnetwork`` will produce anything useful.
    """
    un = _un()
    un.SetSubnetworkDefinition(
        in_utility_network=utility_network,
        domain_network=domain_network,
        tier=tier,
        support_disjoint_subnetwork=None,
        valid_devices=valid_devices,
        valid_lines=valid_lines,
        aggregated_line=aggregated_line,
        diagram_template=diagram_template,
        include_barriers="INCLUDE_BARRIERS" if include_barriers else "EXCLUDE_BARRIERS",
        traversability_scope=traversability_scope,
    )
    cache.invalidate(utility_network)
    return ok({"domain_network": domain_network, "tier": tier, "definition_set": True})


# --- Topology / validation --------------------------------------------------

@guard
def enable_topology(
    utility_network: str,
    max_error_count: int = 10000,
    only_generate_errors: bool = False,
) -> dict:
    """Enable the network topology. Required before any trace."""
    un = _un()
    result = un.EnableNetworkTopology(
        utility_network, max_error_count,
        "ONLY_ERRORS" if only_generate_errors else "ENABLE_TOPO",
    )
    return ok({"utility_network": str(utility_network), "topology": "enabled",
               "messages": _messages(result)})


@guard
def disable_topology(utility_network: str) -> dict:
    """Disable the topology. REQUIRED before most schema changes."""
    un = _un()
    result = un.DisableNetworkTopology(utility_network)
    return ok({"utility_network": str(utility_network), "topology": "disabled",
               "messages": _messages(result)})


@guard
def validate_topology(utility_network: str, extent: str | None = None) -> dict:
    """Validate dirty areas so edits become traceable."""
    un = _un()
    result = un.ValidateNetworkTopology(utility_network, extent)
    return ok({"utility_network": str(utility_network), "validated": True,
               "extent": extent, "messages": _messages(result)})


@guard
def verify_topology(utility_network: str, out_log_file: str | None = None) -> dict:
    """Deep consistency check between the topology and the feature data.

    Signature verified against ArcGIS Pro 3.x: the tool takes only the network
    and an optional log file — there is no consistency flag or error cap.
    """
    un = _un()
    result = un.VerifyNetworkTopology(
        in_utility_network=utility_network, out_log_file=out_log_file
    )
    return ok({"utility_network": str(utility_network), "verified": True,
               "log_file": out_log_file, "messages": _messages(result)})


@guard
def repair_topology(utility_network: str, extent: str | None = None) -> dict:
    """Repair inconsistencies found by :func:`verify_topology`."""
    un = _un()
    result = un.RepairNetworkTopology(utility_network, extent)
    return ok({"utility_network": str(utility_network), "repaired": True,
               "messages": _messages(result)})


@guard
def analyze(utility_network: str, out_json: str | None = None) -> dict:
    """Analyze the network data for modelling problems."""
    un = _un()
    result = un.AnalyzeNetworkData(utility_network, out_json)
    return ok({"utility_network": str(utility_network), "report": out_json,
               "messages": _messages(result)})


# --- Operate ---------------------------------------------------------------

@guard
def add_trace_locations(
    utility_network: str,
    out_feature_class: str,
    from_selection: bool = True,
    clear_existing: bool = True,
    as_barrier: bool = False,
) -> dict:
    """Build the starting-points / barriers table a trace actually needs.

    A trace will NOT accept a plain copy of your features — it requires a
    purpose-built table carrying FEATUREGLOBALID and TERMINALID ("ERROR 001911:
    Invalid tracing locations schema"). This creates that table from the current
    map selection, and its output is what you pass to
    :func:`trace` as ``starting_points`` (or ``barriers``).
    """
    un = _un()
    un.AddTraceLocations(
        in_utility_network=utility_network,
        out_feature_class=out_feature_class,
        load_selected_features=(
            "LOAD_SELECTED_FEATURES" if from_selection else "DO_NOT_LOAD_SELECTED_FEATURES"
        ),
        clear_trace_locations="CLEAR_LOCATIONS" if clear_existing else "KEEP_LOCATIONS",
        filter_barrier="FILTER_BARRIER" if as_barrier else "TRAVERSABILITY_BARRIER",
    )
    payload = {"utility_network": str(utility_network), "output": out_feature_class}
    try:
        arcpy = ops._arcpy()
        if arcpy.Exists(out_feature_class):
            payload["locations"] = int(arcpy.management.GetCount(out_feature_class)[0])
    except Exception:  # noqa: BLE001
        pass
    return ok(payload)


@guard
def trace(
    utility_network: str,
    trace_type: str,
    starting_points: str | None = None,
    barriers: str | None = None,
    domain_network: str | None = None,
    tier: str | None = None,
    target_tier: str | None = None,
    subnetwork_name: str | None = None,
    shortest_path_attribute: str | None = None,
    include_containers: bool = False,
    include_content: bool = False,
    include_structures: bool = False,
    include_barriers: bool = True,
    validate_consistency: bool = True,
    condition_barriers: list | None = None,
    function_barriers: list | None = None,
    functions: list | None = None,
    result_types: list[str] | None = None,
    out_points: str | None = None,
    out_lines: str | None = None,
    out_polygons: str | None = None,
    out_json: str | None = None,
    selection_type: str = "NEW_SELECTION",
    stopping_points: str | None = None,
) -> dict:
    """Run any of the 10 utility-network trace types.

    Results come back in whatever form you ask for via ``result_types``:

    * ``SELECTION`` (the default) selects the traced features in the map — the
      fastest option, and what you usually want interactively.
    * ``AGGREGATED_GEOMETRY`` writes the traced geometry to the ``out_points`` /
      ``out_lines`` / ``out_polygons`` feature classes you name.
    * ``CONNECTIVITY`` / ``ELEMENTS`` return the traced elements as JSON via
      ``out_json``.

    Preconditions the geodatabase enforces (and this reports clearly): the
    topology must be enabled and validated, and SUBNETWORK traces additionally
    need a subnetwork definition plus an updated subnetwork.
    """
    tt = trace_type.strip().upper()
    if tt not in TRACE_TYPES:
        return err(f"trace_type must be one of {TRACE_TYPES}.", valid=TRACE_TYPES)
    if tt == "SHORTEST_PATH" and not shortest_path_attribute:
        return err(
            "A SHORTEST_PATH trace needs shortest_path_attribute (the network "
            "attribute to minimise, e.g. 'Shape length').",
            code="missing_attribute",
        )
    if tt in {"SUBNETWORK", "SUBNETWORK_CONTROLLERS"} and not (domain_network and tier):
        return err(
            f"A {tt} trace needs domain_network and tier.", code="missing_tier"
        )

    types = [t.strip().upper() for t in (result_types or ["SELECTION"])]
    if "AGGREGATED_GEOMETRY" in types and not any((out_points, out_lines, out_polygons)):
        return err(
            "result_types includes AGGREGATED_GEOMETRY, so name at least one of "
            "out_points / out_lines / out_polygons for the geometry to go to.",
            code="missing_output",
        )

    un = _un()
    kwargs: dict[str, Any] = {
        "in_utility_network": utility_network,
        "trace_type": tt,
        "starting_points": starting_points,
        "barriers": barriers,
        "domain_network": domain_network,
        "tier": tier,
        "target_tier": target_tier,
        "subnetwork_name": subnetwork_name,
        "shortest_path_network_attribute_name": shortest_path_attribute,
        "include_containers": "INCLUDE_CONTAINERS" if include_containers else "EXCLUDE_CONTAINERS",
        "include_content": "INCLUDE_CONTENT" if include_content else "EXCLUDE_CONTENT",
        "include_structures": "INCLUDE_STRUCTURES" if include_structures else "EXCLUDE_STRUCTURES",
        "include_barriers": "INCLUDE_BARRIERS" if include_barriers else "EXCLUDE_BARRIERS",
        "validate_consistency": (
            "VALIDATE_CONSISTENCY" if validate_consistency else "DO_NOT_VALIDATE_CONSISTENCY"
        ),
        "condition_barriers": condition_barriers,
        "function_barriers": function_barriers,
        "functions": functions,
        "result_types": types,
        "selection_type": selection_type,
    }
    # Only send the output parameters that were actually asked for — ArcGIS
    # validates them even when the matching result type is absent.
    if out_points:
        kwargs["aggregated_points"] = out_points
    if out_lines:
        kwargs["aggregated_lines"] = out_lines
    if out_polygons:
        kwargs["aggregated_polygons"] = out_polygons
    if out_json:
        kwargs["out_json_file"] = out_json
    if stopping_points:
        kwargs["stopping_points"] = stopping_points

    result = un.Trace(**kwargs)

    payload: dict[str, Any] = {
        "utility_network": str(utility_network),
        "trace_type": tt,
        "starting_points": starting_points,
        "domain_network": domain_network,
        "tier": tier,
        "result_types": types,
        "out_points": out_points,
        "out_lines": out_lines,
        "out_polygons": out_polygons,
        "out_json": out_json,
        "messages": _messages(result),
    }
    for label, path in (("points", out_points), ("lines", out_lines), ("polygons", out_polygons)):
        if not path:
            continue
        try:
            arcpy = ops._arcpy()
            if arcpy.Exists(path):
                payload[f"{label}_count"] = int(arcpy.management.GetCount(path)[0])
        except Exception:  # noqa: BLE001
            pass
    return ok(payload)


@guard
def update_subnetwork(
    utility_network: str,
    domain_network: str,
    tier: str,
    subnetwork_name: str | None = None,
    all_in_tier: bool = False,
    continue_on_failure: bool = False,
) -> dict:
    """Recompute subnetworks so the SUBNETWORK trace and subnetwork table are current."""
    un = _un()
    if not all_in_tier and not subnetwork_name:
        return err("Provide subnetwork_name, or set all_in_tier=True.")
    result = un.UpdateSubnetwork(
        in_utility_network=utility_network,
        domain_network=domain_network,
        tier=tier,
        all_subnetworks_in_tier=(
            "ALL_SUBNETWORKS_IN_TIER" if all_in_tier else "SPECIFIC_SUBNETWORK"
        ),
        subnetwork_name=subnetwork_name,
        continue_on_failure=(
            "CONTINUE_ON_FAILURE" if continue_on_failure else "STOP_ON_FAILURE"
        ),
    )
    return ok(
        {
            "domain_network": domain_network,
            "tier": tier,
            "subnetwork": subnetwork_name if not all_in_tier else "ALL",
            "messages": _messages(result),
        }
    )


@guard
def export_subnetwork(
    utility_network: str,
    domain_network: str,
    tier: str,
    subnetwork_name: str,
    out_json: str,
    acknowledge: bool = False,
    include_geometry: bool = False,
    include_domain_descriptions: bool = True,
) -> dict:
    """Export a subnetwork to JSON (the hand-off format for downstream systems)."""
    un = _un()
    result = un.ExportSubnetwork(
        in_utility_network=utility_network,
        domain_network=domain_network,
        tier=tier,
        subnetwork_name=subnetwork_name,
        export_acknowledged="ACKNOWLEDGE" if acknowledge else "NO_ACKNOWLEDGE",
        out_json_file=out_json,
        include_geometry="INCLUDE_GEOMETRY" if include_geometry else "EXCLUDE_GEOMETRY",
        include_domain_descriptions=(
            "INCLUDE_DOMAIN_DESCRIPTIONS" if include_domain_descriptions
            else "EXCLUDE_DOMAIN_DESCRIPTIONS"
        ),
    )
    return ok(
        {
            "subnetwork": subnetwork_name,
            "domain_network": domain_network,
            "tier": tier,
            "output": out_json,
            "messages": _messages(result),
        }
    )


@guard
def export_rules(utility_network: str, out_csv: str, rule_type: str = "ALL") -> dict:
    """Export connectivity rules to CSV (review, diff, or replay elsewhere)."""
    un = _un()
    un.ExportRules(utility_network, rule_type, out_csv)
    return ok({"utility_network": str(utility_network), "output": out_csv})


@guard
def import_rules(utility_network: str, csv_file: str, rule_type: str = "ALL") -> dict:
    """Bulk-import connectivity rules from CSV."""
    un = _un()
    un.ImportRules(utility_network, rule_type, csv_file)
    cache.invalidate(utility_network)
    return ok({"utility_network": str(utility_network), "imported_from": csv_file})


# --- Declarative build ------------------------------------------------------

@guard
def build(feature_dataset: str, spec: dict, enable: bool = True) -> dict:
    """Author a whole utility network from ONE declarative spec.

    Handles the ordering the geodatabase demands — create, then domain networks,
    then tiers/terminals/categories/attributes, then rules, and only then enable
    the topology. Getting that order wrong is the most common way a hand-built
    network fails.

    ``spec``::

        {"name": "Water", "service_territory": "ServiceTerritory",
         "domain_networks": [
            {"name": "Water", "tier_definition": "HIERARCHICAL",
             "subnetwork_controller_type": "SOURCE",
             "tiers": [{"name": "Distribution", "rank": 1, "topology_type": "RADIAL"}]}
         ],
         "categories": ["Isolating"],
         "network_attributes": [{"name": "Lifecycle", "field_type": "LONG"}],
         "terminal_configurations": [{"name": "HighLow", "terminals": ["High","Low"]}],
         "rules": [...]}
    """
    steps: list[dict] = []

    def record(action: str, target: str, fn) -> bool:
        try:
            envelope = fn()
            good = bool(envelope.get("ok"))
            entry = {"action": action, "target": target, "ok": good}
            if not good:
                entry["error"] = envelope.get("error")
            steps.append(entry)
            return good
        except Exception as exc:  # noqa: BLE001
            steps.append({"action": action, "target": target, "ok": False, "error": str(exc)})
            return False

    name = spec.get("name")
    territory = spec.get("service_territory")
    if not name or not territory:
        return err("spec needs 'name' and 'service_territory'.")

    created = create(feature_dataset, name, territory, spec.get("version", "CURRENT"))
    steps.append({"action": "create", "target": name, "ok": created.get("ok"),
                  **({} if created.get("ok") else {"error": created.get("error")})})
    if not created.get("ok"):
        return _build_result(steps, None)
    un_path = created["data"]["utility_network"]

    for dn in spec.get("domain_networks", []) or []:
        if not record("add_domain_network", dn.get("name", "?"),
                      lambda dn=dn: add_domain_network(
                          un_path, dn["name"],
                          dn.get("tier_definition", "HIERARCHICAL"),
                          dn.get("subnetwork_controller_type", "SOURCE"),
                          dn.get("alias"))):
            continue
        for tier in dn.get("tiers", []) or []:
            record("add_tier", f"{dn['name']}/{tier.get('name')}",
                   lambda tier=tier, dn=dn: add_tier(
                       un_path, dn["name"], tier["name"], tier["rank"],
                       tier.get("topology_type", "RADIAL"),
                       tier.get("tier_group"), tier.get("subnetwork_field")))

    for cat in spec.get("categories", []) or []:
        record("add_category", cat, lambda cat=cat: add_category(un_path, cat))

    for attr in spec.get("network_attributes", []) or []:
        record("add_network_attribute", attr.get("name", "?"),
               lambda attr=attr: add_network_attribute(
                   un_path, attr["name"], attr.get("field_type", "LONG"),
                   attr.get("inline", True), attr.get("apportionable", False),
                   attr.get("domain"), attr.get("bitset", False)))

    for tc in spec.get("terminal_configurations", []) or []:
        record("add_terminal_configuration", tc.get("name", "?"),
               lambda tc=tc: add_terminal_configuration(
                   un_path, tc["name"], tc["terminals"],
                   tc.get("directional", True), tc.get("valid_paths"),
                   tc.get("default_path")))

    for rule in spec.get("rules", []) or []:
        record("add_rule", rule.get("rule_type", "?"),
               lambda rule=rule: add_rule(
                   un_path, rule["rule_type"],
                   rule["from_class"], rule["from_assetgroup"], rule["from_assettype"],
                   rule["to_class"], rule["to_assetgroup"], rule["to_assettype"],
                   rule.get("from_terminal"), rule.get("to_terminal")))

    if enable:
        record("enable_topology", name, lambda: enable_topology(un_path))

    return _build_result(steps, un_path)


def _build_result(steps: list[dict], un_path: str | None) -> dict:
    applied = sum(1 for s in steps if s.get("ok"))
    payload = {
        "utility_network": un_path,
        "steps": steps,
        "applied": applied,
        "attempted": len(steps),
    }
    if applied == len(steps):
        return ok(payload)
    return {"ok": False, "error": f"{len(steps) - applied} build step(s) failed.",
            "data": payload}


def _messages(result: Any) -> str | None:
    try:
        return result.getMessages()
    except Exception:  # noqa: BLE001
        return None
