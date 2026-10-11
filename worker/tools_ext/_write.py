"""The one write path for Wave 1 department verbs (INTERFACES_W1.md §5).

A department write verb SOLVES its arguments into apply_edit_batch
operations (fx items, a motion item's ``composite``) and commits them here,
through ``edit_batch.apply_batch`` — the very function a stale MCP client's
``apply_edit_batch`` runs in the backend. Validation is therefore identical
by construction: the field constraints and ``check`` of the fx_schemas
member, the media-key policing, the device-card gate
(``edit_batch.check_fx_devices``) and then every ToolContext write gate
(music, web sounds, scope guard, ledger line).

A verb never validates an item more strictly than this path does: anything
a verb refuses, a batch must refuse too, so the rule belongs in the
fx_schemas member, not the verb.
"""


def upsert_fx(item):
    """The apply_edit_batch operation that adds or replaces one fx item."""
    return {"action": "upsert", "layer": "fx", "id": item["id"],
            "value": dict(item)}


def remove_fx(item_id):
    """The operation that removes one fx item."""
    return {"action": "remove", "layer": "fx", "id": str(item_id)}


def set_composite(motion_id, composite):
    """The operation that sets (or, with None, clears) one motion item's
    ``composite`` (track 1C's slot)."""
    return {"action": "upsert", "layer": "motion", "id": str(motion_id),
            "value": {"id": str(motion_id), "composite": composite}}


def commit(ctx, operations, summary):
    """Apply ``operations`` to the latest EDL exactly as apply_edit_batch
    does and write it. Returns ToolContext.write_edl's line (``EDL vX ->
    vY: ...`` with the ledger note) or a ``REJECTED`` line naming the
    rule."""
    import edit_batch
    row = ctx.latest_edl()
    try:
        keys = ctx.db.run(edit_batch.project_media_keys, ctx.project_id)
        updated = edit_batch.apply_batch(row["json"], operations,
                                         ctx.duration or None, keys)
    except (ValueError, TypeError) as exc:
        return f"REJECTED (EDL v{row['version']} unchanged): {exc}"
    return ctx.write_edl(updated, summary)
