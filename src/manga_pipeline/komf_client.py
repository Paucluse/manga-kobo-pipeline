"""Komf (Komga Metadata Fetcher) API client.

Triggers metadata identification for series in Komga
via the Komf REST API.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from manga_pipeline.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class KomfResult:
    """Result of a Komf metadata fetch operation."""

    success: bool
    status_code: int = 0
    error: str = ""


def trigger_series_identify(
    komf_base_uri: str,
    series_id: str,
    timeout: int = 60,
) -> KomfResult:
    """Trigger Komf to identify and fetch metadata for a series.

    Args:
        komf_base_uri: Komf server base URI (e.g. http://komf:8085).
        series_id: Komga series ID to identify.
        timeout: Request timeout in seconds.

    Returns:
        KomfResult with outcome.
    """
    url = f"{komf_base_uri.rstrip('/')}/api/identify"
    payload = {
        "seriesId": series_id,
        "provider": "komga",
    }
    logger.info("Triggering Komf identify for series: %s", series_id)

    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=timeout,
        )

        if resp.status_code in (200, 202, 204):
            logger.info("Komf identify triggered successfully for series %s.", series_id)
            return KomfResult(success=True, status_code=resp.status_code)
        else:
            error_msg = f"Komf identify failed: HTTP {resp.status_code} - {resp.text[:200]}"
            logger.warning(error_msg)
            return KomfResult(
                success=False,
                status_code=resp.status_code,
                error=error_msg,
            )

    except requests.ConnectionError as e:
        error_msg = f"Cannot connect to Komf at {komf_base_uri}: {e}"
        logger.warning(error_msg)
        return KomfResult(success=False, error=error_msg)
    except requests.Timeout:
        error_msg = f"Komf identify timed out after {timeout}s"
        logger.warning(error_msg)
        return KomfResult(success=False, error=error_msg)


def trigger_series_match(
    komf_base_uri: str,
    series_id: str,
    timeout: int = 60,
) -> KomfResult:
    """Trigger Komf to auto-match and apply metadata for a series.

    Uses the /api/match endpoint which automatically picks the best result.

    Args:
        komf_base_uri: Komf server base URI.
        series_id: Komga series ID.
        timeout: Request timeout in seconds.

    Returns:
        KomfResult with outcome.
    """
    url = f"{komf_base_uri.rstrip('/')}/api/match"
    payload = {
        "seriesId": series_id,
        "provider": "komga",
    }
    logger.info("Triggering Komf auto-match for series: %s", series_id)

    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=timeout,
        )

        if resp.status_code in (200, 202, 204):
            logger.info("Komf auto-match successful for series %s.", series_id)
            return KomfResult(success=True, status_code=resp.status_code)
        else:
            error_msg = f"Komf match failed: HTTP {resp.status_code} - {resp.text[:200]}"
            logger.warning(error_msg)
            return KomfResult(
                success=False,
                status_code=resp.status_code,
                error=error_msg,
            )

    except requests.ConnectionError as e:
        error_msg = f"Cannot connect to Komf at {komf_base_uri}: {e}"
        logger.warning(error_msg)
        return KomfResult(success=False, error=error_msg)
    except requests.Timeout:
        error_msg = f"Komf match timed out after {timeout}s"
        logger.warning(error_msg)
        return KomfResult(success=False, error=error_msg)
