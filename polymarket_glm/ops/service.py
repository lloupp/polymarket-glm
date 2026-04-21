"""Systemd service configuration and generation.

Provides ServiceConfig for defining how polymarket-glm runs as a systemd
service, plus helpers to generate the unit file, environment file, and
start script.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    """Configuration for running polymarket-glm as a systemd service."""

    mode: str = Field(default="paper", description="Trading mode: paper or live")
    working_dir: str = Field(
        default_factory=lambda: str(Path.cwd()),
        description="Working directory for the service",
    )
    python_path: str = Field(
        default_factory=lambda: sys.executable,
        description="Path to Python interpreter",
    )
    env_file: Optional[str] = Field(
        default=None,
        description="Path to .env file (e.g. /etc/polymarket-glm/.env)",
    )
    env_vars: Dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set",
    )
    service_name: str = Field(
        default="polymarket-glm",
        description="Systemd service name",
    )
    restart_delay_sec: int = Field(
        default=5,
        description="Seconds to wait before restarting on failure",
    )
    user: Optional[str] = Field(
        default=None,
        description="User to run the service as (None = current user)",
    )

    model_config = {"arbitrary_types_allowed": True}


def generate_systemd_unit(config: ServiceConfig) -> str:
    """Generate a systemd unit file content from ServiceConfig.

    Returns a complete .service file string with [Unit], [Service], [Install]
    sections configured for polymarket-glm.
    """
    env_file_line = ""
    if config.env_file:
        env_file_line = f"EnvironmentFile={config.env_file}"

    user_line = ""
    if config.user:
        user_line = f"User={config.user}"

    env_lines = ""
    # Always set PGLM_MODE from config.mode
    mode_line = f"Environment=PGLM_MODE={config.mode}"
    env_lines = mode_line

    # Add any extra env vars
    extra_env = ""
    for key, value in config.env_vars.items():
        if key != "PGLM_MODE":  # already set above
            extra_env += f"\nEnvironment={key}={value}"

    unit = f"""[Unit]
Description=Polymarket GLM Trading Bot ({config.mode} mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={config.working_dir}
ExecStart={config.python_path} -m polymarket_glm.engine.trading_loop
{env_file_line}{user_line}
{env_lines}{extra_env}
Restart=always
RestartSec={config.restart_delay_sec}
StandardOutput=journal
StandardError=journal
SyslogIdentifier={config.service_name}

[Install]
WantedBy=multi-user.target
"""
    return unit


def generate_env_file(config: ServiceConfig) -> str:
    """Generate a .env file content from ServiceConfig.

    Includes PGLM_MODE and any additional env_vars.
    """
    lines = [
        "# polymarket-glm environment",
        f"PGLM_MODE={config.mode}",
    ]

    for key, value in config.env_vars.items():
        if key != "PGLM_MODE":  # already added above
            lines.append(f"{key}={value}")

    return "\n".join(lines) + "\n"


def generate_start_script(config: ServiceConfig) -> str:
    """Generate a bash start script for polymarket-glm.

    Useful for manual starts or debugging outside systemd.
    """
    env_exports = f"export PGLM_MODE={config.mode}\n"

    for key, value in config.env_vars.items():
        if key != "PGLM_MODE":
            env_exports += f"export {key}={value}\n"

    script = f"""#!/usr/bin/env bash
set -euo pipefail

cd {config.working_dir}
{env_exports}
exec {config.python_path} -m polymarket_glm.engine.trading_loop
"""
    return script
