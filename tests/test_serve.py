"""Tests for pl_serve: the helper on 127.0.0.1 that lets the report page add source files.

Each test starts a real helper for a throwaway check folder and talks to it over HTTP, the
way the report page does. Lookups that would go to the internet are stubbed out.
"""

import hashlib
import http.client
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import pl_http
import pl_ingest
import pl_serve
from pl_core import Work, jsave

FIX = Path(__file__).resolve().parent / "fixtures" / "sample"

# The opening of reference 3 ("Brandt, K., Silva, R. (2021). Sleep spindles and overnight
# retention across the lifespan"), as a paper saved under a meaningless name would read.
BRANDT = (
    "Sleep spindles and overnight retention\nacross the lifespan\n"
    "Karin Brandt and Rafael Silva\nJournal of Sleep Studies, 2021\n\nAbstract\n"
    + "Spindle density predicted overnight retention in every age group we tested. " * 12
)


def _offline(*a, **k):
    raise pl_http.HttpError("network error for offline.test: stubbed")


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._patch_env("ATHETIZE_HOME", str(self.tmp / "home"))
        real_get = pl_http.http_get
        pl_http.http_get = _offline
        self.addCleanup(setattr, pl_http, "http_get", real_get)

        self.work = Work(self.tmp / "sample-check")
        self.work.root.mkdir(parents=True)
        jsave(self.work.p("paper.json"), pl_ingest.ingest(FIX / "paper.md"))
        for f in ("refs.json", "claims.json"):
            shutil.copy(FIX / f, self.work.p(f))
        statuses = {"1": "full_text", "2": "abstract_only", "3": "missing", "4": "missing"}
        jsave(
            self.work.p("manifest.json"),
            {"refs": {k: {"status": v} for k, v in statuses.items()}},
        )

        self.server = pl_serve.HelperServer(self.work)
        self.app = self.server.app
        self.summary = {}
        self.thread = threading.Thread(
            target=lambda: self.summary.update(pl_serve.run(self.server, 60)), daemon=True
        )
        self.thread.start()
        self.addCleanup(self._stop)

    def _patch_env(self, name, value):
        before = os.environ.get(name)
        os.environ[name] = value
        if before is None:
            self.addCleanup(os.environ.pop, name, None)
        else:
            self.addCleanup(os.environ.__setitem__, name, before)

    def _stop(self):
        self.app.finished = True
        self.thread.join(5)

    def ask(self, method, route, body=None, token=None, host=None, origin="page"):
        """Send one request the way the report page would, and return (status, body)."""
        here = f"127.0.0.1:{self.app.port}"
        headers = {"Host": host or here}
        if origin == "page":
            origin = "http://" + here
        if method == "POST" and origin:
            headers["Origin"] = origin
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        self.addCleanup(conn.close)
        conn.request(method, f"/{token or self.app.token}/{route}", body=body, headers=headers)
        reply = conn.getresponse()
        return reply.status, reply.read()

    def post_json(self, route, body=None, **kw):
        status, raw = self.ask("POST", route, body=body, **kw)
        return status, json.loads(raw)

    def test_report_is_served_at_the_address_with_the_token(self):
        status, body = self.ask("GET", "report.html")
        self.assertEqual(status, 200)
        self.assertIn(b'id="sourcesView"', body)
        self.assertTrue(self.app.url.startswith(f"http://127.0.0.1:{self.app.port}/"))
        self.assertEqual(self.ask("GET", "api/ping"), (200, b'{"ok": true}'))

    def test_wrong_token_and_unknown_pages_are_not_found(self):
        self.assertEqual(self.ask("GET", "report.html", token="guess")[0], 404)
        self.assertEqual(self.ask("GET", "manifest.json")[0], 404)
        self.assertEqual(self.ask("GET", "../manifest.json")[0], 404)

    def test_a_request_named_for_another_host_is_refused(self):
        self.assertEqual(self.ask("GET", "report.html", host="evil.example")[0], 403)

    def test_only_the_report_page_may_send_files(self):
        for origin in (None, "http://evil.example"):
            status, reply = self.post_json("api/upload?name=a.txt", body=b"x", origin=origin)
            self.assertEqual(status, 403)
            self.assertIn("error", reply)
        self.assertFalse(self.work.p(pl_serve.INCOMING).exists())

    def test_files_of_other_types_are_refused(self):
        status, reply = self.post_json("api/upload?name=run.sh", body=b"echo hi")
        self.assertEqual(status, 415)
        self.assertIn("not a type Athetize reads", reply["error"])
        self.assertFalse(self.work.p(pl_serve.INCOMING).exists())

    def test_an_empty_file_is_refused(self):
        self.assertEqual(self.post_json("api/upload?name=a.txt", body=b"")[0], 400)

    def test_a_file_name_cannot_point_outside_the_incoming_folder(self):
        status, reply = self.post_json("api/upload?name=../../escape.txt", body=b"hello")
        self.assertEqual((status, reply), (200, {"saved": "escape.txt"}))
        self.assertTrue(self.work.p(pl_serve.INCOMING, "escape.txt").exists())
        self.assertFalse((self.tmp / "escape.txt").exists())

    def test_a_second_file_of_the_same_name_does_not_replace_the_first(self):
        self.post_json("api/upload?name=paper.txt", body=b"one")
        status, reply = self.post_json("api/upload?name=paper.txt", body=b"two")
        self.assertEqual(reply, {"saved": "paper (2).txt"})
        self.assertEqual(self.work.p(pl_serve.INCOMING, "paper.txt").read_bytes(), b"one")

    def test_a_chosen_file_is_matched_to_its_reference_and_added(self):
        self.post_json("api/upload?name=download%20(4).txt", body=BRANDT.encode())
        self.post_json("api/upload?name=notes.txt", body=b"milk, eggs, bread")
        status, result = self.post_json("api/import")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["matched"]), 1)
        got = result["matched"][0]
        self.assertEqual(
            (got["file"], got["ref"], got["how"], got["status"]),
            ("download (4).txt", "3", "content", "user_provided"),
        )
        self.assertEqual(got["title"], "Sleep spindles and overnight retention across the lifespan")
        self.assertEqual(result["unmatched"], [{"file": "notes.txt", "why": "no match"}])
        self.assertEqual(self.work.manifest()["refs"]["3"]["status"], "user_provided")
        self.assertEqual(self.work.p("sources", "3.txt").read_text("utf-8"), BRANDT)
        # the matched copy is filed; the unrecognised one is set aside until the person says
        # what it is, so the next import does not report it again
        aside = self.work.p(pl_serve.INCOMING, pl_serve.UNRECOGNISED)
        self.assertEqual(
            sorted(p.name for p in self.work.p(pl_serve.INCOMING).iterdir()), [aside.name]
        )
        self.assertEqual([p.name for p in aside.iterdir()], ["notes.txt"])
        again = self.post_json("api/import")[1]
        self.assertEqual((again["matched"], again["unmatched"]), ([], []))
        # the report served next shows the new source
        self.assertIn(b'"status": "user_provided"', self.ask("GET", "report.html")[1])

        self.assertEqual(self.post_json("api/done"), (200, {"ok": True, "added": 1}))
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.summary["stopped"], "finished")
        self.assertEqual(
            self.summary["added"],
            [{"file": "download (4).txt", "ref": "3", "status": "user_provided"}],
        )
        self.assertEqual(self.summary["not_recognised"], [{"file": "notes.txt", "why": "no match"}])
        self.assertEqual(self.summary["not_recognised_folder"], str(aside))
        self.assertEqual(self.summary["next"], "Run the recheck step.")

    def test_page_can_ask_which_files_are_already_among_the_sources(self):
        self.work.p("sources").mkdir()
        self.work.p("sources", "1.txt").write_bytes(b"already here")
        have = hashlib.sha256(b"already here").hexdigest()
        new = hashlib.sha256(b"not yet").hexdigest()
        body = json.dumps({"hashes": [have, new]}).encode()
        self.assertEqual(self.post_json("api/known", body=body), (200, {"known": [have]}))
        self.assertEqual(self.post_json("api/known", body=b"not json")[0], 400)
        self.assertEqual(self.post_json("api/known", body=b'{"hashes": "x"}')[0], 400)

    def test_a_repeat_among_the_chosen_files_is_reported_and_not_kept(self):
        self.post_json("api/upload?name=one.txt", body=BRANDT.encode())
        self.post_json("api/upload?name=two.txt", body=BRANDT.encode())
        result = self.post_json("api/import")[1]
        self.assertEqual([m["file"] for m in result["matched"]], ["one.txt"])
        self.assertEqual(
            result["duplicates"], [{"file": "two.txt", "why": "same content as one.txt"}]
        )
        self.assertEqual(list(self.work.p(pl_serve.INCOMING).iterdir()), [])

    def test_importing_with_nothing_waiting_adds_nothing(self):
        status, result = self.post_json("api/import")
        self.assertEqual((status, result["matched"], result["unmatched"]), (200, [], []))


class HelperPiecesTests(unittest.TestCase):
    def test_helper_stops_by_itself_when_nothing_asks(self):
        with tempfile.TemporaryDirectory() as d:
            work = Work(d)
            jsave(work.p("refs.json"), [])
            summary = pl_serve.run(pl_serve.HelperServer(work), idle_seconds=0.2)
        self.assertEqual(summary["stopped"], "idle")
        self.assertEqual(summary["next"], "Nothing was added.")

    def test_names_are_reduced_to_a_plain_file_name(self):
        self.assertEqual(pl_serve.safe_name("C:\\Users\\me\\Paper one.pdf"), "Paper one.pdf")
        self.assertEqual(pl_serve.safe_name("/etc/passwd"), "passwd")
        self.assertEqual(pl_serve.safe_name("a<b>:c?.pdf"), "a_b_c_.pdf")
        self.assertEqual(pl_serve.safe_name("../.."), "")

    def test_a_file_cut_short_is_not_kept(self):
        with tempfile.TemporaryDirectory() as d:
            app = pl_serve.AddSources(Work(d), 1)
            with self.assertRaises(pl_serve.Refused) as caught:
                app.save("paper.txt", 100, io.BytesIO(b"only this much"))
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(list(Path(d, pl_serve.INCOMING).iterdir()), [])

    def test_a_file_over_the_size_limit_is_refused_before_it_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            app = pl_serve.AddSources(Work(d), 1)
            with self.assertRaises(pl_serve.Refused) as caught:
                app.save("paper.pdf", pl_serve.MAX_UPLOAD + 1, io.BytesIO(b""))
            self.assertEqual(caught.exception.status, 413)


if __name__ == "__main__":
    unittest.main()
