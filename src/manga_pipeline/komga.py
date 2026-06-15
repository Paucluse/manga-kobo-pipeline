"""Komga REST API client.

Provides functions to interact with the Komga server:
- Trigger library scans after importing new files
- Query library and series information
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests
from requests.auth import HTTPBasicAuth

from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class KomgaScanResult:
    """Result of a Komga library scan trigger."""

    success: bool
    status_code: int = 0
    error: str = ""


def trigger_library_scan(
    base_uri: str,
    library_id: str,
    user: str,
    password: str,
    timeout: int = 30,
) -> KomgaScanResult:
    """Trigger a library scan in Komga.

    Args:
        base_uri: Komga server base URI (e.g. http://komga:25600).
        library_id: ID of the library to scan.
        user: Komga admin username.
        password: Komga admin password.
        timeout: Request timeout in seconds.

    Returns:
        KomgaScanResult with outcome.
    """
    url = f"{base_uri.rstrip('/')}/api/v1/libraries/{library_id}/scan"
    logger.info("Triggering Komga library scan: %s", url)

    try:
        resp = requests.post(
            url,
            auth=HTTPBasicAuth(user, password),
            timeout=timeout,
        )

        if resp.status_code in (200, 202):
            logger.info("Komga library scan triggered successfully.")
            return KomgaScanResult(success=True, status_code=resp.status_code)
        else:
            error_msg = f"Komga scan failed: HTTP {resp.status_code} - {resp.text[:200]}"
            logger.error(error_msg)
            return KomgaScanResult(
                success=False,
                status_code=resp.status_code,
                error=error_msg,
            )

    except requests.ConnectionError as e:
        error_msg = f"Cannot connect to Komga at {base_uri}: {e}"
        logger.error(error_msg)
        return KomgaScanResult(success=False, error=error_msg)
    except requests.Timeout:
        error_msg = f"Komga scan request timed out after {timeout}s"
        logger.error(error_msg)
        return KomgaScanResult(success=False, error=error_msg)


def wait_for_scan_complete(
    base_uri: str,
    user: str,
    password: str,
    max_wait: int = 120,
    poll_interval: int = 5,
) -> bool:
    """Wait for Komga to finish scanning (best-effort).

    Polls the task endpoint until no scanning tasks remain.

    Args:
        base_uri: Komga server base URI.
        user: Komga admin username.
        password: Komga admin password.
        max_wait: Maximum seconds to wait.
        poll_interval: Seconds between polls.

    Returns:
        True if scan completed, False if timed out.
    """
    url = f"{base_uri.rstrip('/')}/api/v1/tasks"
    elapsed = 0

    while elapsed < max_wait:
        try:
            resp = requests.get(
                url,
                auth=HTTPBasicAuth(user, password),
                timeout=10,
            )
            if resp.status_code == 200:
                tasks = resp.json()
                scanning = [t for t in tasks if "SCAN" in t.get("type", "").upper()]
                if not scanning:
                    logger.info("Komga scan completed.")
                    return True
        except (requests.RequestException, ValueError):
            pass  # Silently retry

        time.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning("Timed out waiting for Komga scan (waited %ds).", max_wait)
    return False


def get_library_id(
    base_uri: str,
    user: str,
    password: str,
    library_name: str | None = None,
) -> str | None:
    """Get the first library ID from Komga.

    If library_name is specified, returns the ID of the matching library.
    Otherwise returns the first library found.

    Args:
        base_uri: Komga server base URI.
        user: Komga admin username.
        password: Komga admin password.
        library_name: Optional name to match.

    Returns:
        Library ID string, or None if not found.
    """
    url = f"{base_uri.rstrip('/')}/api/v1/libraries"

    try:
        resp = requests.get(
            url,
            auth=HTTPBasicAuth(user, password),
            timeout=10,
        )
        if resp.status_code == 200:
            libraries = resp.json()
            if not libraries:
                return None
            if library_name:
                for lib in libraries:
                    if lib.get("name", "").lower() == library_name.lower():
                        return lib["id"]
            return libraries[0]["id"]
    except (requests.RequestException, ValueError) as e:
        logger.error("Failed to query Komga libraries: %s", e)

    return None
