"""One small JSON-over-HTTP helper, standard library only, shared by the Jellyfin and Maintainerr clients."""

import http.client
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit


class HttpError(Exception):
    """A request failed. The message names the path and the reason."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would send the API key to whatever host answers next, so it is an error instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        parts = urlsplit(newurl)
        where = f"{parts.scheme}://{parts.netloc}" if parts.netloc else "another address"
        raise HttpError(f"HTTP {code}: the server redirected to {where}; set the URL to the final address")


_OPENER = urllib.request.build_opener(_NoRedirect)


def request(method: str, url: str, path: str, headers: dict[str, str], body: Any = None, timeout: float = 10) -> Any:
    """Send one request; return the decoded JSON, or None for an empty reply. `path` is only for messages."""
    data = None if body is None else json.dumps(body).encode()
    # S310 is silenced here: config.py only accepts http(s) URLs.
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    for name, value in headers.items():
        req.add_header(name, value)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(20_000_000)
    except HttpError as e:
        raise HttpError(f"{path}: {e}") from None
    except urllib.error.HTTPError as e:
        raise HttpError(f"{path}: HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise HttpError(f"{path}: {e}") from e
    except (ValueError, http.client.HTTPException) as e:  # a bad header value, a garbled reply
        raise HttpError(f"{path}: {type(e).__name__}") from None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError as e:
        raise HttpError(f"{path}: the reply is not JSON") from e
