"""AT-0B-4: every ffmpeg filter the FX roadmap relies on exists in the ffmpeg
the product renders with.

CI installs the exact production pin before the worker suite runs — the
BtbN ``ffmpeg-n8.1-latest-linux64-gpl-8.1`` static build every executor
image bakes (worker/Dockerfile, Dockerfile.cloudflare, Dockerfile.agent-base)
— and the Cloudflare deploy workflow runs this suite before it publishes.
A missing filter therefore fails the build, never a customer's render.
"""

import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
REPO = os.path.dirname(WORKER)
sys.path.insert(0, WORKER)

import fxgraph                                               # noqa: E402

PIN = "ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"
IN_CI = bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))

# ROADMAP §2 "ffmpeg filters relied on", verbatim.
ROADMAP_FILTERS = (
    "xfade maskedmerge alphamerge alphaextract hsvhold colorhold pseudocolor "
    "lut3d lagfun displace varblur guided photosensitivity morpho remap "
    "dblur swaprect shufflepixels v360 lenscorrection huesaturation "
    "threshold backgroundkey scdet").split()


def _ffmpeg_or_skip():
    if shutil.which("ffmpeg"):
        return
    if IN_CI:
        pytest.fail("CI must install the production ffmpeg pin")
    pytest.skip("no ffmpeg on this machine")


def test_the_pinned_filter_list_is_the_roadmap_list():
    assert tuple(ROADMAP_FILTERS) == fxgraph.REQUIRED_FILTERS
    assert fxgraph.REQUIRED_BSF_OPTIONS == {"noise": ("drop",)}


def test_at_0b_4_every_required_filter_exists_in_this_ffmpeg():
    _ffmpeg_or_skip()
    fxgraph._FILTER_CACHE.clear()
    assert fxgraph.check_required() == []
    # the parser really read the table (and timeline flags with it)
    assert len(fxgraph.available_filters()) > 300
    assert {"negate", "overlay", "maskedmerge"} <= \
        fxgraph.timeline_filters()


def test_ci_runs_the_suite_on_the_production_pin():
    """The ffmpeg this suite runs on in CI is the one production ships."""
    files = [os.path.join(WORKER, n) for n in
             ("Dockerfile", "Dockerfile.cloudflare", "Dockerfile.agent-base")]
    files += [os.path.join(REPO, ".github", "workflows", n) for n in
              ("check-editor.yml", "deploy-cloudflare-executor.yml")]
    present = [f for f in files if os.path.exists(f)]
    if len(present) < len(files):
        pytest.skip("repository layout not available")
    for f in present:
        assert PIN in open(f, encoding="utf-8").read(), f
    if IN_CI and shutil.which("ffmpeg"):
        first = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                               text=True).stdout.splitlines()[0]
        assert re.search(r"\bn?8\.1", first), first


def test_a_missing_filter_degrades_the_device_not_the_render():
    """The same check runs per device at render time (fault
    ffmpeg_filter_missing takes exactly that path)."""
    import faultinject
    with faultinject.inject("ffmpeg_filter_missing:xfade"):
        assert fxgraph.filter_missing("xfade")
        assert fxgraph.missing_filters(["xfade", "xfade"]) == ["xfade"]
    fxgraph._FILTER_CACHE.clear()
    assert fxgraph.missing_filters(["surely_not_a_filter"],
                                   ffmpeg="/nonexistent/ffmpeg") == \
        ["surely_not_a_filter"]
    fxgraph._FILTER_CACHE.clear()
