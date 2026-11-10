"""
Configurable Schedule Profiles and Production Cycle Definitions.
Phase 5, Stages 3.9C & 3.9D Production Standard.

Enforces Asia/Kolkata timezone awareness, four daily production cycles,
target publication deadlines, midnight date rollover, and runtime profile switching.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import os
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

IST_TZ = ZoneInfo("Asia/Kolkata")


@dataclass
class ProductionCycle:
    """Specification of an autonomous production and publication cycle."""
    cycle_id: str                      # e.g. "cycle_1"
    name: str                          # e.g. "Midnight Early Bird"
    start_hour_ist: int                # 0..23
    start_min_ist: int                 # 0..59
    target_pub_hour_ist: int           # 0..23
    target_pub_min_ist: int            # 0..59
    pub_rollover_days: int = 0         # 1 if target pub is next calendar day
    window_hours: float = 6.0          # Max generation SLA window before missed deadline
    recovery_window_hours: float = 4.0 # Time after cycle start within which recovery is valid (00-04, 06-10, 12-16, 18-22)

    def calculate_datetimes(self, current_dt_ist: Optional[datetime] = None) -> Tuple[datetime, datetime, str, str]:
        """
        Calculates cycle start datetime, target publication datetime,
        cycle date string, and publication date string for a given time.
        """
        dt = current_dt_ist or datetime.now(IST_TZ)
        cycle_start = dt.replace(
            hour=self.start_hour_ist,
            minute=self.start_min_ist,
            second=0,
            microsecond=0,
        )
        target_pub = cycle_start + timedelta(hours=self.window_hours)
        date_str = cycle_start.strftime("%Y-%m-%d")
        pub_date_str = target_pub.strftime("%Y-%m-%d")
        return cycle_start, target_pub, date_str, pub_date_str

    def format_job_id(self, current_dt_ist: Optional[datetime] = None) -> str:
        """Generates deterministic unique job ID for this cycle."""
        dt = current_dt_ist or datetime.now(IST_TZ)
        date_num = dt.strftime("%Y%m%d")
        time_num = f"{self.start_hour_ist:02d}{self.start_min_ist:02d}"
        return f"{self.cycle_id}_{date_num}_{time_num}"

    def is_deadline_exceeded(self, cycle_start_dt: datetime, check_dt: Optional[datetime] = None) -> bool:
        """Returns True if the check time exceeds cycle start + window_hours."""
        now_dt = check_dt or datetime.now(IST_TZ)
        deadline = cycle_start_dt + timedelta(hours=self.window_hours)
        return now_dt > deadline

    def is_within_recovery_window(self, cycle_start_dt: datetime, check_dt: Optional[datetime] = None) -> bool:
        """Returns True if check time is between cycle start and start + recovery_window_hours."""
        now_dt = check_dt or datetime.now(IST_TZ)
        rec_end = cycle_start_dt + timedelta(hours=self.recovery_window_hours)
        return cycle_start_dt <= now_dt <= rec_end

    def is_past_recovery_window(self, cycle_start_dt: datetime, check_dt: Optional[datetime] = None) -> bool:
        """Returns True if check time is strictly after cycle start + recovery_window_hours."""
        now_dt = check_dt or datetime.now(IST_TZ)
        rec_end = cycle_start_dt + timedelta(hours=self.recovery_window_hours)
        return now_dt > rec_end


@dataclass
class ScheduleProfile:
    """Configurable schedule profile holding one or more production cycles."""
    name: str
    description: str
    timezone_name: str = "Asia/Kolkata"
    cycles: List[ProductionCycle] = field(default_factory=list)
    publishing_mode: str = "automatic_public"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


# -----------------------------------------------------------------------------
# Profile Definitions
# -----------------------------------------------------------------------------

# Profile 1: FOUR_CYCLES_DAILY (Standard 4-Cycle Autonomous Schedule)
PROFILE_FOUR_CYCLES_DAILY = ScheduleProfile(
    name="FOUR_CYCLES_DAILY",
    description="Four daily cycles in Asia/Kolkata: 00:00, 06:00, 12:00, 18:00 start with 4-hour recovery windows",
    timezone_name="Asia/Kolkata",
    cycles=[
        ProductionCycle(
            cycle_id="cycle_1",
            name="Cycle 1: Midnight Production -> Morning 06:00 IST Target",
            start_hour_ist=0,
            start_min_ist=0,
            target_pub_hour_ist=6,
            target_pub_min_ist=0,
            pub_rollover_days=0,
            window_hours=6.0,
            recovery_window_hours=4.0,
        ),
        ProductionCycle(
            cycle_id="cycle_2",
            name="Cycle 2: Morning Production -> Noon 12:00 IST Target",
            start_hour_ist=6,
            start_min_ist=0,
            target_pub_hour_ist=12,
            target_pub_min_ist=0,
            pub_rollover_days=0,
            window_hours=6.0,
            recovery_window_hours=4.0,
        ),
        ProductionCycle(
            cycle_id="cycle_3",
            name="Cycle 3: Afternoon Production -> Evening 18:00 IST Target",
            start_hour_ist=12,
            start_min_ist=0,
            target_pub_hour_ist=18,
            target_pub_min_ist=0,
            pub_rollover_days=0,
            window_hours=6.0,
            recovery_window_hours=4.0,
        ),
        ProductionCycle(
            cycle_id="cycle_4",
            name="Cycle 4: Evening Production -> Midnight 00:00 IST Next-Day Target",
            start_hour_ist=18,
            start_min_ist=0,
            target_pub_hour_ist=0,
            target_pub_min_ist=0,
            pub_rollover_days=1,
            window_hours=6.0,
            recovery_window_hours=4.0,
        ),
    ],
    publishing_mode="automatic_public",
)

# Recovery policies
POLICY_STRICT_WINDOW = "STRICT_WINDOW"
POLICY_CATCH_UP = "CATCH_UP"

def get_recovery_policy() -> str:
    """Returns active recovery policy: 'CATCH_UP' (default) or 'STRICT_WINDOW'."""
    return os.environ.get("CRAZZYDOCS_RECOVERY_POLICY", POLICY_CATCH_UP).upper()

# Profile 2: SINGLE_DAILY_2300 (Single Overnight Production Pipeline)
PROFILE_SINGLE_DAILY_2300 = ScheduleProfile(
    name="SINGLE_DAILY_2300",
    description="Single daily run in Asia/Kolkata: Generation @ 23:00 IST, Publishing Queue @ 10:00 IST",
    timezone_name="Asia/Kolkata",
    cycles=[
        ProductionCycle(
            cycle_id="cycle_daily",
            name="Single Daily Run: 23:00 IST Start -> 10:00 IST Morning Target",
            start_hour_ist=23,
            start_min_ist=0,
            target_pub_hour_ist=10,
            target_pub_min_ist=0,
            pub_rollover_days=1,
            window_hours=11.0,
        ),
    ],
    publishing_mode="automatic_public",
)

AVAILABLE_PROFILES: Dict[str, ScheduleProfile] = {
    "FOUR_CYCLES_DAILY": PROFILE_FOUR_CYCLES_DAILY,
    "SINGLE_DAILY_2300": PROFILE_SINGLE_DAILY_2300,
}

# Runtime active profile selection (Default: FOUR_CYCLES_DAILY)
_CURRENT_PROFILE_NAME = os.environ.get("CRAZZYDOCS_SCHEDULE_PROFILE", "FOUR_CYCLES_DAILY")


def get_active_profile() -> ScheduleProfile:
    """Returns currently active schedule profile."""
    global _CURRENT_PROFILE_NAME
    return AVAILABLE_PROFILES.get(_CURRENT_PROFILE_NAME, PROFILE_FOUR_CYCLES_DAILY)


def set_active_profile(profile_name: str) -> ScheduleProfile:
    """Switches active schedule profile at runtime."""
    global _CURRENT_PROFILE_NAME
    if profile_name not in AVAILABLE_PROFILES:
        raise ValueError(
            f"Unknown profile '{profile_name}'. Available profiles: {list(AVAILABLE_PROFILES.keys())}"
        )
    _CURRENT_PROFILE_NAME = profile_name
    return AVAILABLE_PROFILES[profile_name]


def list_profiles() -> List[Dict]:
    """Returns metadata for all available schedule profiles."""
    return [
        {
            "name": p.name,
            "description": p.description,
            "cycle_count": len(p.cycles),
            "is_active": (p.name == _CURRENT_PROFILE_NAME),
        }
        for p in AVAILABLE_PROFILES.values()
    ]
