"""Tests for chat type classification and the centralized chat gate."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyrogram.enums import ChatType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import group_config as group_config_service  # noqa: E402
from utils.chat import UNSUPPORTED, chat_type, check_gate  # noqa: E402


def _chat(t):
    return SimpleNamespace(type=t, id=-1001234567890)


def _message(chat_type_value, from_id=99):
    chat = _chat(chat_type_value)
    return SimpleNamespace(
        chat=chat,
        from_user=SimpleNamespace(id=from_id),
    )


def _cfg(cfg):
    async def get_group_config(_chat_id):
        return cfg

    return get_group_config


class TestChatType:
    def test_classifies_private(self):
        assert chat_type(_chat(ChatType.PRIVATE)) == "PRIVATE"

    def test_classifies_basic_group(self):
        assert chat_type(_chat(ChatType.GROUP)) == "GROUP"

    def test_classifies_supergroup(self):
        assert chat_type(_chat(ChatType.SUPERGROUP)) == "SUPERGROUP"

    def test_classifies_channel_unsupported(self):
        assert chat_type(_chat(ChatType.CHANNEL)) == UNSUPPORTED

    def test_classifies_bot_unsupported(self):
        assert chat_type(_chat(ChatType.BOT)) == UNSUPPORTED


@pytest.fixture
def defaults():
    return dict(group_config_service.DEFAULT_GROUP_CONFIG)


class TestChatGate:
    async def test_private_always_allowed(self, monkeypatch):
        async def _get(*_a, **_k):
            raise AssertionError("group config must not be read for private chats")

        monkeypatch.setattr(group_config_service, "get_group_config", _get)
        allowed, reason = await check_gate(_message(ChatType.PRIVATE), feature="economy")
        assert allowed is True
        assert reason is None

    async def test_unsupported_chat_rejected(self, monkeypatch, defaults):
        monkeypatch.setattr(
            group_config_service, "get_group_config", lambda _c: defaults
        )
        allowed, reason = await check_gate(_message(ChatType.CHANNEL), feature="economy")
        assert allowed is False
        assert "private chats and groups" in reason

    async def test_disabled_group_is_silent(self, monkeypatch, defaults):
        cfg = dict(defaults, group_enabled=False)
        monkeypatch.setattr(group_config_service, "get_group_config", _cfg(cfg))
        allowed, reason = await check_gate(_message(ChatType.SUPERGROUP), feature="economy")
        assert allowed is False
        assert reason is None

    async def test_feature_none_needs_only_group_enabled(self, monkeypatch, defaults):
        cfg = dict(defaults, economy_enabled=False)
        monkeypatch.setattr(group_config_service, "get_group_config", _cfg(cfg))
        allowed, _ = await check_gate(_message(ChatType.GROUP))
        assert allowed is True

    async def test_games_disabled_blocks_games(self, monkeypatch, defaults):
        cfg = dict(defaults, games_enabled=False)
        monkeypatch.setattr(group_config_service, "get_group_config", _cfg(cfg))
        allowed, reason = await check_gate(_message(ChatType.GROUP), feature="games")
        assert allowed is False
        assert "Games" in reason

    async def test_economy_disabled_blocks_economy(self, monkeypatch, defaults):
        cfg = dict(defaults, economy_enabled=False)
        monkeypatch.setattr(group_config_service, "get_group_config", _cfg(cfg))
        allowed, reason = await check_gate(_message(ChatType.SUPERGROUP), feature="economy")
        assert allowed is False
        assert "economy" in reason

    async def test_admin_disabled_blocks_sudo(self, monkeypatch, defaults):
        cfg = dict(defaults, admin_commands_enabled=False)
        monkeypatch.setattr(group_config_service, "get_group_config", _cfg(cfg))
        allowed, _ = await check_gate(_message(ChatType.GROUP, from_id=42), feature="admin")
        assert allowed is False

    async def test_admin_disabled_owner_bypasses(self, monkeypatch, defaults):
        from config import config

        cfg = dict(defaults, admin_commands_enabled=False)
        monkeypatch.setattr(group_config_service, "get_group_config", _cfg(cfg))
        allowed, reason = await check_gate(
            _message(ChatType.GROUP, from_id=config.OWNER_ID), feature="admin"
        )
        assert allowed is True
        assert reason is None

    async def test_chat_control_bypasses_everything(self, monkeypatch, defaults):
        cfg = dict(defaults, group_enabled=False)
        monkeypatch.setattr(group_config_service, "get_group_config", _cfg(cfg))
        allowed, _ = await check_gate(_message(ChatType.GROUP), feature="chat_control")
        assert allowed is True
