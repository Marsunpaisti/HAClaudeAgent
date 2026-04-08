"""Verify the two models.py copies stay in sync."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_models_files_are_identical():
    """The integration and add-on models.py must be byte-identical."""
    integration = REPO_ROOT / "custom_components" / "ha_claude_agent" / "models.py"
    addon = REPO_ROOT / "ha_claude_agent_addon" / "src" / "models.py"

    assert integration.exists(), f"Missing: {integration}"
    assert addon.exists(), f"Missing: {addon}"
    assert integration.read_text(encoding="utf-8") == addon.read_text(encoding="utf-8"), (
        "models.py files have diverged — edit one, copy to the other"
    )
