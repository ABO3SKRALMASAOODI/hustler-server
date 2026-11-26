"""Reuse a film worker's local footage and music engines over an inherited, private socket pair."""
import atexit
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


_service = None
_channel = None
enabled = False


def close():
    global _service, _channel
    if _channel:
        _channel.close()
        _channel = None
    if _service:
        try:
            _service.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _service.terminate()
            _service.wait()
        _service = None


def call(tool, args, *, worker, env, log, timeout, heavy=()):
    """The music client runs inside the usual heavy slot for the entire request."""
    global _service, _channel
    if _service is None or _service.poll() is not None:
        close()
        _channel, child = socket.socketpair()
        with child:
            _service = subprocess.Popen([sys.executable, __file__, "serve", str(child.fileno()), worker],
                                        pass_fds=(child.fileno(),), cwd=worker, env=env)
        atexit.register(close)
    fd = _channel.fileno()
    try:
        return subprocess.run([*heavy, sys.executable, __file__, "call", str(fd), tool, str(log), *args],
                              cwd=worker, env=env, pass_fds=(fd,), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Drop the outstanding response before this worker starts another film.
        _service.terminate()
        close()
        raise


def execute(request):
    if request["tool"] == "footage":
        from footage import run
        sys.argv = ["footage.run", *request["args"]]
        run.main()
    else:
        import music_film
        with open(request["args"][0]) as f:
            job = json.load(f)
        with open(job["l1"]) as f:
            scene = json.load(f)
        report = music_film.score(scene, job["work"], film=job["film"], out=job["out"], kind=job["kind"],
                                  song=job.get("song"), vocals=job.get("vocals"), turn_s=job.get("turn"),
                                  payoff_s=job.get("payoff"), turn_drop=job["turn_drop"], cache=job["cache"],
                                  brief=job["brief"], used=job["used"], call=music_film.openai_call(),
                                  **({"preferred_cid": job["preferred_cid"]} if job.get("preferred_cid") else {}))
        Path(job["report"]).write_text(json.dumps(report, default=float))


def serve(channel, worker):
    from contextlib import redirect_stderr, redirect_stdout
    sys.path.insert(0, worker)
    with channel.makefile("rb") as stream:
        for line in stream:
            job = json.loads(line)
            # Each invocation gets the same environment as its original subprocess.
            os.environ.clear()
            os.environ.update(job["env"])
            tempfile.tempdir = None
            try:
                with open(job["log"], "a", buffering=1) as log, redirect_stdout(log), redirect_stderr(log):
                    execute(job)
                answer = {"ok": True}
            except (Exception, SystemExit) as exc:
                answer = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
            channel.sendall(json.dumps(answer).encode() + b"\n")


if __name__ == "__main__":
    with socket.socket(fileno=int(sys.argv[2])) as sock:
        if sys.argv[1] == "serve":
            serve(sock, sys.argv[3])
        else:
            payload = dict(tool=sys.argv[3], log=sys.argv[4], args=sys.argv[5:], env=dict(os.environ))
            sock.sendall(json.dumps(payload).encode() + b"\n")
            with sock.makefile("rb") as reply:
                result = json.loads(reply.readline())
            if not result["ok"]:
                sys.exit(result["error"])
