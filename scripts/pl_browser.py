"""Optional headless-browser fetcher for athetize (Playwright + Chromium).

Used only as a fallback for open-access pages that need JavaScript to render or
that refuse plain HTTP clients. It does not try to defeat bot checks: when a site
shows a security-verification or captcha page, the fetch stops and reports it, so
the reference can go to the person's own browser instead (see the fetch-sources
skill).

Install once:  pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import importlib.util
import re
import threading

_LOCK = threading.Lock()

BOT_WALL = re.compile(
    r"performing security verification|verify you are (a )?human|just a moment\.\.\.|"
    r"checking your browser|are you a robot|captcha|access denied|unusual traffic|"
    r"there was a problem providing the content you requested",
    re.I,
)

# Page script that reads the PDF address a publisher page names in its citation meta tag.
_CITATION_PDF_JS = (
    "() => { const m = document.querySelector('meta[name=\"citation_pdf_url\"]'); "
    "return m ? m.content : null; }"
)


def available() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


def _request_pdf(ctx, url: str, timeout_ms: int):
    """Download `url` inside the browser context; return a "pdf" result, or None if not a PDF."""
    r = ctx.request.get(url, timeout=timeout_ms)
    body = r.body()
    if body[:5] == b"%PDF-":
        return {"kind": "pdf", "data": body, "final_url": url}
    return None


def _goto_failure(ctx, url: str, timeout_ms: int, msg: str) -> dict:
    """Result for a page load that raised; a direct file download is fetched if it is a PDF."""
    if "Download is starting" in msg:
        try:
            found = _request_pdf(ctx, url, timeout_ms)
            if found:
                return found
        except Exception as e2:
            return {"kind": "error", "reason": f"browser download: {e2}"[:200]}
    return {"kind": "error", "reason": f"browser: {msg}"[:200]}


def _response_pdf(resp, page):
    """Return a "pdf" result when the page response itself is a PDF, else None."""
    ctype = (resp.headers.get("content-type", "") if resp else "").lower()
    if resp and "pdf" in ctype:
        body = resp.body()
        if body[:5] == b"%PDF-":
            return {"kind": "pdf", "data": body, "final_url": page.url}
    return None


def _body_text(page) -> str:
    """Visible text of the page body, or "" when it cannot be read."""
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def _citation_pdf(ctx, page, timeout_ms: int):
    """Return a "pdf" result for the PDF named in the page's citation meta tag, else None."""
    # Publisher pages often name the PDF in a citation meta tag; fetch it in the same context.
    pdf_url = page.evaluate(_CITATION_PDF_JS)
    if pdf_url:
        try:
            return _request_pdf(ctx, pdf_url, timeout_ms)
        except Exception:
            pass
    return None


def _fetch_in_browser(browser, url: str, timeout_ms: int) -> dict:
    """Load `url` in a fresh browser context and work out what kind of result it gives."""
    ctx = browser.new_context(accept_downloads=True)
    page = ctx.new_page()
    try:
        resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    except Exception as e:  # downloads raise here
        return _goto_failure(ctx, url, timeout_ms, str(e))
    found = _response_pdf(resp, page)
    if found:
        return found
    page.wait_for_timeout(2500)
    text = _body_text(page)
    head = text[:3000]
    if BOT_WALL.search(head) and len(text) < 5000:
        return {
            "kind": "blocked",
            "reason": "site shows a bot check; use the person's browser",
            "final_url": page.url,
        }
    found = _citation_pdf(ctx, page, timeout_ms)
    if found:
        return found
    return {
        "kind": "html",
        "text": text,
        "final_url": page.url,
        "status": resp.status if resp else None,
    }


def fetch(url: str, timeout_ms: int = 45000) -> dict:
    """Return {"kind": "pdf"|"html"|"blocked"|"error", "data"/"text"/"reason", "final_url"}."""
    if not available():
        return {"kind": "error", "reason": "playwright not installed"}
    from playwright.sync_api import sync_playwright

    with _LOCK, sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            return _fetch_in_browser(browser, url, timeout_ms)
        finally:
            browser.close()
