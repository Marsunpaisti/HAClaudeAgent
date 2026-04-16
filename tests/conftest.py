"""Pytest configuration for ha_claude_agent tests.

Stubs out the hassil-dependent portions of Home Assistant so that
custom_components.ha_claude_agent.conversation can be imported in the test
environment without a full HA install.  All other homeassistant.* modules
import fine from the installed homeassistant wheel.
"""

from __future__ import annotations

import sys
import types


def _install_ha_conversation_stub() -> None:
    """Replace homeassistant.components.conversation with a minimal stub.

    The real module pulls in hassil (an NLP library) which is version-locked
    to the installed homeassistant wheel and does not match what hassil itself
    ships as a standalone package on the current Python version.  Only the
    conversation module has this problem; all other HA sub-packages import
    cleanly.
    """
    # Guard: if already patched (e.g. conftest loaded twice), skip.
    if "homeassistant.components.conversation" in sys.modules:
        return

    ha_conv_mod = types.ModuleType("homeassistant.components.conversation")

    class _ConversationEntity:
        """Minimal base class stand-in."""

    class _ConversationEntityFeature:
        CONTROL = 1

    ha_conv_mod.ConversationEntity = _ConversationEntity  # type: ignore[attr-defined]
    ha_conv_mod.ConversationEntityFeature = _ConversationEntityFeature  # type: ignore[attr-defined]
    ha_conv_mod.ConversationInput = type("ConversationInput", (), {})  # type: ignore[attr-defined]
    ha_conv_mod.ConversationResult = type("ConversationResult", (), {})  # type: ignore[attr-defined]
    ha_conv_mod.ChatLog = type("ChatLog", (), {})  # type: ignore[attr-defined]
    # AssistantContentDeltaDict is a TypedDict — at runtime it's just dict.
    ha_conv_mod.AssistantContentDeltaDict = dict  # type: ignore[attr-defined]
    ha_conv_mod.async_set_agent = lambda *a, **kw: None  # type: ignore[attr-defined]
    ha_conv_mod.async_unset_agent = lambda *a, **kw: None  # type: ignore[attr-defined]

    sys.modules["homeassistant.components.conversation"] = ha_conv_mod

    # Also wire into homeassistant.components namespace so that
    # `from homeassistant.components import conversation` resolves to the stub.
    import homeassistant.components  # noqa: PLC0415

    homeassistant.components.conversation = ha_conv_mod  # type: ignore[attr-defined]


# Install stub immediately at import time so it's in place before any test
# module is collected and imported.
_install_ha_conversation_stub()
