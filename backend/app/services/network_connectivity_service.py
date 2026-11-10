"""
Network Connectivity Service for CrazzyDocs Autonomous Production Studio.
Phase 5, Stage 3.10 Production Standard.

Provides:
- Configurable multi-target general connectivity checks.
- Provider-specific endpoint health probes (Wikimedia, Edge-TTS, YouTube API).
- Exponential backoff wait loop with deadline enforcement.
- Outage classification: General Internet Outage vs Provider-Specific Degraded.
"""

from datetime import datetime, timezone
import logging
import os
import socket
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_DNS_TARGETS = [
    ("1.1.1.1", 53),
    ("8.8.8.8", 53),
    ("9.9.9.9", 53),
]

DEFAULT_PROVIDER_TARGETS = {
    "wikimedia": ("commons.wikimedia.org", 443),
    "speech_tts": ("speech.platform.bing.com", 443),
    "youtube_api": ("www.googleapis.com", 443),
}


class NetworkConnectivityService:
    """Verifies external network accessibility and provider service health."""

    def __init__(
        self,
        dns_targets: Optional[List[Tuple[str, int]]] = None,
        provider_targets: Optional[Dict[str, Tuple[str, int]]] = None,
    ):
        self.dns_targets = dns_targets or DEFAULT_DNS_TARGETS
        self.provider_targets = provider_targets or DEFAULT_PROVIDER_TARGETS

    def check_general_connectivity(self, timeout: float = 2.0) -> bool:
        """
        Attempts fast TCP socket connections to root DNS servers.
        Returns True if at least one target is reachable.
        """
        for host, port in self.dns_targets:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((host, port))
                sock.close()
                return True
            except (socket.timeout, OSError):
                continue
        return False

    def check_provider_services(self, timeout: float = 2.5) -> Dict[str, bool]:
        """Probes individual upstream API and media repositories."""
        results = {}
        for provider_name, (host, port) in self.provider_targets.items():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((host, port))
                sock.close()
                results[provider_name] = True
            except (socket.timeout, OSError):
                results[provider_name] = False
        return results

    def is_environment_ready_for_cycle(
        self,
        require_all_providers: bool = False,
        timeout: float = 2.0,
    ) -> Tuple[bool, str, Dict]:
        """
        Evaluates overall readiness before initiating a production cycle.
        Returns (is_ready, reason_message, details_dict).
        """
        if not self.check_general_connectivity(timeout=timeout):
            return False, "GENERAL_INTERNET_UNAVAILABLE", {"general": False, "providers": {}}

        prov_status = self.check_provider_services(timeout=timeout)
        if require_all_providers and not all(prov_status.values()):
            failed_provs = [k for k, v in prov_status.items() if not v]
            return (
                False,
                f"PROVIDER_SERVICES_UNAVAILABLE: {', '.join(failed_provs)}",
                {"general": True, "providers": prov_status},
            )

        return True, "READY", {"general": True, "providers": prov_status}

    def wait_for_connectivity(
        self,
        max_wait_seconds: float = 300.0,
        initial_backoff: float = 5.0,
        deadline_dt: Optional[datetime] = None,
        probe_fn: Optional[callable] = None,
        sleep_fn: Optional[callable] = None,
    ) -> bool:
        """
        Polls for network availability with exponential backoff until reachable,
        max_wait_seconds elapses, or deadline_dt is reached.
        """
        probe = probe_fn or self.check_general_connectivity
        sleeper = sleep_fn or time.sleep

        t_start = time.time()
        backoff = initial_backoff
        max_backoff = 30.0

        while True:
            # Check deadline first
            if deadline_dt and datetime.now(deadline_dt.tzinfo) > deadline_dt:
                logger.warning("[NETWORK] Recovery deadline exceeded while waiting for internet.")
                return False

            if time.time() - t_start >= max_wait_seconds:
                logger.warning("[NETWORK] Maximum wait time (%.1fs) exceeded without connectivity.", max_wait_seconds)
                return False

            if probe():
                logger.info("[NETWORK] Internet connectivity confirmed operational.")
                return True

            logger.info("[NETWORK] Internet unavailable. Retrying in %.1fs...", backoff)
            sleeper(backoff)
            backoff = min(backoff * 1.5, max_backoff)


network_connectivity_service = NetworkConnectivityService()
