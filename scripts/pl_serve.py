"""A helper on this computer that lets the report page add source files.

A report opened as a file cannot save anything, so ``pl.py serve`` shows the report from
an address on 127.0.0.1 instead. The page sends the files the person chooses here; they are
saved in the check folder, read, matched to their references and added.

The files stay on this computer. The helper listens on 127.0.0.1 only, every address
carries a random token made for this run, and requests whose Host or Origin is not this
helper are refused, so another web page cannot reach it.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast

import pl_core
import pl_resolve
from pl_cmd_setup import out
from pl_cmd_sources import import_paths, source_hashes
from pl_identify import _ref_title
from pl_workspace import Work

# Largest single file the page may send, in bytes.
MAX_UPLOAD = 300 * 1024 * 1024
# Folder inside the check folder where files from the page wait to be matched.
INCOMING = "incoming"
# Folder inside that one for files that could not be matched to a reference.
UNRECOGNISED = "not-recognised"
_JSON = "application/json; charset=utf-8"
_HTML = "text/html; charset=utf-8"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_CHUNK = 64 * 1024
# Largest list of file hashes the page may send, in bytes.
_MAX_LIST = 1024 * 1024
_RESULT_LISTS = ("matched", "ambiguous", "unmatched", "duplicates", "already_imported")


# The Mac's own open panel, asked to accept files and folders together. A web page can only
# offer a files dialog or a folder dialog, never one for both; the helper runs on the
# person's own computer, so it can show the system's.
_MAC_CHOOSER = """
ObjC.import('AppKit');
var app = $.NSApplication.sharedApplication;
app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
var panel = $.NSOpenPanel.openPanel;
panel.canChooseFiles = true;
panel.canChooseDirectories = true;
panel.allowsMultipleSelection = true;
panel.message = $('Choose the papers, or a folder that holds them');
panel.prompt = $('Add');
app.activateIgnoringOtherApps(true);
var chosen = [];
if (panel.runModal == $.NSModalResponseOK) {
  var urls = panel.URLs;
  for (var i = 0; i < urls.count; i++) chosen.push(ObjC.unwrap(urls.objectAtIndex(i).path));
}
JSON.stringify(chosen);
"""
# How long the system chooser may stay open before the request is given up, in seconds.
_CHOOSER_WAIT = 900


def has_system_chooser() -> bool:
    """True where the helper can open one system chooser for files and folders (a Mac)."""
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def choose_paths() -> list[str]:
    """Open the system chooser and return the files and folders picked; [] when cancelled."""
    res = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", _MAC_CHOOSER],
        capture_output=True,
        timeout=_CHOOSER_WAIT,
    )
    if res.returncode != 0:
        raise Refused(500, "The system chooser could not be opened.")
    return [str(p) for p in json.loads(res.stdout.decode("utf-8") or "[]")]


class Refused(Exception):
    """A request the helper will not serve, with the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def safe_name(name: str) -> str:
    """A file name with no folders in it and only ordinary characters; '' when nothing is left."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^\w.() \-]+", "_", base).strip(" .")


def free_path(folder: Path, name: str) -> Path:
    """A path in ``folder`` for ``name`` that does not overwrite a file already there."""
    path = folder / name
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
    return path


class AddSources:
    """What the helper does for one check folder."""

    def __init__(self, work: Work, port: int):
        self.work = work
        self.port = port
        self.token = secrets.token_urlsafe(16)
        self.added: list[dict] = []
        self.left: dict[str, str] = {}
        self.chosen: list[str] = []
        self.finished = False
        self.last_seen = time.monotonic()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/report.html"

    def _hosts(self) -> tuple[str, str]:
        return (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def check(self, method: str, raw_path: str, headers) -> tuple[str, dict]:
        """Refuse a request that is not for this helper; return its route and query otherwise."""
        if headers.get("Host") not in self._hosts():
            raise Refused(403, "This address is not served here.")
        parts = urllib.parse.urlsplit(raw_path)
        token, _, route = parts.path.lstrip("/").partition("/")
        if not hmac.compare_digest(token.encode(), self.token.encode()):
            raise Refused(404, "Not found.")
        if method == "POST" and headers.get("Origin") not in tuple(
            "http://" + h for h in self._hosts()
        ):
            raise Refused(403, "Requests must come from the report page.")
        return route, urllib.parse.parse_qs(parts.query)

    def report(self) -> bytes:
        """The report, rebuilt so it shows sources added a moment ago."""
        return pl_core.build_report(self.work).read_bytes()

    def word(self) -> tuple[str, bytes]:
        """Name and content of the Word copy with suggested edits, when one has been written."""
        path = self.work.p(pl_core.word_export_name(self.work.paper()))
        if not path.exists():
            raise Refused(404, "No Word report has been written for this check yet.")
        return path.name, path.read_bytes()

    def save(self, name: str, length: int, stream) -> dict:
        """Save one file sent by the page into the incoming folder."""
        clean = safe_name(name)
        if Path(clean).suffix.lower() not in pl_resolve.USER_FILE_EXTS:
            kinds = ", ".join(pl_resolve.USER_FILE_EXTS)
            raise Refused(415, f"{clean or 'This file'} is not a type Athetize reads ({kinds}).")
        if length <= 0:
            raise Refused(400, f"{clean} is empty.")
        if length > MAX_UPLOAD:
            raise Refused(413, f"{clean} is larger than {MAX_UPLOAD // (1024 * 1024)} MB.")
        folder = self.work.p(INCOMING)
        folder.mkdir(exist_ok=True)
        dest = free_path(folder, clean)
        with open(dest, "wb") as fh:
            left = length
            while left > 0:
                block = stream.read(min(_CHUNK, left))
                if not block:
                    break
                fh.write(block)
                left -= len(block)
        if left:
            dest.unlink()
            raise Refused(400, f"{clean} did not arrive whole.")
        return {"saved": dest.name}

    def take_in(self) -> dict:
        """Match the waiting files to references, add the ones recognised and say what happened."""
        folder = self.work.p(INCOMING)
        if not folder.is_dir():
            return {key: [] for key in _RESULT_LISTS}
        result = import_paths(self.work, [folder])
        self._note_added(result)
        filed = [m["file"] for m in result["matched"]] + result["already_imported"]
        for name in filed + [d["file"] for d in result["duplicates"]]:
            (folder / name).unlink()
        for item in result["ambiguous"]:
            self._set_aside(item, "could be " + " or ".join(item["could_be"]))
        for item in result["unmatched"]:
            self._set_aside(item, item["why"])
        result.pop("dry_run")
        return result

    def choose(self) -> dict:
        """Let the person pick files or folders in the system chooser, then add what matches.

        The files are read where they are and copied into the check; the person's own
        files and folders are never moved, renamed or deleted.
        """
        if not has_system_chooser():
            raise Refused(404, "This computer has no single chooser for files and folders.")
        picked = [Path(p) for p in choose_paths()]
        if not picked:
            return {"cancelled": True}
        readable = [
            p for p in picked if p.is_dir() or p.suffix.lower() in pl_resolve.USER_FILE_EXTS
        ]
        result = import_paths(self.work, readable, recursive=True)
        self._note_added(result)
        for item in result["ambiguous"]:
            self.left[item["file"]] = "could be " + " or ".join(item["could_be"])
        for item in result["unmatched"]:
            self.left[item["file"]] = item["why"]
        self.chosen += [str(p) for p in picked]
        result.pop("dry_run")
        result["other_files"] = len(picked) - len(readable)
        return result

    def _note_added(self, result: dict) -> None:
        """Give each added file its reference's title and remember it for the summary."""
        refs = {r["id"]: r for r in self.work.refs()}
        for m in result["matched"]:
            m["title"] = _ref_title(refs[m["ref"]]) or pl_core._clean_raw(refs[m["ref"]]["raw"])
            self.added.append({k: m[k] for k in ("file", "ref", "status")})

    def known(self, body: bytes) -> dict:
        """Which of the content hashes the page asks about are already among the sources."""
        try:
            asked = json.loads(body.decode("utf-8"))["hashes"]
        except (ValueError, KeyError, TypeError) as exc:
            raise Refused(400, "The list of files could not be read.") from exc
        if not isinstance(asked, list):
            raise Refused(400, "The list of files could not be read.")
        have = source_hashes(self.work)
        return {"known": [h for h in asked if h in have]}

    def _set_aside(self, item: dict, why: str) -> None:
        """Keep a file that was not recognised, out of the way of the next import."""
        aside = self.work.p(INCOMING, UNRECOGNISED)
        aside.mkdir(exist_ok=True)
        kept = free_path(aside, item["file"])
        self.work.p(INCOMING, item["file"]).rename(kept)
        self.left[kept.name] = why

    def summary(self, reason: str) -> dict:
        """What was added while the helper ran, for Claude to act on."""
        waiting = self.work.p(INCOMING, UNRECOGNISED)
        return {
            "stopped": reason,
            "added": self.added,
            "not_recognised": [{"file": f, "why": why} for f, why in sorted(self.left.items())],
            "not_recognised_folder": str(waiting) if waiting.is_dir() else None,
            "chosen_in_system_chooser": self.chosen,
            "next": "Run the recheck step." if self.added else "Nothing was added.",
        }


class _Handler(BaseHTTPRequestHandler):
    """Answers the report page. The names do_GET and do_POST are fixed by the base class."""

    server_version = "Athetize"
    timeout = 60

    def log_message(self, format, *args):  # the base class writes to stderr; stay quiet
        return

    def _answer(self, status: int, ctype: str, body: bytes, download: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._answer(status, _JSON, json.dumps(data).encode("utf-8"))

    def _serve(self, method: str, routes: dict) -> None:
        app = cast(HelperServer, self.server).app
        app.last_seen = time.monotonic()
        try:
            route, query = app.check(method, self.path, self.headers)
            if route not in routes:
                raise Refused(404, "Not found.")
            routes[route](app, query)
        except Refused as refused:
            self._json(refused.status, {"error": refused.message})

    def do_GET(self):
        self._serve(
            "GET",
            {
                "report.html": lambda app, q: self._answer(200, _HTML, app.report()),
                "api/ping": lambda app, q: self._json(
                    200, {"ok": True, "chooser": has_system_chooser()}
                ),
                "word.docx": self._word,
            },
        )

    def do_POST(self):
        self._serve(
            "POST",
            {
                "api/upload": self._upload,
                "api/known": self._known,
                "api/choose": lambda app, q: self._json(200, app.choose()),
                "api/import": lambda app, q: self._json(200, app.take_in()),
                "api/done": self._done,
            },
        )

    def _word(self, app: AddSources, query: dict) -> None:
        name, body = app.word()
        self._answer(200, _DOCX, body, download=safe_name(name))

    def _upload(self, app: AddSources, query: dict) -> None:
        length = self.headers.get("Content-Length") or ""
        if not length.isdigit():
            raise Refused(411, "The file's size was not given.")
        name = (query.get("name") or [""])[0]
        self._json(200, app.save(name, int(length), self.rfile))

    def _known(self, app: AddSources, query: dict) -> None:
        length = self.headers.get("Content-Length") or ""
        if not length.isdigit() or int(length) > _MAX_LIST:
            raise Refused(413, "The list of files is too long.")
        self._json(200, app.known(self.rfile.read(int(length))))

    def _done(self, app: AddSources, query: dict) -> None:
        app.finished = True
        self._json(200, {"ok": True, "added": len(app.added)})


class HelperServer(HTTPServer):
    """The helper for one check folder, listening on 127.0.0.1."""

    def __init__(self, work: Work, port: int = 0):
        super().__init__(("127.0.0.1", port), _Handler)
        self.timeout = 0.5
        self.app = AddSources(work, self.server_address[1])


def run(server: HelperServer, idle_seconds: float) -> dict:
    """Serve until the page says it is finished or nothing has asked for a while."""
    app = server.app
    try:
        while not app.finished:
            server.handle_request()
            if time.monotonic() - app.last_seen > idle_seconds:
                return app.summary("idle")
        return app.summary("finished")
    finally:
        server.server_close()


def cmd_serve(args):
    """Show the report from 127.0.0.1 so the person can add source files from the page."""
    work = Work(args.work)
    if not work.p("refs.json").exists():
        raise SystemExit(f"No check found in {work.root}")
    server = HelperServer(work, args.port)
    print(
        json.dumps(
            {
                "url": server.app.url,
                "note": "Give the person this address. It works on this computer only and "
                "stops when they finish or after the idle time.",
            }
        ),
        flush=True,
    )
    out(run(server, args.idle_minutes * 60))
