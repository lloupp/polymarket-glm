"""Tests for systemd service configuration."""
import pytest
import os
from polymarket_glm.ops.service import (
    ServiceConfig,
    generate_systemd_unit,
    generate_env_file,
    generate_start_script,
)


def test_service_config_defaults():
    """ServiceConfig should have sensible defaults."""
    config = ServiceConfig()
    assert config.working_dir != ""
    assert config.python_path != ""
    assert config.mode in ("paper", "live")


def test_generate_systemd_unit():
    """Should generate valid systemd unit file content."""
    config = ServiceConfig(mode="paper")
    unit = generate_systemd_unit(config)
    assert "[Unit]" in unit
    assert "[Service]" in unit
    assert "[Install]" in unit
    assert "polymarket-glm" in unit
    assert "Restart=always" in unit
    assert "paper" in unit


def test_generate_systemd_unit_live_mode():
    """Live mode should set PGLM_MODE=live."""
    config = ServiceConfig(mode="live")
    unit = generate_systemd_unit(config)
    assert "PGLM_MODE=live" in unit


def test_generate_env_file():
    """Should generate .env file with required vars."""
    config = ServiceConfig(
        mode="paper",
        env_vars={"PGLM_MODE": "paper", "PGLM_LOG_LEVEL": "INFO"},
    )
    env = generate_env_file(config)
    assert "PGLM_MODE=paper" in env
    assert "PGLM_LOG_LEVEL=INFO" in env


def test_generate_start_script():
    """Should generate a start script with correct python path."""
    config = ServiceConfig(mode="paper")
    script = generate_start_script(config)
    assert "#!/bin/bash" in script or "#!/usr/bin/env bash" in script
    assert "python" in script.lower() or "pglm" in script.lower()


def test_service_config_env_file_path():
    """ServiceConfig should track env file path."""
    config = ServiceConfig(env_file="/etc/polymarket-glm/.env")
    assert config.env_file == "/etc/polymarket-glm/.env"
