"""Tests for chat/message binding on game sessions.

Verifies that a mines session started in one chat/message cannot be controlled
from another chat/message (group-safe callbacks).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import games as games_db  # noqa: E402
from games import mines as mines_game  # noqa: E402
from services.game_engine import GameError, NoActiveGame  # noqa: E402


def _session(user_id=1, chat_id=-1001, message_id=10, status="active"):
    return {
        "game_id": "s1",
        "game": "mines",
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "status": status,
        "bet": 1000,
        "state": {"mines": [0, 1], "revealed": [], "bomb_count": 2},
    }


async def _patched_get(monkeypatch, session):
    async def fake_get(_game_id):
        return session

    monkeypatch.setattr(games_db, "get_session", fake_get)


class TestSessionBinding:
    async def test_owner_can_control_in_same_chat(self, monkeypatch):
        session = _session()
        await _patched_get(monkeypatch, session)
        doc = await mines_game._owned_active_session(
            "s1", 1, chat_id=-1001, message_id=10
        )
        assert doc["game_id"] == "s1"

    async def test_wrong_chat_rejected(self, monkeypatch):
        await _patched_get(monkeypatch, _session())
        with pytest.raises(GameError, match="another chat"):
            await mines_game._owned_active_session("s1", 1, chat_id=-1002)

    async def test_wrong_message_rejected(self, monkeypatch):
        await _patched_get(monkeypatch, _session())
        with pytest.raises(GameError, match="message"):
            await mines_game._owned_active_session("s1", 1, message_id=99)

    async def test_unbound_session_accepts_any(self, monkeypatch):
        session = _session()
        session["chat_id"] = None
        session["message_id"] = None
        await _patched_get(monkeypatch, session)
        doc = await mines_game._owned_active_session(
            "s1", 1, chat_id=-555, message_id=7
        )
        assert doc["game_id"] == "s1"

    async def test_other_user_rejected(self, monkeypatch):
        await _patched_get(monkeypatch, _session(user_id=1))
        with pytest.raises(GameError, match="another user"):
            await mines_game._owned_active_session("s1", 2)

    async def test_settled_session_rejected(self, monkeypatch):
        await _patched_get(monkeypatch, _session(status="won"))
        with pytest.raises(NoActiveGame):
            await mines_game._owned_active_session("s1", 1)

    async def test_missing_session_rejected(self, monkeypatch):
        async def fake_get(_game_id):
            return None

        monkeypatch.setattr(games_db, "get_session", fake_get)
        with pytest.raises(NoActiveGame):
            await mines_game._owned_active_session("missing", 1)
