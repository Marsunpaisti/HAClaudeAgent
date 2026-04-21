"""Shared Pydantic models for OpenAI-backend wire events.

This file is duplicated in custom_components/ha_claude_agent/openai_events.py.
The two copies MUST stay identical — see tests/test_models_sync.py.
"""

from __future__ import annotations

from pydantic import BaseModel


class OpenAIInitEvent(BaseModel):
    """Emitted once at stream start on the OpenAI backend.

    Carries the SQLiteSession key so the integration can store it in its
    existing session LRU cache the same way it consumes the Claude
    backend's SystemMessage(subtype="init") today.
    """

    session_id: str


class OpenAIResultEvent(BaseModel):
    """Emitted once at stream end on the OpenAI backend.

    Token counts are populated from the openai-agents Runner result. Cost
    tracking is not attempted on this backend — ``cost_usd`` stays 0 in
    the integration's ``_StreamResult`` because OpenAI-compatible
    endpoints return token counts only (pricing tables rot too fast to
    maintain).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
