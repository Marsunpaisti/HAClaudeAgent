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


def test_openai_events_sync():
    """The add-on and integration copies of openai_events.py must match."""
    addon_file = REPO_ROOT / "ha_claude_agent_addon" / "src" / "openai_events.py"
    integration_file = (
        REPO_ROOT / "custom_components" / "ha_claude_agent" / "openai_events.py"
    )

    assert addon_file.exists(), f"Missing: {addon_file}"
    assert integration_file.exists(), f"Missing: {integration_file}"
    # Normalize line endings to handle CRLF vs LF differences (Windows/Linux)
    addon_text = addon_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    integration_text = integration_file.read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )
    assert addon_text == integration_text, (
        "openai_events.py copies have drifted — keep them identical"
    )
