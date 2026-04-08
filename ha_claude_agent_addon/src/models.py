"""Shared Pydantic models for the add-on HTTP API contract.

This file is duplicated in ha_claude_agent_addon/src/models.py.
The two copies MUST stay identical — see tests/test_models_sync.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Request body for POST /query."""

    prompt: str
    model: str
    system_prompt: str
    max_turns: int = 10
    effort: str = "medium"
    session_id: str | None = None
    exposed_entities: list[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """Response body from POST /query."""

    result_text: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    error_code: str | None = None
