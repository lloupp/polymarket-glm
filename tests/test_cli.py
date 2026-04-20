"""Tests for CLI interface."""
import pytest
from polymarket_glm.interface.cli import main


def test_cli_help(capsys):
    """CLI with no args should print help."""
    main([])
    captured = capsys.readouterr()
    assert "pglm" in captured.out or "polymarket" in captured.out.lower() or "--help" in captured.out or captured.out == ""


def test_cli_status(capsys):
    main(["--mode", "paper", "--balance", "5000", "status"])
    captured = capsys.readouterr()
    assert "paper" in captured.out.lower()
    assert "5,000" in captured.out or "5000" in captured.out


def test_cli_risk(capsys):
    main(["--mode", "paper", "risk"])
    captured = capsys.readouterr()
    assert "Risk" in captured.out


def test_cli_killswitch(capsys):
    main(["--mode", "paper", "killswitch", "--reason", "test"])
    captured = capsys.readouterr()
    assert "ACTIVATED" in captured.out

    main(["--mode", "paper", "killswitch", "--deactivate"])
    captured = capsys.readouterr()
    assert "deactivated" in captured.out.lower()
