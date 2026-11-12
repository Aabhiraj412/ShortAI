"""
Lightweight Agent Identity Configuration for CrazzyDocs.
Phase 5, Stage 3.15 Standard (Multi-Agent Foundation).

Defines the isolated agent identity under the D:\\Agents topology.
Each agent under D:\\Agents\\ operates independently with its own
codebase, database, media library, credentials, and schedules.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from backend.app.core.paths import get_project_root_str

@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str = "crazzydocs"
    agent_name: str = "CrazzyDocs"
    agent_root: str = get_project_root_str()
    timezone: str = "Asia/Kolkata"
    publishing_mode: str = "manual_approval"
    channel_handle: str = "@crazzydocs"
    channel_id: str = "UCUOZOEBngXgt6W7fmus0bvw"

AGENT_IDENTITY = AgentIdentity()

def get_agent_identity() -> AgentIdentity:
    """Returns the immutable AgentIdentity instance."""
    return AGENT_IDENTITY
