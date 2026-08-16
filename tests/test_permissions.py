"""Permission and fly-config validation unit tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers.admin import _validate_fly_settings  # noqa: E402
from utils.permissions import is_owner  # noqa: E402
from config import config  # noqa: E402


class TestPermissions:
    async def test_owner_matches_config(self):
        assert await is_owner(config.OWNER_ID)

    async def test_owner_rejects_others(self):
        assert not await is_owner(999999999)


class TestFlyConfigValidation:
    def test_valid_config_passes(self):
        cfg = {"min_mult": 1.1, "max_mult": 1.6, "risk": 0.2, "win_prob": 0.75}
        assert _validate_fly_settings("low", cfg) is None

    def test_min_greater_than_max_rejected(self):
        cfg = {"min_mult": 2.0, "max_mult": 1.0}
        assert _validate_fly_settings("low", cfg) is not None

    def test_negative_bet_rejected(self):
        cfg = {"min_bet": -100}
        assert _validate_fly_settings("low", cfg) is not None

    def test_invalid_probability_rejected(self):
        cfg = {"win_prob": 1.5}
        assert _validate_fly_settings("low", cfg) is not None

    def test_nan_rejected(self):
        cfg = {"min_mult": float("nan")}
        assert _validate_fly_settings("low", cfg) is not None
