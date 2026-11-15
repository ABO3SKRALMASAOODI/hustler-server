"""Private, loopback-only SSH bridge to the owner's local subscription adapter."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path('/Users/masaoodi/Documents/Valmera')


def client_files(port):
    client = ROOT / '.valmera/tools/codex-proxy/client'
    if not 1 <= int(port) <= 65535:
        raise ValueError('Invalid allocated SSH port')
    return {
        'client/sitecustomize.py': (client / 'sitecustomize.py').read_bytes(),
        'client/proxy_client.py': (client / 'proxy_client.py').read_text().replace(
            '127.0.0.1:8787', f'127.0.0.1:{port}').encode(),
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
                    '127.0.0.1:0:127.0.0.1:8787', host], check=True,
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
