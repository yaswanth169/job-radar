"""HTTP with retries, backoff and explicit block detection."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_session: Optional[requests.Session] = None


class Blocked(Exception):
    """The site is refusing us - 403, 429 or a WAF challenge."""


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
        _session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return _session


def post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    attempts: int = 3,
) -> Any:
    """POST JSON, retrying only what is worth retrying."""
    session = get_session()
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)

    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            resp = session.post(url, json=payload, headers=all_headers, timeout=timeout)
            if resp.status_code in (403, 429):
                raise Blocked(f"HTTP {resp.status_code} from {url}")
            resp.raise_for_status()
            return resp.json()
        except Blocked:
            raise
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            # 4xx other than 429 will not change on retry.
            if status is not None and 400 <= status < 500:
                raise
            last = exc
        except (requests.RequestException, ValueError, OSError) as exc:
            last = exc
        if attempt < attempts - 1:
            delay = (2 ** attempt) + random.uniform(0, 0.75)
            logger.debug("retry %d for %s in %.1fs (%s)", attempt + 1, url, delay, last)
            time.sleep(delay)
    assert last is not None
    raise last
