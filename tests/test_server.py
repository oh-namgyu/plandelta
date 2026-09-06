from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from plandelta.compare import compare_pair
from plandelta.discovery import discover
from plandelta.hashing import toolchain_id
from plandelta.server import ServerState, build_server
from plandelta.store import Store
from test_store import ScriptedEngine, all_done_reply

FIXTURES = Path(__file__).parent / "fixtures" / "sample_root"
QUOTE = "The ingest service reads CSV files"


def request(url: str, *, method: str = "GET", headers: dict | None = None, body: bytes | None = None):
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            return exc.code, json.loads(payload or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "docs"
        shutil.copytree(FIXTURES, cls.root)

        engine = ScriptedEngine([all_done_reply(3, QUOTE)])
        store = Store(cls.root)
        pair = next(p for p in discover(cls.root).pairs if p.id == "prose-plan")
        result = compare_pair(pair, engine, store, find_extras=False, classify_prose=False)
        store.write_snapshot(
            pair_id=pair.id, plan_hash=result.plan_hash, bundle_hash=result.bundle_hash,
            engine=engine.info.id, model=engine.info.model_id,
            toolchain=toolchain_id(engine.info.id, engine.info.model_id),
            totals=result.totals.as_dict(), verdicts=result.verdicts, extras=result.extras,
            lineage=result.lineage,
        )
        store.close()

        cls.state = ServerState(cls.root, engine, "127.0.0.1", 0)
        cls.httpd = build_server(cls.state)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def auth(self) -> dict:
        return {"X-Plandelta-Token": self.state.token}

    # -- access control ----------------------------------------------------

    def test_api_without_a_token_is_rejected(self) -> None:
        status, payload = request(self.url("/api/pairs"))
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "E_AUTH")

    def test_api_with_a_wrong_token_is_rejected(self) -> None:
        status, _ = request(self.url("/api/pairs"), headers={"X-Plandelta-Token": "nope"})
        self.assertEqual(status, 401)

    def test_token_may_travel_in_the_query_string(self) -> None:
        status, _ = request(self.url(f"/api/pairs?token={self.state.token}"))
        self.assertEqual(status, 200)

    def test_foreign_host_header_is_rejected(self) -> None:
        headers = {**self.auth(), "Host": "evil.example.com"}
        status, payload = request(self.url("/api/pairs"), headers=headers)
        self.assertEqual(status, 403)
        self.assertIn("Host", payload["error"]["message"])

    def test_post_without_origin_is_rejected(self) -> None:
        status, _ = request(
            self.url("/api/pairs/prose-plan/recompare"), method="POST", headers=self.auth(), body=b"{}"
        )
        self.assertEqual(status, 403)

    def test_post_from_a_foreign_origin_is_rejected(self) -> None:
        headers = {**self.auth(), "Origin": "https://evil.example.com"}
        status, _ = request(
            self.url("/api/pairs/prose-plan/recompare"), method="POST", headers=headers, body=b"{}"
        )
        self.assertEqual(status, 403)

    def test_oversized_body_is_rejected(self) -> None:
        headers = {**self.auth(), "Origin": f"http://127.0.0.1:{self.port}"}
        status, payload = request(
            self.url("/api/pairs/prose-plan/recompare"), method="POST", headers=headers,
            body=b"x" * (1024 * 1024 + 10),
        )
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "E_DOC_TOO_LARGE")

    def test_responses_carry_a_restrictive_csp(self) -> None:
        req = urllib.request.Request(self.url("/"), headers=self.auth())
        with urllib.request.urlopen(req, timeout=10) as response:
            policy = response.headers["Content-Security-Policy"]
            self.assertIn("default-src 'none'", policy)
            self.assertIn("connect-src 'self'", policy)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_health_needs_no_token(self) -> None:
        status, payload = request(self.url("/api/health"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    # -- data --------------------------------------------------------------

    def test_pairs_report_dirty_state(self) -> None:
        _, payload = request(self.url("/api/pairs"), headers=self.auth())
        rows = {row["id"]: row for row in payload}
        self.assertFalse(rows["prose-plan"]["dirty"], "compared pair should be clean")
        self.assertTrue(rows["checkbox-app"]["dirty"], "never-compared pair should be dirty")

    def test_latest_returns_items_and_charts(self) -> None:
        _, payload = request(self.url("/api/pairs/prose-plan/latest"), headers=self.auth())
        self.assertEqual(len(payload["items"]), 3)
        self.assertIn("<svg", payload["charts"]["donut"])
        self.assertIn("<svg", payload["charts"]["stack"])

    def test_snapshots_expose_the_round_series(self) -> None:
        _, payload = request(self.url("/api/pairs/prose-plan/snapshots"), headers=self.auth())
        self.assertEqual([row["round"] for row in payload], [1])

    def test_document_outside_root_is_denied(self) -> None:
        status, payload = request(
            self.url("/api/doc?file=../../etc/hosts"), headers=self.auth()
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "E_PATH_DENIED")

    def test_unknown_route_is_404(self) -> None:
        status, _ = request(self.url("/api/nope"), headers=self.auth())
        self.assertEqual(status, 404)

    def test_chart_markup_escapes_document_text(self) -> None:
        """An item title from a document must never reach the page as markup."""
        _, payload = request(self.url("/api/pairs/prose-plan/latest"), headers=self.auth())
        markup = payload["charts"]["stack"]
        self.assertNotIn("<script", markup)
        self.assertNotIn("onerror=", markup)


if __name__ == "__main__":
    unittest.main()
