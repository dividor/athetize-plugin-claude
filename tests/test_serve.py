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
from unittest import mock

import pl_http
import pl_ingest
import pl_serve
import pl_workspace
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


# A long text that names none of the paper's references.
PLAIN = (
    "The harbour ledger lists cargo, tides and berths for each week of the season. " * 12
).encode()

NO_EMAIL = {"has_email": False, "email": ""}


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
        self.assertTrue(json.loads(self.ask("GET", "api/ping")[1])["ok"])

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
        self.assertEqual([u["file"] for u in result["unmatched"]], ["notes.txt"])
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
        self.assertEqual([n["file"] for n in self.summary["not_recognised"]], ["notes.txt"])
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

    def test_word_report_is_offered_once_it_has_been_written(self):
        status, body = self.ask("GET", "word.docx")
        self.assertEqual(status, 404)
        self.assertIn(b"No Word report", body)
        self.assertNotIn(b'"word_export": "paper', self.ask("GET", "report.html")[1])
        self.work.p("paper_athetize_suggestions.docx").write_bytes(b"PK word copy")
        self.assertEqual(self.ask("GET", "word.docx"), (200, b"PK word copy"))
        self.assertIn(
            b'"word_export": "paper_athetize_suggestions.docx"',
            self.ask("GET", "report.html")[1],
        )

    def _pick(self, paths):
        """Stand in for the system chooser: the person picks these files and folders."""
        seen = mock.patch.object(pl_serve, "has_system_chooser", return_value=True)
        picked = mock.patch.object(pl_serve, "choose_paths", return_value=[str(p) for p in paths])
        with seen, picked:
            return self.post_json("api/choose")

    def test_one_chooser_takes_a_folder_and_files_together(self):
        papers = self.tmp / "My papers"
        (papers / "older").mkdir(parents=True)
        (papers / "older" / "S0019103511.txt").write_text(BRANDT, "utf-8")
        (papers / "holiday.png").write_bytes(b"not a paper")
        loose = self.tmp / "notes.txt"
        loose.write_text("milk, eggs, bread", "utf-8")
        photo = self.tmp / "scan.png"
        photo.write_bytes(b"not a paper")
        status, result = self._pick([papers, loose, photo])
        self.assertEqual(status, 200)
        got = result["matched"][0]
        self.assertEqual(
            (got["file"], got["ref"], got["how"], got["status"]),
            ("S0019103511.txt", "3", "content", "user_provided"),
        )
        self.assertEqual([u["file"] for u in result["unmatched"]], ["notes.txt"])
        self.assertEqual(result["other_files"], 1)
        # the person's own files stay exactly where they were
        self.assertTrue((papers / "older" / "S0019103511.txt").exists())
        self.assertTrue(loose.exists())
        self.assertEqual(self.app.chosen, [str(papers), str(loose), str(photo)])

    def test_closing_the_chooser_adds_nothing(self):
        self.assertEqual(self._pick([]), (200, {"cancelled": True}))
        self.assertEqual(self.app.added, [])

    def test_page_is_told_whether_one_chooser_is_available(self):
        for there in (True, False):
            with mock.patch.object(pl_serve, "has_system_chooser", return_value=there):
                self.assertEqual(json.loads(self.ask("GET", "api/ping")[1])["chooser"], there)
        with mock.patch.object(pl_serve, "has_system_chooser", return_value=False):
            self.assertEqual(self.post_json("api/choose")[0], 404)

    def test_a_document_uploaded_on_a_row_is_added_to_that_reference(self):
        # the text names no reference of the paper, so only the person's choice says whose it is
        status, got = self.post_json("api/upload-for?ref=4&name=scan%20of%20chapter.txt", PLAIN)
        self.assertEqual(status, 200)
        self.assertEqual(
            (got["file"], got["ref"], got["status"], got["looks_like"]),
            ("scan of chapter.txt", "4", "user_provided", None),
        )
        self.assertTrue(got["title"])
        self.assertEqual(self.work.manifest()["refs"]["4"]["status"], "user_provided")
        self.assertEqual(self.work.p("sources", "4.txt").read_bytes(), PLAIN)
        self.assertEqual(list(self.work.p(pl_serve.INCOMING).iterdir()), [])
        self.assertEqual(
            self.app.added, [{"file": "scan of chapter.txt", "ref": "4", "status": "user_provided"}]
        )

    def test_a_document_that_reads_like_another_reference_is_added_and_flagged(self):
        status, got = self.post_json("api/upload-for?ref=4&name=paper.txt", BRANDT.encode())
        self.assertEqual((status, got["ref"], got["looks_like"]), (200, "4", "3"))
        self.assertEqual(
            got["looks_like_title"], "Sleep spindles and overnight retention across the lifespan"
        )
        self.assertEqual(self.work.manifest()["refs"]["4"]["status"], "user_provided")
        self.assertEqual(self.work.manifest()["refs"]["3"]["status"], "missing")

    def test_a_document_for_a_reference_that_is_not_in_the_paper_is_refused(self):
        status, got = self.post_json("api/upload-for?ref=99&name=paper.txt", PLAIN)
        self.assertEqual(status, 404)
        self.assertIn("Reference 99", got["error"])
        self.assertFalse(self.work.p(pl_serve.INCOMING).exists())
        self.assertEqual(self.post_json("api/upload-for?name=paper.txt", PLAIN)[0], 404)

    def test_a_document_with_almost_no_text_is_refused_and_nothing_changes(self):
        status, got = self.post_json("api/upload-for?ref=3&name=cover.txt", b"Sign in to read")
        self.assertEqual(status, 422)
        self.assertIn("almost no text", got["error"])
        self.assertEqual(self.work.manifest()["refs"]["3"], {"status": "missing"})
        self.assertFalse(self.work.p("sources").exists())
        self.assertEqual(list(self.work.p(pl_serve.INCOMING).iterdir()), [])
        self.assertEqual(self.app.added, [])
        self.assertEqual(self.post_json("api/upload-for?ref=3&name=tool.exe", PLAIN)[0], 415)

    def test_contact_email_is_saved_shown_masked_and_removed(self):
        self.assertEqual(self.ask("GET", "api/settings")[0], 200)
        self.assertEqual(self.post_json("api/settings", b"{}")[0], 400)
        self.assertEqual(json.loads(self.ask("GET", "api/settings")[1]), NO_EMAIL)
        status, got = self.post_json("api/settings", b'{"email": " reader@example.org "}')
        self.assertEqual((status, got), (200, {"has_email": True, "email": "r***@example.org"}))
        self.assertEqual(pl_workspace.load_config()["email"], "reader@example.org")
        self.assertEqual(json.loads(self.ask("GET", "api/settings")[1]), got)
        self.assertEqual(self.post_json("api/settings", b'{"email": ""}'), (200, NO_EMAIL))
        self.assertNotIn("email", pl_workspace.load_config())

    def test_only_a_plain_address_is_accepted_as_the_contact_email(self):
        # the address goes into a request header, so nothing that could break one is stored
        for bad in ("reader", "reader@host", "a b@example.org", "x@example.org\r\nX-Evil: 1", 7):
            body = json.dumps({"email": bad}).encode()
            status, got = self.post_json("api/settings", body)
            self.assertIn(status, (400, 422), bad)
            self.assertIn("error", got)
        self.assertNotIn("email", pl_workspace.load_config())
        self.assertFalse(self.app.email_changed)
        self.assertEqual(self.post_json("api/settings", b"x" * 5000)[0], 413)

    def test_a_changed_email_tells_claude_to_look_for_the_sources_again(self):
        self.post_json("api/settings", b'{"email": "reader@example.org"}')
        self.post_json("api/done")
        self.thread.join(5)
        self.assertTrue(self.summary["email_changed"])
        self.assertIn("resolve again", self.summary["next"])

    def test_importing_with_nothing_waiting_adds_nothing(self):
        status, result = self.post_json("api/import")
        self.assertEqual((status, result["matched"], result["unmatched"]), (200, [], []))


class HelperPiecesTests(unittest.TestCase):
    def test_email_check_and_mask(self):
        self.assertEqual(
            pl_workspace.clean_email(" a.b+c@uni.example.edu "), "a.b+c@uni.example.edu"
        )
        for bad in (
            "",
            "a@b",
            "@example.org",
            "a@@example.org",
            "a@example..org",
            "a" * 250 + "@x.org",
        ):
            with self.assertRaises(ValueError):
                pl_workspace.clean_email(bad)
        self.assertEqual(pl_workspace.masked_email("reader@example.org"), "r***@example.org")

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
