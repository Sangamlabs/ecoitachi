"""Unit tests for the colour game (pure logic, no database)."""

import pytest

from games.colour import (
    build_keyboard,
    derive_colour,
    derive_size,
    is_expired,
    match_count,
    multiplier_for,
    parse_callback,
    payout_for,
    roll_result,
    selections_complete,
    validate_ownership,
)
from services.game_engine import GameError, NoActiveGame
from services.settings import GAME_DEFAULTS
from utils.money import parse_amount
from utils.validators import parse_amount_or_error


def _cfg(**overrides):
    cfg = {
        "match_multipliers": [0.0, 1.5, 4.0, 8.0],
        "max_multiplier": 8.0,
        "max_payout": 100_000_000,
    }
    cfg.update(overrides)
    return cfg


def _session(**overrides):
    doc = {
        "game_id": "sid1",
        "game": "colour",
        "user_id": 1,
        "chat_id": 10,
        "message_id": 100,
        "bet": 100_000,
        "status": "active",
        "state": {"size": None, "colour": None, "number": None},
        "expires_at": 10**12,
    }
    doc.update(overrides)
    return doc


class TestAmountParser:
    def test_basic_amount(self):
        assert parse_amount("1000") == 100_000

    def test_human_readable_amounts(self):
        assert parse_amount("10k") == 10_000 * 100
        assert parse_amount("1m") == 1_000_000 * 100
        assert parse_amount("1 lakh") == 100_000 * 100

    def test_invalid_amount_rejected(self):
        assert parse_amount_or_error("")[1] is not None
        assert parse_amount_or_error("abc")[1] is not None

    def test_zero_and_negative_rejected(self):
        assert parse_amount_or_error("0")[1] is not None
        assert parse_amount_or_error("-5")[1] is not None


class TestDerivation:
    def test_colour_mapping(self):
        expected = {"0": "VIOLET", "1": "RED", "2": "GREEN", "3": "RED",
                    "4": "GREEN", "5": "RED", "6": "GREEN", "7": "RED",
                    "8": "GREEN", "9": "BLUE"}
        for n in range(10):
            assert derive_colour(n) == expected[str(n)]

    def test_size_mapping(self):
        expected = {"0": "SMALL", "1": "SMALL", "2": "SMALL", "3": "SMALL",
                    "4": "MEDIUM", "5": "MEDIUM", "6": "MEDIUM",
                    "7": "BIG", "8": "BIG", "9": "BIG"}
        for n in range(10):
            assert derive_size(n) == expected[str(n)]

    def test_roll_result_in_range(self):
        for _ in range(200):
            assert 0 <= roll_result() <= 9


class TestMatching:
    def test_big_green_7_example(self):
        picks = {"size": "BIG", "colour": "GREEN", "number": "7"}
        assert match_count(picks, 7) == 2  # size + number (7 -> RED)

    def test_number_match_implies_all(self):
        picks = {"size": "SMALL", "colour": "GREEN", "number": "2"}
        assert match_count(picks, 2) == 3

    def test_partial_matches(self):
        picks = {"size": "BIG", "colour": "GREEN", "number": "7"}
        assert match_count(picks, 9) == 1  # size only (9 -> BLUE/BIG)

    def test_no_match(self):
        picks = {"size": "MEDIUM", "colour": "BLUE", "number": "7"}
        assert match_count(picks, 2) == 0  # 2 -> SMALL/GREEN


class TestPayout:
    def test_no_match_pays_zero(self):
        assert payout_for(1000, 0, _cfg()) == 0

    def test_one_match(self):
        assert payout_for(100_000, 1, _cfg()) == 150_000

    def test_two_matches(self):
        assert payout_for(100_000, 2, _cfg()) == 400_000

    def test_three_matches(self):
        assert payout_for(100_000, 3, _cfg()) == 800_000

    def test_max_multiplier_cap(self):
        cfg = _cfg(max_multiplier=5.0)
        assert payout_for(100_000, 3, cfg) == 500_000

    def test_max_payout_cap(self):
        cfg = _cfg(max_payout=500_000)
        assert payout_for(100_000_000, 3, cfg) == 500_000

    def test_payout_never_negative(self):
        for matches in range(4):
            assert payout_for(10**9, matches, _cfg()) >= 0

    def test_multiplier_capped_and_bounded(self):
        assert multiplier_for(3, _cfg(max_multiplier=8.0)) == 8.0
        assert multiplier_for(99, _cfg()) == 8.0  # beyond table uses last entry


class TestKeyboard:
    def test_layout(self):
        kb = build_keyboard("sid1", {"size": None, "colour": None, "number": None})
        assert len(kb.inline_keyboard) == 5
        assert [len(row) for row in kb.inline_keyboard] == [3, 4, 5, 5, 1]

    def test_callback_data_size(self):
        for row in build_keyboard("sid1", {"size": None, "colour": None, "number": None}).inline_keyboard:
            for btn in row:
                assert len(btn.callback_data) <= 64

    def test_selection_markers(self):
        kb = build_keyboard("sid1", {"size": "BIG", "colour": "GREEN", "number": "5"})
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "⬆️ BIG ✅" in labels
        assert "🟢 GREEN ✅" in labels
        assert "5 ✅" in labels
        assert "🎯 PLAY" in labels[-1]

    def test_play_gated_when_incomplete(self):
        kb = build_keyboard("sid1", {"size": "BIG", "colour": None, "number": "5"})
        assert kb.inline_keyboard[-1][0].text == "🎯 PLAY (select all)"
        assert not selections_complete({"size": "BIG", "colour": None, "number": "5"})

    def test_selections_complete_includes_zero_number(self):
        assert selections_complete({"size": "SMALL", "colour": "VIOLET", "number": "0"})


class TestCallbackCodec:
    def test_parse_round_trip(self):
        assert parse_callback("colour:sid:play") == ("sid", "play", "")
        assert parse_callback("colour:sid:size:BIG") == ("sid", "size", "BIG")
        assert parse_callback("colour:sid:col:RED") == ("sid", "col", "RED")
        assert parse_callback("colour:sid:num:7") == ("sid", "num", "7")

    def test_parse_rejects_invalid(self):
        assert parse_callback("mines:sid:play") is None
        assert parse_callback("colour:sid:size:XXL") is None
        assert parse_callback("colour:sid:col:BANANA") is None
        assert parse_callback("colour:sid:num:11") is None
        assert parse_callback("colour:sid:unknown:x") is None


class TestOwnership:
    def test_missing_session(self):
        with pytest.raises(NoActiveGame):
            validate_ownership(None, 1)

    def test_owner_only(self):
        with pytest.raises(GameError):
            validate_ownership(_session(), 999)

    def test_settled_session_not_replayable(self):
        settled = _session(status="won")
        with pytest.raises(NoActiveGame):
            validate_ownership(settled, 1)

    def test_chat_and_message_binding(self):
        with pytest.raises(GameError):
            validate_ownership(_session(), 1, chat_id=99)
        with pytest.raises(GameError):
            validate_ownership(_session(), 1, message_id=99)
        validate_ownership(_session(), 1, chat_id=10, message_id=100)

    def test_expiry_check(self):
        assert is_expired(_session(expires_at=0))
        assert not is_expired(_session(expires_at=10**12), now=5)
        assert not is_expired(_session(expires_at=None))


class TestDefaults:
    def test_game_defaults_configured(self):
        cfg = GAME_DEFAULTS["colour"]
        assert cfg["minimum_bet"] <= cfg["maximum_bet"]
        assert cfg["duration"] >= 1
        assert cfg["max_payout"] > 0
        assert len(cfg["match_multipliers"]) >= 2