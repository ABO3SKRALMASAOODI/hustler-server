"""Renders, frame looks and face measurement survive a dropped connection.

Long sources reach ffmpeg as presigned R2 URLs: a long Cloudflare source or
a large insert in a render (renderer._stream_cloudflare_source, the
WorkdirTooSmall fallback, _render_asset_source), filmstrip sheets, sparse
frame looks (frameserve) and face measurement on a proxy (follow). With no
reconnect options ffmpeg takes a response cut off mid-body for the end of the
file and exits 0: on ffmpeg 9.0 (25 Oct 2026) a 10.0 s preview from a
streamed source came back 5.0 s, 125 of 250 frames, with no error. Every
URL input now carries http_inputs.HTTP_INPUT_ARGS (media.run and the helpers
that launch their own ffmpeg), and resumes from the byte it reached.
"""
import hashlib
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import config
import follow
import http_inputs
import media
import renderer

URL = "https://bucket.r2.invalid/originals/4746/a.mp4?X-Amz-Signature=s"


def test_url_inputs_get_resume_options_and_local_commands_stay_identical():
    opts = http_inputs.HTTP_INPUT_ARGS
    local = ["ffmpeg", "-y", "-ss", "7.000", "-i", "/tmp/valmera/src.mp4",
             "-i", "pipe:0", "-f", "null", "-"]
    assert http_inputs.resilient(local) is local
    probe = ["ffprobe", "-v", "error", "-show_format", "/tmp/a.mp4"]
    assert http_inputs.resilient(probe) is probe
    not_ffmpeg = ["curl", "-i", URL]
    assert http_inputs.resilient(not_ffmpeg) is not_ffmpeg

    # Each URL input gets the options among its own, right before its -i;
    # its seek/duration options and every local input are untouched.
    cmd = ["/usr/local/bin/ffmpeg", "-y", "-copyts", "-ss", "7.000", "-i",
           URL, "-i", "/tmp/music.m4a", "-ss", "29.000", "-t", "5.000",
           "-i", URL, "-filter_complex", "[0:v]null[v]", "-map", "[v]",
           "out.mp4"]
    assert http_inputs.resilient(cmd) == [
        "/usr/local/bin/ffmpeg", "-y", "-copyts", "-ss", "7.000", *opts,
        "-i", URL, "-i", "/tmp/music.m4a", "-ss", "29.000", "-t", "5.000",
        *opts, "-i", URL, "-filter_complex", "[0:v]null[v]", "-map", "[v]",
        "out.mp4"]
    # An input that already sets -reconnect (analysis_jobs._decode, the
    # indexer's media.input_args) keeps its own options, once.
    own = ["ffmpeg", *media.input_args(URL), "-i", URL, "-f", "null", "-"]
    assert http_inputs.resilient(own) == [
        "ffmpeg", *opts, "-i", URL, *opts, "-i", URL, "-f", "null", "-"]
    assert media.input_args(URL) == [*opts, "-i", URL]
    # ffprobe takes its input positionally or after -i.
    assert http_inputs.resilient(["ffprobe", "-v", "error", URL]) == [
        "ffprobe", "-v", "error", *opts, URL]
    assert http_inputs.resilient(["ffprobe", "-i", URL, "-show_format"]) == [
        "ffprobe", *opts, "-i", URL, "-show_format"]


@pytest.fixture
def dropping_server(tmp_path):
    """A 40 s faststart mp4 served with Range support, where EVERY response
    longer than 64 KiB is cut off half-way, the way a dropped connection
    ends a long read. So the probe, the main read, each bounded cluster read
    and each resumed read are all cut until what is left of them is small.
    """
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("requires real ffmpeg")
    source = tmp_path / "original.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=25:duration=40", "-f",
                    "lavfi", "-i", "sine=frequency=440:duration=40",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "50",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    "-movflags", "+faststart", str(source)],
                   check=True, capture_output=True, timeout=120)
    data = source.read_bytes()
    log = {"reads": [], "cuts": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            start, end = 0, len(data) - 1
            range_ = self.headers.get("Range")
            if range_:
                a, b = range_.removeprefix("bytes=").split("-")
                start, end = int(a), int(b) if b else end
            body = data[start:end + 1]
            cut = len(body) > 64 * 1024
            with lock:
                log["reads"].append((start, end))
                log["cuts"] += cut
            self.send_response(206 if range_ else 200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            if range_:
                self.send_header("Content-Range",
                                 f"bytes {start}-{end}/{len(data)}")
            self.end_headers()
            try:
                self.wfile.write(body[:len(body) // 2] if cut else body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass                       # the reader had what it needed
            if cut:
                self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield str(source), f"http://127.0.0.1:{server.server_port}/o.mp4", log
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _decoded(path):
    frames = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:v", "-f", "framemd5",
         "-"], capture_output=True, text=True, check=True).stdout
    audio = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a", "-f", "md5",
         "-"], capture_output=True, text=True, check=True).stdout
    return [ln for ln in frames.splitlines() if not ln.startswith("#")], audio


@pytest.mark.parametrize("keep, seconds, url_inputs, seek", [
    # One read from the top, through the first cut (half-way into the file,
    # ~20 s): without the options the render came back short.
    ([[2.0, 4.0], [17.0, 25.0]], 10.0, 1, None),
    # Keeps 8 s and 30 s in: the main read seeks (-copyts -ss 7) and the two
    # clusters are bounded -ss/-t reads of the same URL; without the options
    # the picture survived and the sound did not.
    ([[8.0, 10.0], [30.0, 33.0]], 5.0, 3, "7.000"),
])
def test_streamed_render_survives_dropped_connections(
        dropping_server, tmp_path, monkeypatch, keep, seconds, url_inputs,
        seek):
    local, url, log = dropping_server
    edl = {"keep": keep}
    index = {"words": [], "sentences": [], "silences": [], "video": {}}
    if seek:
        assert renderer._keep_clusters(keep, config.KEEP_CLUSTER_GAP_S,
                                       8) == [tuple(k) for k in keep]
    launched = []
    real = http_inputs.resilient

    def spy(cmd):
        out = real(cmd)
        launched.append(out)
        return out

    monkeypatch.setattr(http_inputs, "resilient", spy)
    got = {}
    for name, src in (("local", local), ("url", url)):
        out, work = tmp_path / f"{name}.mp4", tmp_path / f"work_{name}"
        work.mkdir()
        log["cuts"] = 0
        got[name] = renderer.render_edl(edl, index, src, str(out), str(work),
                                        preview=True, suppress_outro=True)
        got[name + "_cuts"] = log["cuts"]
        got[name + "_decoded"] = _decoded(str(out))
    assert got["local"] == pytest.approx(seconds, abs=0.05)
    assert got["url"] == pytest.approx(got["local"], abs=0.01)
    assert got["url_cuts"] >= url_inputs, "the reads were not interrupted"
    assert any(start > 0 for start, _end in log["reads"])
    frames, audio = got["url_decoded"]
    assert len(frames) == round(seconds * 25)
    assert frames == got["local_decoded"][0], "every frame identical"
    assert audio == got["local_decoded"][1], "audio identical"

    encode = max((c for c in launched if c[0] == "ffmpeg"),
                 key=lambda c: c.count(url))
    opts = http_inputs.HTTP_INPUT_ARGS
    reads = [i for i, a in enumerate(encode) if a == url]
    assert len(reads) == url_inputs
    for at in reads:
        assert encode[at - 1] == "-i"
        assert encode[at - 1 - len(opts):at - 1] == opts
    if seek:
        at = encode.index("-copyts")
        assert encode[at:at + 3] == ["-copyts", "-ss", seek]
    # The local render's commands carry no option they did not have before.
    assert not any("-reconnect" in c for c in launched if url not in c)


def test_streamed_frame_looks_and_face_reads_survive_dropped_connections(
        dropping_server, tmp_path):
    local, url, log = dropping_server
    # frameserve / filmstrip: one still at a time, by input seek.
    stills = {}
    for name, src in (("local", local), ("url", url)):
        dst = tmp_path / f"{name}.jpg"
        media.frame_at(src, 31.0, str(dst), width=320)
        stills[name] = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert stills["url"] == stills["local"]
    # follow: a window of grey frames piped from the proxy, to its end.
    log["cuts"] = 0
    want = follow._decode_gray(local, 25.0, 40.0, 4.0, 64, 36, 60)
    seen = follow._decode_gray(url, 25.0, 40.0, 4.0, 64, 36, 60)
    assert log["cuts"] >= 1, "the streamed read was not interrupted"
    assert len(want) == 60
    assert [hashlib.sha256(f.tobytes()).hexdigest() for f in seen] == [
        hashlib.sha256(f.tobytes()).hexdigest() for f in want]
