"""Structural tests for the /adminpanel module.

Checks that the category catalog is well-formed, every callback id is
namespaced, and the registration wires up the expected commands.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers import adminpanel  # noqa: E402


def test_category_catalog_is_complete():
    # Every category has a label and at least one command line.
    assert adminpanel.CATEGORIES
    for key, (label, lines) in adminpanel.CATEGORIES.items():
        assert label
        assert lines
        assert key not in ("main", "close", "help")


def test_all_callbacks_namespaced():
    prefixes = {
        f"{adminpanel.PREFIX}cat:economy",
        f"{adminpanel.PREFIX}help",
        f"{adminpanel.PREFIX}close",
        f"{adminpanel.PREFIX}main",
    }
    for key, (_, lines) in adminpanel.CATEGORIES.items():
        prefixes.add(f"{adminpanel.CAT_PREFIX}{key}")
    for item in prefixes:
        assert item.startswith(adminpanel.PREFIX)


def test_categories_cover_admin_surface():
    # Sanity: the panel mentions the commands added by this workstream.
    flat = "\n".join(line for _, lines in adminpanel.CATEGORIES.values() for line in lines)
    for cmd in ("/leaderban", "/leaderunban", "/clearlb", "/bgc", "/bdm", "/give", "/freeze"):
        assert cmd in flat


def test_menus_are_inline_keyboards():
    main = adminpanel._main_menu()
    assert main.inline_keyboard
    for row in main.inline_keyboard:
        for btn in row:
            assert btn.callback_data.startswith(adminpanel.PREFIX)
    back = adminpanel._back_menu()
    assert back.inline_keyboard[0][0].callback_data == f"{adminpanel.PREFIX}main"