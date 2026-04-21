"""Tests for bounded runtime state helpers."""

import pytest

from custom_components.ha_claude_agent import (
    BoundedConversationLockMap,
    MAX_SESSIONS,
)


def test_bounded_conversation_lock_map_reuses_lock_for_same_key():
    locks = BoundedConversationLockMap()

    first = locks.get_lock("conversation-1")
    second = locks.get_lock("conversation-1")

    assert first is second
    assert len(locks) == 1


def test_bounded_conversation_lock_map_evicts_oldest_key():
    locks = BoundedConversationLockMap()

    for idx in range(MAX_SESSIONS + 1):
        locks.get_lock(f"conversation-{idx}")

    assert len(locks) == MAX_SESSIONS
    assert "conversation-0" not in locks
    assert "conversation-1" in locks


@pytest.mark.asyncio
async def test_bounded_conversation_lock_map_keeps_held_lock() -> None:
    locks = BoundedConversationLockMap()

    held = locks.get_lock("conversation-held")
    await held.acquire()
    try:
        for idx in range(MAX_SESSIONS):
            locks.get_lock(f"conversation-{idx}")

        assert "conversation-held" in locks
        assert len(locks) == MAX_SESSIONS
        assert "conversation-0" not in locks
    finally:
        held.release()


@pytest.mark.asyncio
async def test_bounded_conversation_lock_map_prunes_released_lock_on_next_access() -> None:
    locks = BoundedConversationLockMap()

    held = locks.get_lock("conversation-held")
    await held.acquire()
    try:
        for idx in range(MAX_SESSIONS):
            locks.get_lock(f"conversation-{idx}")
    finally:
        held.release()

    locks.get_lock("conversation-next")

    assert len(locks) == MAX_SESSIONS
    assert "conversation-held" not in locks
