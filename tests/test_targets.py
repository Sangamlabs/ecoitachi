"""Tests for target user resolution (reply > explicit id > username)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.validators import parse_target_arg, target_from_message  # noqa: E402


def _user(uid, username=None):
    return SimpleNamespace(id=uid, username=username, first_name="X")


class TestTargetFromMessage:
    def test_reply_user_id_returned(self):
        msg = SimpleNamespace(reply_to_message=SimpleNamespace(from_user=_user(12345)))
        assert target_from_message(msg) == 12345

    def test_no_reply_returns_none(self):
        assert target_from_message(SimpleNamespace(reply_to_message=None)) is None

    def test_reply_without_user_returns_none(self):
        msg = SimpleNamespace(reply_to_message=SimpleNamespace(from_user=None))
        assert target_from_message(msg) is None


class TestParseTargetArg:
    def test_numeric_id(self):
        assert parse_target_arg("123456") == (123456, None)

    def test_username(self):
        assert parse_target_arg("@bob") == (-1, "bob")

    def test_amount_not_a_target(self):
        assert parse_target_arg("500") == (500, None)

    def test_blank_is_not_target(self):
        assert parse_target_arg("") is None
        assert parse_target_arg(None) is None

    def test_junk_is_not_target(self):
        assert parse_target_arg("hello world") is None
