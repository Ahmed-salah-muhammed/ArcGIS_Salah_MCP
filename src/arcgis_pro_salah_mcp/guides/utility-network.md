# Utility Network (pro_un_*)

- `pro_un_describe what=model` - domain networks, tiers, terminals, network attributes, categories in one read;
  `what=trace_types` explains the 10 trace types (UPSTREAM = what feeds this, ISOLATION = which valves to close).
- `pro_un_author action=build spec={...}` authors a whole network in the order the geodatabase demands
  (create -> domain networks -> tiers -> rules -> enable topology); the other actions add one piece.
- `pro_un_topology action=enable|disable|validate|verify|repair|analyze` - topology must be DISABLED to change
  the schema and ENABLED + validated to trace.
- `pro_un_trace action=add_locations` builds the starting-points table (a plain feature-class copy is rejected
  with ERROR 001911); then `pro_un_trace trace_type=...`.
- `pro_un_subnetwork action=update|export`.
- Symbology: `pro_symbology kind=water_utility` applies the AM/FM water look in one call.
