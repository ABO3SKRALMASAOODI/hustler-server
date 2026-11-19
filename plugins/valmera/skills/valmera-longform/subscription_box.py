"""Private, loopback-only SSH bridge to the owner's local subscription adapter."""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import tempfile
import time

ROOT = Path('/Users/masaoodi/Documents/Valmera')


def local_port():
    """The Mac's proxy port: 8788 since 19 Nov (the CRM's server holds *:8787), CODEX_PROXY_PORT when set."""
    return int(os.environ.get('CODEX_PROXY_PORT') or 8788)


def client_files(port):
    client = ROOT / '.valmera/tools/codex-proxy/client'
    if not 1 <= int(port) <= 65535:
        raise ValueError('Invalid allocated SSH port')
    # The box reaches the proxy through the tunnel's port, whatever default the Mac's client carries.
    lines = [f"BASE = 'http://127.0.0.1:{int(port)}/v1'" if ln.startswith('BASE = ') else ln
             for ln in (client / 'proxy_client.py').read_text().splitlines()]
    return {
        'client/sitecustomize.py': (client / 'sitecustomize.py').read_bytes(),
        'client/proxy_client.py': ('\n'.join(lines) + '\n').encode(),
    }


@contextmanager
def bridge(host, key):
    # TMPDIR belongs to the run. The short socket name avoids macOS's Unix
    # socket path limit even when the run's directory is deeply nested.
    with tempfile.TemporaryDirectory(prefix='wf4-ssh-') as folder:
        socket = str(Path(folder) / 's')
        options = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
                   '-o', 'ExitOnForwardFailure=yes', '-i', key, '-S', 's']
        with open(Path(folder) / 'ssh.log', 'w+') as log:
            process = subprocess.Popen(options + ['-M', '-N', host],
                                       stdout=log, stderr=log, cwd=folder)
            try:
                until = time.monotonic() + 25
                while not Path(socket).exists():
                    if process.poll() is not None or time.monotonic() >= until:
                        raise RuntimeError('Local subscription SSH bridge did not start')
                    time.sleep(.1)
                result = subprocess.run(options + ['-O', 'forward', '-R',
                    f'127.0.0.1:0:127.0.0.1:{local_port()}', host], check=True,
                    capture_output=True, text=True, timeout=25, cwd=folder)
                port = int(result.stdout.strip())
                yield port
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
