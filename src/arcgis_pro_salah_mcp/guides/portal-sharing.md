# Sharing

`portal_share(item_ids=[...], level=..., add_groups=[...], remove_groups=[...])`

- `level`: `private` | `org` | `everyone`. **everyone (public) needs confirm=True** - ask the user first.
- Groups by id or exact title; an ambiguous title is refused with the candidate ids.
- Uses `item.sharing` (`sharing_level`, `sharing.groups.add/remove`) - the 2.4 API; `item.share()` is deprecated.
- Every item is re-read afterwards; the result shows before/after per item and `all_ok`.
- Shared-update groups (capability `updateitemcontrol`) let members edit each other's items; the flag can only
  be set when the group is created (`portal_groups action=create shared_update=true`, needs the
  `portal:admin:createUpdateCapableGroup` privilege).
- Sharing a view: share the VIEW item, not its source, when the public / a group should only see the filtered data.
