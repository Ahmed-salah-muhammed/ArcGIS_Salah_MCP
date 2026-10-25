# Migration and cloning

1. Open both connections: `portal_connect(name="source", ...)`, `portal_connect(name="target", ...)`
   (same org into another folder: use `default` for both).
2. Run `portal_apps action=dependency_graph` on the items to see what comes along.
3. `portal_clone(item_ids=[...], connection="source", target_connection="target", folder="Migrated")` returns a
   preview (items, what they use); call again with confirm=True.
   - `search_existing_items=True` (default) reuses items cloned earlier instead of duplicating them.
   - `copy_data=False` clones feature services empty (schema only).
   - `item_mapping={source_id: existing_target_id}` points clones at items that already exist in the target.
4. Verify with `portal_search owner:me` / `portal_item action=get` in the target.

Single item, same org: `portal_item action=copy` (title, folder).
