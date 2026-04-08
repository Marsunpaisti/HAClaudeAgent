"""Shared Pydantic models for the add-on HTTP API contract.

This file is duplicated in ha_claude_agent_addon/src/models.py.
The two copies MUST stay identical — see tests/test_models_sync.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Request body for POST /query."""

    prompt: str = Field(max_length=3_000)
    model: str = Field(max_length=200)
    system_prompt: str = Field(max_length=20_000)
    max_turns: int = Field(default=10, ge=1, le=100)
    effort: str = Field(default="medium", max_length=20)
    session_id: str | None = Field(default=None, max_length=200)
    exposed_entities: list[str] = Field(default_factory=list, max_length=1_000)


class QueryResponse(BaseModel):
    """Response body from POST /query."""

    result_text: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    error_code: str | None = None
