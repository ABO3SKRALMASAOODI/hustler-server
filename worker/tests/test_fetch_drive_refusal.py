"""A Drive link only its owner can repair is a precondition, not our failure
(ticket 10, 5 Nov 2026): the customer gets the step that fixes it, and the
intake is not told a customer is blocked by us. A Google refusal that is NOT
a sign-in wall stays a failure of ours, so an IP block can never hide here."""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools  # noqa: E402
import net_fetch  # noqa: E402
import tool_outcome  # noqa: E402
import url_media  # noqa: E402

FILE_ID = "1fSKcjm_a-GddHh5NOou6DSroSPNmBGlR"
LINK = f"https://drive.google.com/file/d/{FILE_ID}/view?usp=drivesdk"
FORBIDDEN = (f"ERROR: [GoogleDrive] {FILE_ID}: Unable to download JSON "
             "metadata: HTTP Error 403: Forbidden (caused by <HTTPError 403: "
             "Forbidden>)")
GONE = FORBIDDEN.replace("403: Forbidden", "404: Not Found")
SIGN_IN = "https://accounts.google.com/v3/signin/identifier?continue=x"


def _refused(monkeypatch, tmp_path, url, err, final):
    def fail(*a, **k):
        raise url_media.FetchMediaError(err)

    probes = []
    monkeypatch.setattr(url_media, "_extract", fail)
    monkeypatch.setattr(url_media.net_fetch, "final_url",
                        lambda u, **k: probes.append(u) or final)
    with pytest.raises(url_media.FetchMediaError) as caught:
        url_media._extract_with_fallback(url, str(tmp_path))
    return str(caught.value), probes


@pytest.mark.parametrize("url", [
    LINK,
    f"https://drive.google.com/u/0/file/d/{FILE_ID}/view",
    f"https://drive.google.com/open?id={FILE_ID}",
    f"https://drive.google.com/uc?export=download&id={FILE_ID}",
    f"https://docs.google.com/file/d/{FILE_ID}/edit",
])
def test_every_drive_file_link_shape_is_recognised(url):
    assert url_media.drive_file_id(url) == FILE_ID


@pytest.mark.parametrize("url", [
    "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz",
    "https://example.com/file/d/1fSKcjm_a-GddHh5NOou6DSroSPNmBGlR/view",
    "https://drive.google.com.evil.example/file/d/"
    "1fSKcjm_a-GddHh5NOou6DSroSPNmBGlR/view",
])
def test_other_links_are_not_drive_files(url):
    assert url_media.drive_file_id(url) is None


def test_a_private_file_is_named_private(monkeypatch, tmp_path):
    said, probes = _refused(monkeypatch, tmp_path, LINK, FORBIDDEN, SIGN_IN)
    assert said == url_media.DRIVE_PRIVATE
    assert url_media.drive_needs_customer(said)
    assert probes == [f"https://drive.usercontent.google.com/download"
                      f"?id={FILE_ID}&export=download"]


def test_a_deleted_file_needs_no_probe(monkeypatch, tmp_path):
    said, probes = _refused(monkeypatch, tmp_path, LINK, GONE, SIGN_IN)
    assert said == url_media.DRIVE_MISSING
    assert url_media.drive_needs_customer(said)
    assert probes == []


@pytest.mark.parametrize("final", [
    None,                                     # could not tell
    "https://drive.usercontent.google.com/download?id=x",   # no wall there
    "https://accounts.google.com.evil.example/signin",
])
def test_a_403_without_a_sign_in_wall_stays_our_failure(
        monkeypatch, tmp_path, final):
    said, _ = _refused(monkeypatch, tmp_path, LINK, FORBIDDEN, final)
    assert said == FORBIDDEN
    assert not url_media.drive_needs_customer(said)


def test_another_sites_403_never_probes_google(monkeypatch, tmp_path):
    err = "ERROR: [generic] Unable to download webpage: HTTP Error 403"
    said, probes = _refused(monkeypatch, tmp_path,
                            "https://example.com/video", err, SIGN_IN)
    assert said == err and probes == []


def _tool_answer(monkeypatch, tmp_path, error):
    monkeypatch.setattr(agent_tools, "_execution_policy", lambda ctx: "redesign")
    monkeypatch.setattr(agent_tools.remote, "fetch_available", lambda: True)
    monkeypatch.setattr(agent_tools.remote, "run_fetch_remote",
                        lambda *a, **k: {"ok": False, "error": error})
    ctx = types.SimpleNamespace(
        workdir=str(tmp_path), project_id=5498, job={"user_id": 7},
        urls_fetched=[])
    return tool_outcome.from_legacy(agent_tools.fetch_url(ctx, LINK))


@pytest.mark.parametrize("error, fix", [
    (url_media.DRIVE_PRIVATE, "Anyone with the link"),
    (url_media.DRIVE_MISSING, "a working link"),
])
def test_the_tool_answers_a_prerequisite_with_the_fix(
        monkeypatch, tmp_path, error, fix):
    # The sentence crosses the executor boundary as text, as in production.
    outcome = _tool_answer(monkeypatch, tmp_path, error)
    assert outcome.status == "prerequisite"
    assert error in outcome.message
    assert fix in outcome.message
    assert "Do not call fetch_url" in outcome.message
    assert outcome.prerequisite_tool is None


def test_a_real_google_refusal_is_still_transient(monkeypatch, tmp_path):
    outcome = _tool_answer(monkeypatch, tmp_path, FORBIDDEN)
    assert outcome.status == "transient_failure"


def test_final_url_says_nothing_rather_than_guessing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("dead host")

    monkeypatch.setattr(net_fetch, "_open", boom)
    assert net_fetch.final_url("https://example.com/x") is None
