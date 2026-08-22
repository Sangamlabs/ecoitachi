"""Global Battle Missions System.

Defines 10 economy-related missions, auto-detects completion from bot activity,
and handles the 3/10 unlock for Global Event access.

ADMIN/OWNER PRE-UNLOCK: Bot Owner and SUDO admins have Global Event
pre-unlocked (no 3/10 missions required). Normal users must complete 3/10.
"""

from __future__ import annotations

import logging
from typing import Any

from config import config
from database import global_battle as gb_db
from database import users as users_db
from services import identity as identity_service
from utils.permissions import is_sudo

logger = logging.getLogger(__name__)

# Mission requirement types map to handler functions
# These are called from existing handlers after successful completion
MISSION_HANDLERS = {
    "command": "_record_command_completion",
    "game_complete": "_record_game_completion",
}


async def record_command_completion(user_id: int, command: str) -> None:
    """Record completion of a command-type mission."""
    missions = await gb_db.get_all_missions(active_only=True)
    for mission in missions:
        if mission["requirement_type"] == "command" and mission["requirement_value"] == command:
            await _process_mission_completion(user_id, mission["mission_id"])


async def record_game_completion(user_id: int, game: str) -> None:
    """Record completion of an economy game."""
    try:
        missions = await gb_db.get_all_missions(active_only=True)
    except Exception:
        # Database not initialized (e.g., in tests) - silently skip
        return
    for mission in missions:
        if mission["requirement_type"] == "game_complete":
            await _process_mission_completion(user_id, mission["mission_id"])


async def _process_mission_completion(user_id: int, mission_id: str) -> None:
    """Process a mission completion for a user."""
    # Check if already completed
    progress = await gb_db.get_mission_progress(user_id, mission_id)
    if progress and progress.get("completed"):
        return

    # Increment progress
    updated = await gb_db.increment_mission_progress(user_id, mission_id)

    # Check if this completes the mission (progress >= 1 for single-step missions)
    if updated.get("progress", 0) >= 1 and not updated.get("completed"):
        await gb_db.complete_mission(user_id, mission_id)
        logger.info("User %s completed mission %s", user_id, mission_id)

        # Check for global event unlock
        await _check_global_unlock(user_id)


async def _check_global_unlock(user_id: int) -> None:
    """Check if user has completed 3+ missions and unlock global event."""
    # Skip if already unlocked
    if await gb_db.is_global_unlocked(user_id):
        return

    # ADMIN/OWNER PRE-UNLOCK: Owner and SUDO admins get instant unlock
    if user_id == config.OWNER_ID or await is_sudo(user_id):
        await gb_db.unlock_global_event(user_id)
        logger.info("Admin/Owner %s pre-unlocked Global Event", user_id)
        return

    completed = await gb_db.count_completed_missions(user_id)
    required = 3  # From gphase.md: "3 OUT OF 10 MISSIONS"

    if completed >= required:
        await gb_db.unlock_global_event(user_id)
        logger.info("User %s unlocked Global Event (completed %d missions)", user_id, completed)


async def get_missions_ui(user_id: int) -> dict[str, Any]:
    """Get formatted mission data for /missions command."""
    missions = await gb_db.get_all_missions(active_only=True)
    progress_docs = await gb_db.get_user_progress(user_id)
    progress_map = {p["mission_id"]: p for p in progress_docs}

    completed_count = sum(1 for p in progress_docs if p.get("completed"))
    
    # ADMIN/OWNER PRE-UNLOCK: Check if user is owner or SUDO admin
    is_admin = user_id == config.OWNER_ID or await is_sudo(user_id)
    unlocked = await gb_db.is_global_unlocked(user_id) or is_admin

    mission_list = []
    for mission in missions:
        prog = progress_map.get(mission["mission_id"], {})
        mission_list.append({
            "mission_id": mission["mission_id"],
            "name": mission["name"],
            "description": mission["description"],
            "completed": prog.get("completed", False),
            "progress": prog.get("progress", 0),
        })

    return {
        "missions": mission_list,
        "completed_count": completed_count,
        "total_missions": len(mission_list),
        "required_for_unlock": 3,
        "unlocked": unlocked,
        "pre_unlocked": is_admin,
    }


async def format_missions_message(data: dict[str, Any]) -> str:
    """Format mission data for display."""
    lines = ["🌍 <b>GLOBAL EVENT MISSIONS</b>", ""]

    for i, m in enumerate(data["missions"], 1):
        status = "✅" if m["completed"] else "❌"
        lines.append(f"{i}. {status} {m['name']}")

    lines.append("")
    lines.append(f"Progress: <b>{data['completed_count']} / {data['total_missions']}</b>")
    lines.append(f"Need: <b>{data['required_for_unlock']} missions</b>")
    lines.append("")

    if data["unlocked"]:
        if data.get("pre_unlocked"):
            lines.append("Global Event: 🔓 <b>UNLOCKED (Admin Pre-Unlock)</b>")
        else:
            lines.append("Global Event: 🔓 <b>UNLOCKED</b>")
        lines.append('<a href="tg://resolve?domain=uno_reverse_god_bot&start=gbattle">[ 🌍 ENTER GLOBAL EVENT ]</a>')
    else:
        lines.append("Global Event: 🔒 <b>LOCKED</b>")

    return "\n".join(lines)


# Initialize missions on module load
async def initialize() -> None:
    """Initialize the missions system (called at startup)."""
    inserted = await gb_db.init_missions()
    if inserted:
        logger.info("Initialized %d default global battle missions", inserted)


async def is_pre_unlocked(user_id: int) -> bool:
    """Check if user has admin pre-unlock access."""
    return user_id == config.OWNER_ID or await is_sudo(user_id)