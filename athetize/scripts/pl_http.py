"""HTTP access for athetize: one GET helper that only speaks http and https.

Every network request the scripts make goes through `http_get`. The opener has no
file, ftp or data handlers, so neither a URL nor a redirect can reach a local file
or another scheme.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_MANIFEST = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
_PLUGIN: dict = json.loads(_MANIFEST.read_text("utf-8"))
VERSION: str = _PLUGIN["version"]
# Where the plugin lives on the web: its source code and issues.
HOMEPAGE: str = _PLUGIN["homepage"]
ALLOWED_SCHEMES = ("http", "https")


class HttpError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        cafile = os.environ.get(var)
        if cafile and os.path.exists(cafile):
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def _opener() -> urllib.request.OpenerDirector:
    """An opener limited to http and https (system proxies are still honoured)."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(context=_ssl_context()),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def http_get(
    url: str,
    email: str | None = None,
    accept: str = "*/*",
    timeout: int = 25,
    max_bytes: int = 60_000_000,
):
    """GET a URL. Returns (body bytes, content type, final URL) or raises HttpError."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise HttpError(f"unsupported URL scheme {parts.scheme!r}: only http and https are fetched")
    ua = f"athetize/{VERSION}" + (f" (mailto:{email})" if email else "")
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": accept})
    try:
        with _opener().open(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise HttpError("response too large")
            return data, resp.headers.get("Content-Type", ""), resp.geturl()
    except urllib.error.HTTPError as e:
        raise HttpError(f"HTTP {e.code} for {parts.netloc}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HttpError(f"network error for {parts.netloc}: {getattr(e, 'reason', e)}") from e


def http_json(url: str, email: str | None = None):
    data, _, _ = http_get(url, email=email, accept="application/json")
    return json.loads(data.decode("utf-8", "replace"))
