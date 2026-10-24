"""The render cache's rules are the backend's too (config.RENDER_RULE_CONSTANTS).

74ffcc0e fixed repaint patches running one frame late and stamped `patch_v`;
this cache busts every pre-fix render of an EDL with a patch. The backend's
final gate mirrored only outro_v, trans_v and wm_v, and the studio and
export_final short-circuit to an existing final, so this cache was never
asked: export_final on Gates s23 (project 4356, v36) returned the pre-fix
final (asset 31550) with its orphan frame.

Now every render stamps `rules` (renderer.render_rules) and every render
job's answer carries them; the backend learns the deployed rules from the
newest renders and hides a final no worker on those rules has seen, so a
Download asks this cache. These tests fail when a version the cache check
reads is missing from the rules — the bump the backend would never hear of.

    cd worker && PYTHONPATH=.. python -m pytest tests/test_render_rules.py -q
"""
import ast
import hashlib
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                 # noqa: E402
import renderer                                               # noqa: E402

_TREE = ast.parse(inspect.getsource(renderer))
_FNS = {n.name: n for n in _TREE.body if isinstance(n, ast.FunctionDef)}


def _reachable(root):
    """renderer functions the predicate `root` calls, transitively."""
    seen, stack = set(), [root]
    while stack:
        name = stack.pop()
        if name in seen or name not in _FNS:
            continue
        seen.add(name)
        for node in ast.walk(_FNS[name]):
            if isinstance(node, ast.Name) and node.id in _FNS:
                stack.append(node.id)
    return seen


def _config_versions(names):
    found = {}
    for name in names:
        for node in ast.walk(_FNS[name]):
            if isinstance(node, ast.Attribute) \
                    and isinstance(node.value, ast.Name) \
                    and node.value.id == "config" \
                    and node.attr.endswith("_VERSION"):
                found.setdefault(node.attr, name)
    return found


def test_every_version_the_cache_check_reads_is_a_rule_the_backend_hears():
    reads = _config_versions(_reachable("cache_rules_current"))
    missing = {v: fn for v, fn in reads.items()
               if v not in config.RENDER_RULE_CONSTANTS}
    assert not missing, (
        "the render cache reads these versions, but config."
        "RENDER_RULE_CONSTANTS does not list them, so the backend's final "
        "gate never hears they moved and stale finals stay downloadable: "
        f"{missing}")
    # The rules the walk must find today, so a broken walk cannot pass.
    for v in ("PATCH_CLOCK_VERSION", "CARD_LAYOUT_VERSION",
              "TRUE_PEAK_GUARD_VERSION", "OUTRO_VERSION", "WATERMARK_VERSION",
              "HANDOFF_VERSION", "FINISH_VERSION", "DELIVERY_VERSION"):
        assert v in reads, v


def _bare_number_stamps(fn):
    """Comparisons of a `.get("<x>_v")` stamp with an integer literal."""
    bad = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left] + list(node.comparators)
        stamp = any(isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "get" and c.args
                    and isinstance(c.args[0], ast.Constant)
                    and str(c.args[0].value).endswith("_v")
                    for side in sides for c in ast.walk(side))
        number = any(isinstance(side, ast.Constant)
                     and type(side.value) is int for side in sides)
        if stamp and number:
            bad.append(ast.unparse(node))
    return bad


def test_no_stamp_is_compared_with_a_bare_number():
    """`meta.get("delivery_v") == 1` is a version no list can see."""
    probe = ast.parse('def f(meta):\n'
                      '    return (meta or {}).get("delivery_v") == 1\n')
    assert _bare_number_stamps(probe.body[0])           # the check bites
    bad = {name: _bare_number_stamps(_FNS[name])
           for name in _reachable("cache_rules_current")}
    assert not {k: v for k, v in bad.items() if v}, bad


def test_every_rule_is_a_real_integer_version():
    rules = renderer.render_rules()
    assert list(rules) == list(config.RENDER_RULE_CONSTANTS)
    assert len(set(config.RENDER_RULE_CONSTANTS)) == \
        len(config.RENDER_RULE_CONSTANTS)
    for name, value in rules.items():
        assert type(value) is int and value >= 1, name


# The literals renderer._caption_index_fp mixes into a caption fingerprint.
# The cache compares fingerprints itself; the backend hears a change only
# through CAPTION_FP_VERSION. Changed the fingerprint? Bump
# config.CAPTION_FP_VERSION and record the new digest and version here.
CAPTION_FP_LITERALS = ("bd1a6dda54558a42", 1)


def test_a_caption_fingerprint_change_bumps_its_rule():
    fn = _FNS["_caption_index_fp"]
    doc = ast.get_docstring(fn, clean=False)
    lits = sorted({repr(n.value) for n in ast.walk(fn)
                   if isinstance(n, ast.Constant)
                   and isinstance(n.value, (str, bytes)) and n.value != doc})
    digest = hashlib.sha256("\n".join(lits).encode()).hexdigest()[:16]
    assert (digest, config.CAPTION_FP_VERSION) == CAPTION_FP_LITERALS, (
        "renderer._caption_index_fp changed: bump config.CAPTION_FP_VERSION "
        f"and set CAPTION_FP_LITERALS = ({digest!r}, <new version>)")


def test_every_render_and_every_answer_carries_the_rules():
    src = inspect.getsource(renderer._run_render_job)
    # The asset's stamp, the cached answer, the fresh answer.
    assert src.count('"rules": render_rules()') == 3
    assert "cache_rules_current(cached, edl_row[\"json\"], variant" in src


def test_the_backend_reads_the_rules_from_renders_and_answers():
    bev = open(os.path.join(os.path.dirname(__file__),
                            "../../backend/routes/video.py")).read()
    assert "meta -> 'rules' AS rules" in bev            # the deployed rules
    assert "result -> 'rules' AS rules" in bev          # each answer's rules
    gate = bev[bev.index("def _final_gate"):bev.index("def final_degraded_hold")]
    assert "_deployed_render_rules(cur)" in gate
    assert "_rules_vouched(meta, answer, deployed)" in gate


# ── the Gates s23 shape, through the one predicate ──────────────────────────

def _s23(**extra):
    """Asset 31550's stamps (project 4356, v36): everything current except
    the repaint-patch clock it predates."""
    meta = {"outro_v": (config.OUTRO_VERSION
                        if renderer.outro_seconds(False) else 0),
            "gfx_shape_v": 1, "delivery_v": 1, "trans_v": 4, "cam_v": 2,
            "handoff_v": 1, "follow_v": 1, "card_v": 3, "clock_v": 1,
            "cap_v": 3, "tail_v": 2, "legib_v": 5, "carry_v": 5, "look_v": 1,
            "finish_v": 1, "audio_peak_v": 1, "master_v": 1,
            "wm_v": renderer.watermark_version("final", False, None),
            "wm_p": "frame", "matte_status": "ready", "variant": "final",
            "edl_version": 36}
    meta.update(extra)
    return {"meta": meta, "width": 1080, "height": 1920}


_PATCHED = {"keep": [[340.0, 352.0]],
            "patches": [{"kind": "erase", "src_start": 345.14,
                         "src_end": 349.47}]}


def test_the_pre_fix_s23_final_is_not_served_again():
    assert not renderer.cache_rules_current(_s23(), _PATCHED, "final")


def test_after_the_fix_it_is_and_unpatched_edls_keep_their_cache():
    assert renderer.cache_rules_current(
        _s23(patch_v=config.PATCH_CLOCK_VERSION), _PATCHED, "final")
    assert renderer.cache_rules_current(
        _s23(), {"keep": [[340.0, 352.0]]}, "final")


def test_the_caption_fingerprint_is_part_of_the_same_predicate():
    edl = {"keep": [[0.0, 4.0]], "captions": {"mode": "from_transcript"}}
    index = {"words": [{"w": "hi", "t0": 0.5, "t1": 0.9}]}
    fp = renderer._caption_index_fp(edl, index)
    assert renderer.cache_rules_current(
        _s23(caption_fp=fp), edl, "final", index_of=lambda: index)
    assert not renderer.cache_rules_current(
        _s23(caption_fp="0" * 16), edl, "final", index_of=lambda: index)
    # A render older than fingerprints is trusted, as before.
    assert renderer.cache_rules_current(
        _s23(), edl, "final", index_of=lambda: index)
