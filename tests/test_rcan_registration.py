"""Tests for rcan.dev registration flow in wizard and CLI."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# _print_manual_registration_url
# ---------------------------------------------------------------------------


def test_manual_url_printed(capsys):
    from castor.wizard import _print_manual_registration_url

    _print_manual_registration_url(
        {"metadata": {"manufacturer": "acme", "model": "arm"}},
        "My Robot",
        manufacturer="acme",
        model="arm",
    )
    out = capsys.readouterr().out
    assert "rcan.dev" in out
    assert "acme" in out


def test_manual_url_empty_config(capsys):
    from castor.wizard import _print_manual_registration_url

    _print_manual_registration_url({}, "TestBot")
    out = capsys.readouterr().out
    assert "rcan.dev" in out


# ---------------------------------------------------------------------------
# _offer_rcan_registration — user skips
# ---------------------------------------------------------------------------


def test_offer_registration_skip(monkeypatch, capsys):
    from castor.wizard import _offer_rcan_registration

    monkeypatch.setattr("builtins.input", lambda _: "n")
    result = _offer_rcan_registration({}, "TestBot", "test.rcan.yaml")
    assert result is None


def test_offer_registration_eof(monkeypatch):
    from castor.wizard import _offer_rcan_registration

    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError()))
    result = _offer_rcan_registration({}, "TestBot", "test.rcan.yaml")
    assert result is None


# ---------------------------------------------------------------------------
# _programmatic_register — success
# ---------------------------------------------------------------------------


def test_programmatic_register_success(capsys):
    from castor.wizard import _programmatic_register

    mock_result = {"rrn": "RRN-00000042", "uri": "rcan://registry.rcan.dev/acme/arm/v1/x"}

    async def _fake_register(*a, **kw):
        return mock_result

    mock_client = AsyncMock()
    mock_client.register = _fake_register
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("rcan.registry.RegistryClient", return_value=mock_client):
        rrn = _programmatic_register(
            api_key="rcan_test",
            manufacturer="acme",
            model="arm",
            version="v1",
            device_id="x",
            meta={},
            robot_name="TestBot",
        )

    assert rrn == "RRN-00000042"
    out = capsys.readouterr().out
    assert "RRN-00000042" in out


def test_programmatic_register_network_error(capsys):
    from castor.wizard import _programmatic_register

    async def _fail(*a, **kw):
        raise Exception("Connection refused")

    mock_client = AsyncMock()
    mock_client.register = _fail
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("rcan.registry.RegistryClient", return_value=mock_client):
        rrn = _programmatic_register(
            api_key="rcan_test",
            manufacturer="acme",
            model="arm",
            version="v1",
            device_id="x",
            meta={},
            robot_name="TestBot",
        )

    assert rrn is None
    out = capsys.readouterr().out
    assert "rcan.dev" in out or "Error" in out


# ---------------------------------------------------------------------------
# RegistryClient — ensure register flow produces RRN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_client_register_returns_rrn():
    from rcan.registry import RegistryClient

    result_data = {
        "rrn": "RRN-00000099",
        "uri": "rcan://registry.rcan.dev/myco/mybot/v2/unit-001",
        "registered_at": "2026-03-05T00:00:00Z",
        "verification_tier": "community",
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = result_data
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.is_closed = False

    client = RegistryClient(api_key="rcan_testkey")
    client._client = mock_http

    result = await client.register(
        manufacturer="myco",
        model="mybot",
        version="v2",
        device_id="unit-001",
        metadata={"opencastor": True},
    )

    assert result["rrn"] == "RRN-00000099"
    assert result["verification_tier"] == "community"


# ---------------------------------------------------------------------------
# CLI — castor register (parse check)
# ---------------------------------------------------------------------------


def test_cli_register_subcommand_exists():
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "castor.cli", "register", "--help"],
        capture_output=True, text=True, cwd=os.path.expanduser("~/OpenCastor"),
    )
    assert "rcan.dev" in result.stdout or "register" in result.stdout.lower()


def test_cli_compliance_subcommand_exists():
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "castor.cli", "compliance", "--help"],
        capture_output=True, text=True, cwd=os.path.expanduser("~/OpenCastor"),
    )
    assert "conformance" in result.stdout.lower() or "rcan" in result.stdout.lower() or result.returncode == 0
