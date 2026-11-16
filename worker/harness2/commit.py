"""The v2 write path: stage, repair, partial apply, one version, one envelope.

``edit`` reads the head, applies every op to a staged copy (harness2.ops),
and writes ONE version with ``db.insert_edl`` plus its change manifest. It
never calls ``ToolContext.write_edl``, so none of v1's guard chain runs.
The same ``operation_id`` returns the first result (idempotency, not a
refusal). A head that moved while the ops ran is a rebase: the ops are
declarative, so they are applied again on the new head.
"""
import copy
import json
import threading

import db as dbx
import edl_diff
import quality_verifier
from agent_tools import edl_signature

from harness2 import measure as measure_mod
from harness2 import ops as ops_mod
from harness2.envelope import Envelope
from harness2.stage import Stage

CREATED_BY = "agent"
MUTATION_TOOL = "harness2.edit"
_RECEIPTS = {}
_RECEIPTS_LOCK = threading.Lock()
_RECEIPTS_MAX = 2048


class EditEnv(Envelope):
    def __init__(self, tool="edit"):
        super().__init__(tool=tool)
        self.already = []
        self.measures = {}
        self.refusals = []
        self.engine_errors = []


# --------------------------------------------------------------------------
# operation_id receipts: in process, and durable on the change manifest

def _receipt_key(ctx, operation_id):
    return (int(ctx.project_id), str(operation_id))


def _stored_receipt(conn, project_id, operation_id):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.change_manifests') AS t")
        if not (cur.fetchone() or {}).get("t"):
            return None
        cur.execute("""SELECT edl_version, manifest->'v2_envelope' AS env
                       FROM change_manifests
                       WHERE project_id = %s
                         AND manifest->>'operation_id' = %s
                       ORDER BY edl_version DESC LIMIT 1""",
                    (int(project_id), str(operation_id)))
        return cur.fetchone()


def _replay(ctx, operation_id):
    with _RECEIPTS_LOCK:
        hit = _RECEIPTS.get(_receipt_key(ctx, operation_id))
    if hit is not None:
        return copy.deepcopy(hit)
    try:
        row = ctx.db.run(_stored_receipt, ctx.project_id, operation_id)
    except Exception:  # noqa: BLE001 - no receipt read means run it
        row = None
    if not row or not row.get("env"):
        return None
    data = row["env"] if isinstance(row["env"], dict) else json.loads(
        row["env"])
    return envelope_from(data)


def _remember(ctx, operation_id, env):
    if not operation_id:
        return
    with _RECEIPTS_LOCK:
        if len(_RECEIPTS) >= _RECEIPTS_MAX:
            _RECEIPTS.pop(next(iter(_RECEIPTS)))
        _RECEIPTS[_receipt_key(ctx, operation_id)] = copy.deepcopy(env)


def envelope_from(data):
    env = EditEnv(tool=data.get("tool") or "edit")
    env.changed = data.get("changed") or ""
    env.measured = data.get("measured") or {}
    env.repairs = list(data.get("repairs") or [])
    env.not_applied = list(data.get("not_applied") or [])
    env.findings = list(data.get("findings") or [])
    env.check = list(data.get("check") or [])
    env.undo_to = data.get("undo_to")
    env.version = data.get("version")
    env.applied = 1 if env.changed else 0
    env.finding("the same operation_id was already applied: this is its "
                "first result")
    return env


# --------------------------------------------------------------------------

def _source_s(ctx):
    try:
        return float(getattr(ctx, "duration", 0.0) or 0.0) or None
    except (TypeError, ValueError):
        return None


def _stale_base_line(ctx, base_version, head):
    try:
        base = ctx.db.run(dbx.get_edl_version, ctx.project_id,
                          int(base_version))
    except Exception:  # noqa: BLE001
        base = None
    if not base:
        return (f"base v{base_version} was not found; the ops were applied "
                f"on the head v{head['version']}")
    line = measure_mod.change_line(base["json"], head["json"],
                                   base_version, head["version"])
    return (f"base v{base_version} is behind the head v{head['version']} "
            f"(another writer: {line.split(': ', 1)[-1]}); the ops were "
            f"applied on v{head['version']}")


def write_version(ctx, prev, new_edl, env, note, operation_id=None,
                  tool=MUTATION_TOOL):
    """Insert one version, its manifest and the envelope's facts."""
    job = getattr(ctx, "job", None) or {}
    version = ctx.db.run(
        dbx.insert_edl, ctx.project_id, new_edl, CREATED_BY, job.get("id"),
        tool, prev["version"], job.get("total_claims"))
    try:
        ctx.versions_written.append(version)
    except AttributeError:
        pass
    chg = edl_diff.change_ranges(prev["json"], new_edl)
    env.version = version
    env.undo_to = prev["version"]
    env.changed = measure_mod.change_line(prev["json"], new_edl,
                                          prev["version"], version)
    env.measured = measure_mod.edl_measure(new_edl, _source_s(ctx))
    try:
        manifest = quality_verifier.build_change_manifest(
            ctx.project_id, version, prev["json"], new_edl, chg,
            description=str(note or env.changed)[:300], tool=tool)
    except Exception as exc:  # noqa: BLE001 - the version is already durable
        manifest = {"description": str(note or "")[:300],
                    "manifest_error": str(exc)[:200]}
    manifest["harness"] = "v2"
    if operation_id:
        manifest["operation_id"] = str(operation_id)
    manifest["v2_envelope"] = env.to_dict()
    try:
        ctx.change_manifests[version] = manifest
    except (AttributeError, TypeError):
        pass
    try:
        ctx.db.run(dbx.upsert_change_manifest, ctx.project_id, version,
                   manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"[harness2] manifest persist deferred: {exc}", flush=True)
    try:
        ctx.last_change = (dict(chg, edl_version=version, manifest=manifest)
                           if chg else {"edl_version": version,
                                        "manifest": manifest})
    except Exception:  # noqa: BLE001
        pass
    return version


def _checks(env, prev_edl, new_edl):
    out = []
    if (prev_edl.get("keep") or []) != (new_edl.get("keep") or []):
        out.append(f"look(show='removed', version={env.version})")
        out.append(f"look(target='program', show='junctions', "
                   f"version={env.version})")
    out.append("render(scope='changes')")
    return out


def edit(ctx, ops, note="", base_version=None, operation_id=None):
    """Apply ``ops`` and write one version. Returns the envelope."""
    if operation_id:
        prior = _replay(ctx, operation_id)
        if prior is not None:
            return prior
    env = EditEnv()
    if not isinstance(ops, list):
        ops = [ops] if isinstance(ops, dict) else []
    if not ops:
        env.skip(0, "edit takes ops=[{...}, ...]",
                 "timeline(schema='ops') lists every op with an example")
        return env
    for attempt in range(3):
        head = ctx.latest_edl()
        if attempt == 0 and base_version is not None and \
                int(base_version) != int(head["version"]):
            env.repair(_stale_base_line(ctx, base_version, head))
        stage = Stage(ctx, head["json"], head["version"])
        env.applied = ops_mod.apply(stage, copy.deepcopy(ops), env)
        new_edl = stage.edl
        if edl_signature(new_edl) == edl_signature(head["json"]):
            env.version = head["version"]
            if env.applied or not env.not_applied:
                env.changed = f"already so: v{head['version']} unchanged"
            env.measured = measure_mod.edl_measure(head["json"],
                                                   _source_s(ctx))
            env.measured.update(env.measures)
            env.findings.extend(env.already[:12])
            if env.refusals:
                _ticket_refusals(ctx, env)
            _remember(ctx, operation_id, env)
            return env
        try:
            write_version(ctx, head, new_edl, env, note, operation_id)
        except ValueError as exc:
            # The head moved under us (insert_edl's before_version check):
            # rebase, i.e. apply the same declarative ops on the new head.
            if "Edit version changed" not in str(exc) or attempt == 2:
                raise
            env = EditEnv()
            env.repair("another writer saved a version while this edit "
                       "ran; the ops were applied again on the new head")
            continue
        break
    env.check = _checks(env, head["json"], new_edl)
    env.measured.update(env.measures)
    env.findings.extend(env.already[:12])
    if env.refusals:
        _ticket_refusals(ctx, env)
    _remember(ctx, operation_id, env)
    return env


def _ticket_refusals(ctx, env):
    """A verb's REJECTED reached through v2 is a fixer's ticket, never the
    model's wall (H32). Filed best-effort through the ops intake table."""
    try:
        from harness2 import tickets
        tickets.file_refusals(ctx, env.refusals)
    except Exception as exc:  # noqa: BLE001
        print(f"[harness2] refusal ticket not filed: {exc}", flush=True)


def undo(ctx, to=None, operation_id=None):
    """Restore version ``to`` (default: the one before the head) as a new
    version. History only grows."""
    if operation_id:
        prior = _replay(ctx, operation_id)
        if prior is not None:
            return prior
    env = EditEnv(tool="undo")
    head = ctx.latest_edl()
    target_v = int(to) if to is not None else int(head["version"]) - 1
    target = None
    if target_v >= 1:
        try:
            target = ctx.db.run(dbx.get_edl_version, ctx.project_id,
                                target_v)
        except Exception:  # noqa: BLE001
            target = None
    if not target:
        env.skip(0, f"there is no version {target_v}",
                 f"versions 1-{head['version']}: timeline(versions=true)")
        return env
    if edl_signature(target["json"]) == edl_signature(head["json"]):
        env.version = head["version"]
        env.changed = (f"already so: v{head['version']} is the same edit as "
                       f"v{target_v}")
        env.applied = 1
        return env
    from harness2 import repair as repair_mod
    normalized, notes, error = repair_mod.repair(
        target["json"], getattr(ctx, "duration", None), before=head["json"])
    for line in notes:
        env.repair(line)
    if normalized is None:
        env.skip(0, f"v{target_v} no longer fits this source: {error[:300]}",
                 "keep/cut ops rebuild the same cut on this source")
        return env
    env.applied = 1
    write_version(ctx, head, normalized, env, f"undo to v{target_v}",
                  operation_id, tool="harness2.undo")
    env.changed = env.changed.replace(
        f"v{head['version']} -> v{env.version}:",
        f"v{head['version']} -> v{env.version} (a copy of v{target_v}):", 1)
    env.check = _checks(env, head["json"], normalized)
    _remember(ctx, operation_id, env)
    return env
