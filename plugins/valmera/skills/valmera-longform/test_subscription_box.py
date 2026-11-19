import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import subscription_box as SB


def test_box_client_reaches_the_tunnel_port_whatever_the_macs_default(monkeypatch):
    files = SB.client_files(41234)
    text = files['client/proxy_client.py'].decode()
    assert "BASE = 'http://127.0.0.1:41234/v1'" in text
    assert text.count('\nBASE = ') == 1


def test_bridge_forwards_to_the_proxys_home_port(monkeypatch):
    monkeypatch.delenv('CODEX_PROXY_PORT', raising=False)
    assert SB.local_port() == 8788
    monkeypatch.setenv('CODEX_PROXY_PORT', '8790')
    assert SB.local_port() == 8790
