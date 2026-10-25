"""Resumable reads for every ffmpeg/ffprobe input that is an HTTP(S) URL.

Long sources reach ffmpeg as presigned R2 URLs instead of staged files: a
long Cloudflare source or a large insert in a render, a filmstrip sheet, a
sparse frame look, a face measurement on a proxy, an index too big to stage.
With no reconnect options, ffmpeg takes a response that is cut off mid-body
for the end of the file and exits 0 with a shorter output. Measured 25 Oct
2026 on ffmpeg 9.0 against a Range-capable server that drops long responses
half-way: a 4 s upload indexed to a 1.9 s wav, a 10.0 s preview rendered
5.0 s (125 of 250 frames), and a read from -ss 27 of a 30 s file returned 24
of 75 frames. With HTTP_INPUT_ARGS each read resumes from the byte it had
reached (HTTP Range) and decodes the same frames as the local file, input
seeking (-ss, -copyts) included.

A leaf module (no worker imports), so the pure helpers that launch their
own ffmpeg can use it as well as media.run.
"""
import os

# A dropped connection or a stalled socket resumes from the byte it reached;
# a server that stays silent for two minutes fails the read instead of
# hanging it.
HTTP_INPUT_ARGS = ["-reconnect", "1", "-reconnect_streamed", "1",
                   "-reconnect_on_network_error", "1",
                   "-reconnect_delay_max", "30",
                   "-rw_timeout", "120000000"]

_TOOLS = ("ffmpeg", "ffprobe")


def is_url(path):
    return isinstance(path, str) and path.startswith(("http://", "https://"))


def input_args(src):
    """ffmpeg input options for `src`: resilient reads for an HTTP(S) URL."""
    src = str(src)
    if is_url(src):
        return HTTP_INPUT_ARGS + ["-i", src]
    return ["-i", src]


def resilient(cmd):
    """``cmd`` with HTTP_INPUT_ARGS ahead of each URL input that does not
    set -reconnect itself.

    Options before an input belong to that input, so the options go directly
    before its ``-i`` (ffprobe: before its input, ``-i`` or positional).
    Anything other than an ffmpeg/ffprobe command, and any command without a
    URL input, comes back as the same list: local-file commands are
    byte-identical."""
    tool = os.path.basename(str(cmd[0])) if cmd else ""
    if tool not in _TOOLS:
        return cmd
    out, own = [cmd[0]], 1          # own: where this input's options begin
    i = 1
    while i < len(cmd):
        if cmd[i] == "-i" and i + 1 < len(cmd):
            take = [cmd[i], cmd[i + 1]]
        elif tool == "ffprobe" and is_url(cmd[i]):
            take = [cmd[i]]
        else:
            out.append(cmd[i])
            i += 1
            continue
        if is_url(take[-1]) and "-reconnect" not in out[own:]:
            out += HTTP_INPUT_ARGS
        out += take
        own = len(out)
        i += len(take)
    return cmd if len(out) == len(cmd) else out
