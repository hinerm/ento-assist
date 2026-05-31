# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""Tests for server.py — bootstrap and environment validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ento_assist.server import SERVER_INSTRUCTIONS, build_server, main

# ---------------------------------------------------------------------------
# build_server
# ---------------------------------------------------------------------------


def test_build_server_returns_fastmcp_instance(tmp_path: Path):
    from mcp.server.fastmcp import FastMCP

    db_path = tmp_path / "test.sqlite"
    mcp = build_server(db_path)
    assert isinstance(mcp, FastMCP)


def test_build_server_name(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    mcp = build_server(db_path)
    assert mcp.name == "ento-assist"


# ---------------------------------------------------------------------------
# SERVER_INSTRUCTIONS
# ---------------------------------------------------------------------------


def test_server_instructions_nonempty():
    assert SERVER_INSTRUCTIONS
    assert len(SERVER_INSTRUCTIONS.strip()) > 100


def test_server_instructions_mentions_human_approval():
    """HITL rules must be present — this guards against accidental weakening."""
    assert "commit_extraction" in SERVER_INSTRUCTIONS
    assert "advance_session" in SERVER_INSTRUCTIONS


# ---------------------------------------------------------------------------
# main() environment variable handling
# ---------------------------------------------------------------------------


def test_main_missing_env_exits_nonzero(tmp_path: Path):
    with patch.dict("os.environ", {}, clear=True):
        # Ensure ENTO_DB_PATH is not set
        import os

        os.environ.pop("ENTO_DB_PATH", None)
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code != 0


def test_main_valid_env_calls_mcp_run(tmp_path: Path):
    from ento_assist.db.connection import initialize_db

    db_path = tmp_path / "ento.sqlite"
    initialize_db(db_path)

    with (
        patch.dict("os.environ", {"ENTO_DB_PATH": str(db_path)}),
        patch("ento_assist.server.FastMCP.run") as mock_run,
    ):
        main()
        mock_run.assert_called_once()
