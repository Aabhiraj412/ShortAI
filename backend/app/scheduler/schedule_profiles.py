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
    active_weekdays: Tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    # Target YouTube publication time of day ("HH:MM", cycle timezone). Informational for
    # production: it never moves the start, shortens recovery, or blocks completion.
    target_publish_time: Optional[str] = None

    def occurrence_start(self, current_dt_ist: Optional[datetime] = None) -> datetime:
        """
        Start of the cycle occurrence that `current_dt_ist` belongs to: yesterday's run
        while it is still inside its recovery window across midnight (e.g. a 22:00 cycle
        with 4 h recovery at 01:00), otherwise today's start.
        """
        dt = current_dt_ist or datetime.now(IST_TZ)
        today = dt.replace(hour=self.start_hour_ist, minute=self.start_min_ist, second=0, microsecond=0)
        if dt < today:
            prev = today - timedelta(days=1)
            if prev.weekday() in self.active_weekdays and dt < prev + timedelta(hours=self.recovery_window_hours):
                return prev
        return today

    def target_publication_at(self, cycle_start: datetime) -> Optional[datetime]:
        """First occurrence of target_publish_time after the production start (rolls past midnight)."""
        if not self.target_publish_time:
            return None
        hh, mm = (int(x) for x in str(self.target_publish_time).split(":", 1))
        target = cycle_start.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= cycle_start:
            target += timedelta(days=1)
        return target

    def calculate_datetimes(self, current_dt_ist: Optional[datetime] = None) -> Tuple[datetime, datetime, str, str]:
        """
        Calculates cycle start datetime, recovery deadline (legacy name target_pub),
        cycle date string, and deadline date string for a given time.
        """
        dt = current_dt_ist or datetime.now(IST_TZ)
        cycle_start = self.occurrence_start(dt)
        target_pub = cycle_start + timedelta(hours=self.window_hours)
        date_str = cycle_start.strftime("%Y-%m-%d")
        pub_date_str = target_pub.strftime("%Y-%m-%d")
        return cycle_start, target_pub, date_str, pub_date_str

    def format_job_id(self, current_dt_ist: Optional[datetime] = None) -> str:
        """Generates deterministic unique job ID for this cycle."""
        dt = current_dt_ist or datetime.now(IST_TZ)
        date_num = self.occurrence_start(dt).strftime("%Y%m%d")
        time_num = f"{self.start_hour_ist:02d}{self.start_min_ist:02d}"
        return f"{self.cycle_id}_{date_num}_{time_num}"

    def is_deadline_exceeded(self, cycle_start_dt: datetime, check_dt: Optional[datetime] = None) -> bool:
        """Returns True if the check time exceeds cycle start + window_hours."""
        now_dt = check_dt or datetime.now(IST_TZ)
        deadline = cycle_start_dt + timedelta(hours=self.window_hours)
        return now_dt > deadline

    def is_within_recovery_window(self, cycle_start_dt: datetime, check_dt: Optional[datetime] = None) -> bool:
        """Returns True during the half-open recovery interval [start, expiration)."""
        now_dt = check_dt or datetime.now(IST_TZ)
        rec_end = cycle_start_dt + timedelta(hours=self.recovery_window_hours)
        return cycle_start_dt <= now_dt < rec_end

    def is_past_recovery_window(self, cycle_start_dt: datetime, check_dt: Optional[datetime] = None) -> bool:
        """Returns True at or after recovery expiration."""
        now_dt = check_dt or datetime.now(IST_TZ)
        rec_end = cycle_start_dt + timedelta(hours=self.recovery_window_hours)
        return now_dt >= rec_end


@dataclass
class ScheduleProfile:
    """Configurable schedule profile holding one or more production cycles."""
    name: str
    description: str
    timezone_name: str = "Asia/Kolkata"
    cycles: List[ProductionCycle] = field(default_factory=list)
    publishing_mode: str = "manual_approval"
    schedule_version: int = 1
    revision: str = ""

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
            target_publish_time="06:00",
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
            target_publish_time="12:00",
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
            target_publish_time="18:00",
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
            target_publish_time="00:00",
            pub_rollover_days=1,
            window_hours=6.0,
            recovery_window_hours=4.0,
        ),
    ],
    publishing_mode="manual_approval",
)

# Recovery policies
POLICY_STRICT_WINDOW = "STRICT_WINDOW"
POLICY_CATCH_UP = "CATCH_UP"

def get_recovery_policy() -> str:
    """Return the enforced no-backfill recovery policy."""
    return POLICY_STRICT_WINDOW

# Profile 2: SINGLE_DAILY_2300 (Single Overnight Production Pipeline)
PROFILE_SINGLE_DAILY_2300 = ScheduleProfile(
    name="SINGLE_DAILY_2300",
    description="Single daily run in Asia/Kolkata: Generation @ 23:00 IST -> Target Publication @ 05:00 IST Next-Day (6-hour target)",
    timezone_name="Asia/Kolkata",
    cycles=[
        ProductionCycle(
            cycle_id="cycle_daily",
            name="Single Daily Run: 23:00 IST Start -> 05:00 IST Morning Target",
            start_hour_ist=23,
            start_min_ist=0,
            target_pub_hour_ist=5,
            target_pub_min_ist=0,
            target_publish_time="05:00",
            pub_rollover_days=1,
            window_hours=6.0,
            recovery_window_hours=4.0,
        ),
    ],
    publishing_mode="manual_approval",
)

AVAILABLE_PROFILES: Dict[str, ScheduleProfile] = {
    "FOUR_CYCLES_DAILY": PROFILE_FOUR_CYCLES_DAILY,
    "SINGLE_DAILY_2300": PROFILE_SINGLE_DAILY_2300,
}

# Runtime active profile selection (Default: FOUR_CYCLES_DAILY)
_CURRENT_PROFILE_NAME = os.environ.get("CRAZZYDOCS_SCHEDULE_PROFILE", "FOUR_CYCLES_DAILY")
_RUNTIME_PROFILE: Optional[ScheduleProfile] = None


def get_active_profile() -> ScheduleProfile:
    """Returns currently active schedule profile."""
    if _RUNTIME_PROFILE is not None:
        return _RUNTIME_PROFILE
    global _CURRENT_PROFILE_NAME
    return AVAILABLE_PROFILES.get(_CURRENT_PROFILE_NAME, PROFILE_FOUR_CYCLES_DAILY)


def set_active_profile(profile_name: str) -> ScheduleProfile:
    """Switches active schedule profile at runtime."""
    global _RUNTIME_PROFILE
    global _CURRENT_PROFILE_NAME
    if profile_name not in AVAILABLE_PROFILES:
        raise ValueError(
            f"Unknown profile '{profile_name}'. Available profiles: {list(AVAILABLE_PROFILES.keys())}"
        )
    _RUNTIME_PROFILE = None
    _CURRENT_PROFILE_NAME = profile_name
    return AVAILABLE_PROFILES[profile_name]


def set_runtime_profile(profile: ScheduleProfile) -> ScheduleProfile:
    """Installs an explicitly activated custom schedule in the current process."""
    global _CURRENT_PROFILE_NAME, _RUNTIME_PROFILE
    _RUNTIME_PROFILE = profile
    _CURRENT_PROFILE_NAME = profile.name
    return profile


def list_profiles() -> List[Dict]:
    """Returns metadata for all available schedule profiles."""
    profiles = [
        {
            "name": p.name,
            "description": p.description,
            "cycle_count": len(p.cycles),
            "is_active": (p.name == get_active_profile().name),
        }
        for p in AVAILABLE_PROFILES.values()
    ]
    active = get_active_profile()
    if active.name not in AVAILABLE_PROFILES:
        profiles.append({
            "name": active.name,
            "description": active.description,
            "cycle_count": len(active.cycles),
            "is_active": True,
        })
    return profiles
