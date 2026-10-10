"""Safety boundary applies before importing the production package."""
import socket
import subprocess
import os

import pytest


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    for name in list(os.environ):
        if name.startswith('TEGRASTATS'):
            monkeypatch.delenv(name)
    def denied(*args, **kwargs):
        raise AssertionError("Offline suite forbids network and real subprocesses")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket.socket, "bind", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    from tegrastats_api.model_monitor import ModelMonitor
    monkeypatch.setattr(ModelMonitor, "start", denied)
