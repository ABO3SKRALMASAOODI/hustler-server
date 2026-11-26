"""Reuse a film worker's local footage and music engines over an inherited, private socket pair."""
import atexit
import json
import os
from pathlib import Path
import socket
import signal
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


def activity(root, pid):
    # Some decoders spend a long time on one file. CPU time distinguishes that from an idle child.
    changed = 0
    for p in Path(root).rglob("*"):
        try:
            changed = max(changed, p.stat().st_mtime_ns)
        except FileNotFoundError:
            pass
    rows = subprocess.check_output(["ps", "-A", "-o", "pid=,ppid=,time="], text=True)
    children, times = {}, {}
    for row in rows.splitlines():
        child, parent, cpu = row.split()
        seconds = 0.0
        for component in cpu.split(":"):
            seconds = seconds * 60 + float(component)
        times[int(child)] = int(seconds)
        children.setdefault(int(parent), []).append(int(child))
    cpu, todo = 0, [pid]
    while todo:
        process = todo.pop()
        cpu += times.get(process, 0)
        todo.extend(children.get(process, []))
    return changed, cpu


def run_media(cmd, *, cwd, env, timeout, progress_root, engine_pid=None, pass_fds=()):
    """A stalled tool is retried. Let a decoder or narrator still producing frames or takes finish."""
    with subprocess.Popen(cmd, cwd=cwd, env=env, pass_fds=pass_fds, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as child:
        watched = engine_pid or child.pid
        before = activity(progress_root, watched)
        while True:
            try:
                stdout, stderr = child.communicate(timeout=timeout)
                return subprocess.CompletedProcess(cmd, child.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                after = activity(progress_root, watched)
                if before != after:
                    before = after
                    continue
                # The media command can be inside the normal heavy-slot wrapper.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.communicate()
                except ProcessLookupError:
                    pass
                raise


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
        command = [*heavy, sys.executable, __file__, "call", str(fd), tool, str(log), *args]
        if tool == "footage":
            return run_media(command, cwd=worker, env=env, timeout=timeout, pass_fds=(fd,),
                             progress_root=env.get("FOOTAGE_WORK") or Path(log).parent, engine_pid=_service.pid)
        return subprocess.run(command, cwd=worker, env=env, pass_fds=(fd,),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Drop the outstanding response before this worker starts another film.
        _service.terminate()
        close()
        raise


def execute(request):
    if request["tool"] == "footage":
        from footage import run
        if os.environ.get("VALMERA_BATCH_HEARING_LOCK"):
            import batch_hearing
            batch_hearing.install(run.hear)
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
