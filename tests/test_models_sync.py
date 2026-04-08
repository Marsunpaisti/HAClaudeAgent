"""Verify the two models.py copies stay in sync."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_models_files_are_identical():
    """The integration and add-on models.py must be byte-identical."""
    integration = REPO_ROOT / "custom_components" / "ha_claude_agent" / "models.py"
    addon = REPO_ROOT / "ha_claude_agent_addon" / "src" / "models.py"

    assert integration.exists(), f"Missing: {integration}"
    assert addon.exists(), f"Missing: {addon}"
    # Normalize line endings to handle CRLF vs LF differences (Windows/Linux)
    integration_text = integration.read_text(encoding="utf-8").replace("\r\n", "\n")
    addon_text = addon.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert integration_text == addon_text, (
        "models.py files have diverged — edit one, copy to the other"
    )
