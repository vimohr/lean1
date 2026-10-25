"""Selecting unsolved QIQCOP problems reliably, with validation and retries."""

import io
import json
import os
import random
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from .support import FakeZoo, problem_id

from lean1.common import RunnerError
from lean1.issues import EXIT_PROBLEM_SOURCE
from lean1.qiqcop import (
    DEFAULT_SITE, ProblemSourceError, ZooClient, ZooProblem, pick_unsolved, site_url, standard_problem_text,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener_for(documents: dict[str, object]):
    """Serve JSON documents or raise the stored exception, keyed by URL suffix."""
    def opener(request, timeout):
        for suffix, document in documents.items():
            if request.full_url.endswith(suffix):
                if isinstance(document, BaseException):
                    raise document
                if isinstance(document, bytes):
                    return Response(document)
                return Response(json.dumps(document).encode())
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)
    return opener


def index(*entries, unsolved=None):
    document = {"problems": [dict(id=i, title=f"T{i}", status=s) for i, s in entries]}
    if unsolved is not None:
        document["counts"] = {"unsolved": unsolved}
    return document


class StandardTextTests(unittest.TestCase):
    def test_standard_problem_has_the_requested_form(self):
        problem = ZooProblem("op_f1e5a1cad168b38b", "Honest-party lockability", "Unsolved")
        self.assertEqual(
            standard_problem_text(problem),
            "Solve this problem https://qiqc-op.com/problem/op_f1e5a1cad168b38b/. Make no mistakes.",
        )
        self.assertEqual(problem.json_url, "https://qiqc-op.com/api/problems/op_f1e5a1cad168b38b.json")

    def test_site_override_and_validation(self):
        self.assertEqual(site_url(), DEFAULT_SITE)
        self.assertEqual(site_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000/")
        with patch.dict(os.environ, {"LEAN1_QIQCOP_SITE": "https://mirror.example.org/zoo/"}):
            self.assertEqual(site_url(), "https://mirror.example.org/zoo/")
            self.assertEqual(ZooProblem("op_0000000000000001", "", "Unsolved", site_url()).url, "https://mirror.example.org/zoo/problem/op_0000000000000001/")
        for value in ("ftp://example.org/", "qiqc-op.com"):
            with self.subTest(value=value), self.assertRaises(RunnerError):
                site_url(value)


class IndexTests(unittest.TestCase):
    def client(self, documents, **options):
        return ZooClient("https://zoo.test/", opener=opener_for(documents), sleep=lambda _: None, **options)

    def test_valid_index_with_duplicate_ids_and_rich_titles(self):
        a, b = problem_id(1), problem_id(2)
        document = index((a, "Unsolved"), (b, "Solved"), (a, "Unsolved"), unsolved=1)
        document["problems"][1]["title"] = {"text": "Rich title"}
        problems = self.client({"api/index.json": document}).index()
        self.assertEqual([p.id for p in problems], [a, b])
        self.assertEqual(problems[1].title, "Rich title")
        self.assertTrue(problems[0].unsolved)
        self.assertFalse(problems[1].unsolved)

    def test_malformed_indexes_are_rejected(self):
        good = problem_id(1)
        cases = {
            "no list": {"items": []},
            "bad id": index(("op_123", "Unsolved")),
            "injected id": index(("op_0000000000000001/../../x", "Unsolved")),
            "missing status": {"problems": [{"id": good}]},
            "entry not object": {"problems": ["op"]},
            "count mismatch": index((good, "Unsolved"), unsolved=2),
        }
        for label, document in cases.items():
            with self.subTest(label), self.assertRaises(ProblemSourceError) as raised:
                self.client({"api/index.json": document}).index()
            self.assertEqual(raised.exception.exit_code, EXIT_PROBLEM_SOURCE)

    def test_invalid_json_is_an_issue(self):
        with self.assertRaisesRegex(ProblemSourceError, "valid JSON"):
            self.client({"api/index.json": b"<html>maintenance</html>"}).index()

    def test_transient_failures_are_retried_with_backoff(self):
        delays = []
        responses = [URLError("timeout"), HTTPError("u", 503, "Unavailable", {}, None)]

        def opener(request, timeout):
            if responses:
                raise responses.pop(0)
            return Response(json.dumps(index((problem_id(1), "Unsolved"))).encode())

        client = ZooClient("https://zoo.test/", opener=opener, sleep=delays.append)
        self.assertEqual(len(client.index()), 1)
        self.assertEqual(delays, [2.0, 4.0])

    def test_persistent_failure_reports_every_attempt(self):
        delays = []
        client = ZooClient("https://zoo.test/", opener=opener_for({"api/index.json": URLError("down")}), sleep=delays.append, attempts=3)
        with self.assertRaises(ProblemSourceError) as raised:
            client.index()
        self.assertEqual(delays, [2.0, 4.0])
        self.assertEqual(raised.exception.detail.count("down"), 3)

    def test_missing_documents_are_not_retried(self):
        delays = []
        client = ZooClient("https://zoo.test/", opener=opener_for({}), sleep=delays.append)
        with self.assertRaisesRegex(ProblemSourceError, "could not download"):
            client.index()
        self.assertEqual(delays, [])


class PickTests(unittest.TestCase):
    def setUp(self):
        self.ids = [problem_id(number) for number in range(1, 6)]
        self.statuses = {identifier: "Unsolved" for identifier in self.ids[:4]} | {self.ids[4]: "Solved"}
        self.zoo = FakeZoo(self.statuses).__enter__()
        self.addCleanup(self.zoo.__exit__)
        self.client = ZooClient(self.zoo.site, sleep=lambda _: None)

    def test_picks_only_unsolved_unattempted_problems_and_confirms_them(self):
        seen = set()
        for seed in range(40):
            problem, record = pick_unsolved(self.client, exclude=[self.ids[0]], rng=random.Random(seed))
            seen.add(problem.id)
            self.assertEqual(record["id"], problem.id)
            self.assertEqual(problem.title, f"Problem {problem.id}")
            self.assertTrue(problem.url.startswith(self.zoo.site))
        self.assertEqual(seen, set(self.ids[1:4]))
        self.assertIn(f"/problem/{problem.id}/", self.zoo.requests)

    def test_same_seed_gives_the_same_problem(self):
        first, _ = pick_unsolved(self.client, rng=random.Random(7))
        second, _ = pick_unsolved(self.client, rng=random.Random(7))
        self.assertEqual(first, second)

    def test_record_disagreeing_with_index_is_skipped(self):
        for identifier in self.ids[:3]:
            self.zoo.record_statuses[identifier] = "Solved"
        problem, _ = pick_unsolved(self.client, rng=random.Random(0))
        self.assertEqual(problem.id, self.ids[3])

    def test_no_confirmed_problem_is_an_issue(self):
        for identifier in self.ids[:4]:
            self.zoo.record_statuses[identifier] = "Solved"
        with self.assertRaisesRegex(ProblemSourceError, "could not confirm") as raised:
            pick_unsolved(self.client)
        self.assertIn("record says 'Solved'", raised.exception.detail)

    def test_exhausted_campaign_is_an_issue(self):
        with self.assertRaisesRegex(ProblemSourceError, "no unsolved problems remain"):
            pick_unsolved(self.client, exclude=self.ids)

    def test_record_for_another_problem_or_page_without_the_id_is_rejected(self):
        problem = ZooProblem(self.ids[0], "", "Unsolved", self.zoo.site)
        with patch.object(self.client, "get_json", return_value={"id": self.ids[1], "status": "Unsolved"}):
            with self.assertRaisesRegex(ProblemSourceError, "describes"):
                self.client.record(problem)
        with patch.object(ZooClient, "_get", return_value=b"<html>other</html>"):
            with self.assertRaisesRegex(ProblemSourceError, "does not mention"):
                self.client.check_page(problem)

    def test_server_errors_recover_within_the_retry_budget(self):
        self.zoo.failures["/api/index.json"] = 2
        problem, _ = pick_unsolved(self.client, rng=random.Random(1))
        self.assertTrue(problem.unsolved)
        self.assertEqual(self.zoo.requests.count("/api/index.json"), 3)


@unittest.skipUnless(os.environ.get("LEAN1_LIVE_TESTS"), "set LEAN1_LIVE_TESTS=1 to query https://qiqc-op.com/")
class LiveSiteTests(unittest.TestCase):
    def test_live_site_supplies_distinct_confirmed_unsolved_problems(self):
        client = ZooClient()
        problems = client.index()
        self.assertGreater(sum(problem.unsolved for problem in problems), 0)
        chosen: list[str] = []
        for _ in range(3):
            problem, record = pick_unsolved(client, exclude=chosen)
            self.assertEqual(record["id"], problem.id)
            self.assertEqual(record["status"].lower(), "unsolved")
            self.assertEqual(standard_problem_text(problem), f"Solve this problem https://qiqc-op.com/problem/{problem.id}/. Make no mistakes.")
            chosen.append(problem.id)
        self.assertEqual(len(set(chosen)), 3)


if __name__ == "__main__":
    unittest.main()
