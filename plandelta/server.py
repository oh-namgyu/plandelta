"""Local HTTP server for the review UI.

"Local" is not the same as "safe". A browser on this machine can be pointed at
this port by any page you visit, so the server checks four things before it does
anything: a session token minted at startup, a Host header it recognises (DNS
rebinding), an Origin it recognises for anything that writes, and a body size
limit. Comparing is a write — it can send your documents to an external engine.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import guards, report, service
from .compare import compare_pair
from .discovery import discover, resolve_in_root
from .errors import PathDenied, PlandeltaError
from .hashing import read_text, toolchain_id
from .store import Store

DEFAULT_PORT = 6188
STATIC_DIR = Path(__file__).parent / "static"


class ServerState:
    """Everything a request handler is allowed to touch."""

    def __init__(self, root: Path, engine, host: str, port: int) -> None:
        self.root = root
        self.engine = engine
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.toolchain = toolchain_id(engine.info.id, engine.info.model_id)
        self.compare_lock = threading.Lock()
        self.store_lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?token={self.token}"

    def open_store(self) -> Store:
        return Store(self.root)


class Handler(BaseHTTPRequestHandler):
    server_version = "plandelta"
    state: ServerState

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - noise
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", guards.CSP)
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict | list, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")

    def _error(self, code: str, message: str, status: int = 400) -> None:
        self._json({"error": {"code": code, "message": message}}, status)

    # -- guards ------------------------------------------------------------

    def _guard(self, *, writing: bool) -> bool:
        if not guards.host_allowed(self.headers.get("Host", "")):
            self._error("E_AUTH", "unrecognised Host header", 403)
            return False
        token = self._token()
        if not token or not secrets.compare_digest(token, self.state.token):
            self._error("E_AUTH", "missing or invalid session token", 401)
            return False
        if writing and not guards.origin_allowed(self.headers.get("Origin", "")):
            self._error("E_AUTH", "missing or cross-site Origin", 403)
            return False
        return True

    def _token(self) -> str:
        header = self.headers.get("X-Plandelta-Token", "")
        if header:
            return header
        query = parse_qs(urlparse(self.path).query)
        return (query.get("token") or [""])[0]

    def _read_body(self) -> dict | None:
        payload, error = guards.read_body(self.rfile, self.headers.get("Content-Length", ""))
        if error == "E_DOC_TOO_LARGE":
            self._error(error, f"body exceeds {guards.MAX_BODY_BYTES} bytes", 413)
        elif error:
            self._error(error, "body is not valid JSON")
        return payload

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if route == "/static/style.css":
            self._send(200, (STATIC_DIR / "style.css").read_bytes(), "text/css; charset=utf-8")
            return
        if route == "/static/app.js":
            self._send(200, (STATIC_DIR / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        if route == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if route == "/api/health":
            self._json({"ok": True, "root": str(self.state.root)})
            return
        if not self._guard(writing=False):
            return
        try:
            self._route_get(route)
        except PlandeltaError as exc:
            self._json(exc.as_dict(), 400)

    def _route_get(self, route: str) -> None:
        query = parse_qs(urlparse(self.path).query)
        store = self.state.open_store()
        try:
            if route == "/api/pairs":
                self._json(service.scan(self.state.root, store, self.state.toolchain))
                return
            if route.startswith("/api/pairs/") and route.endswith("/snapshots"):
                self._json(service.trend(store, route.split("/")[3]))
                return
            if route.startswith("/api/pairs/") and route.endswith("/latest"):
                self._latest(store, route.split("/")[3])
                return
            if route == "/api/doc":
                self._document(query)
                return
        finally:
            store.close()
        self._error("E_SCHEMA", f"unknown route: {route}", 404)

    def _latest(self, store: Store, pair_id: str) -> None:
        snapshot = store.latest_snapshot(pair_id)
        if snapshot is None:
            self._json({"pair_id": pair_id, "snapshot": None})
            return
        detail = service.snapshot_detail(store, snapshot["id"], pair_id)
        totals = detail["totals"]
        charts = {
            "donut": report.donut_svg(totals.get("counts", {})),
            "stack": report.stack_svg(detail["items"]),
            "trend": report.trend_svg(service.trend(store, pair_id)),
        }
        self._json(
            {
                "pair_id": pair_id,
                "snapshot_id": snapshot["id"],
                "created_at": snapshot["created_at"],
                "engine": snapshot["engine"],
                "model": snapshot["model"],
                "lineage": service.lineage_summary(detail["items"]),
                "charts": charts,
                **detail,
            }
        )

    def _document(self, query: dict) -> None:
        name = (query.get("file") or [""])[0]
        if not name:
            self._error("E_SCHEMA", "file parameter required")
            return
        try:
            path = resolve_in_root(self.state.root, Path(name))
        except PathDenied as exc:
            self._json(exc.as_dict(), 403)
            return
        if not path.is_file():
            self._error("E_PATH_DENIED", "no such document", 404)
            return
        self._json({"file": name, "text": read_text(path)})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        route = urlparse(self.path).path
        if not self._guard(writing=True):
            return
        body = self._read_body()
        if body is None:
            return
        if route.startswith("/api/pairs/") and route.endswith("/override"):
            self._override(route.split("/")[3], body)
            return
        if not (route.startswith("/api/pairs/") and route.endswith("/recompare")):
            self._error("E_SCHEMA", f"unknown route: {route}", 404)
            return
        if not self.state.compare_lock.acquire(blocking=False):
            self._error("E_SCHEMA", "a comparison is already running", 409)
            return
        try:
            self._recompare(route.split("/")[3])
        except PlandeltaError as exc:
            self._json(exc.as_dict(), 400)
        finally:
            self.state.compare_lock.release()

    def _override(self, pair_id: str, body: dict) -> None:
        """Record or revoke a person's verdict. Cheap, local, no engine call."""
        item_key = str(body.get("item_key") or "")
        if not item_key:
            self._error("E_SCHEMA", "item_key is required")
            return
        with self.state.store_lock:
            store = self.state.open_store()
            try:
                if body.get("revoke"):
                    self._json({"revoked": store.revoke_override(pair_id, item_key)})
                    return
                try:
                    store.set_override(
                        pair_id, item_key, str(body.get("status") or ""),
                        str(body.get("reason") or ""), str(body.get("author") or "human"),
                    )
                except ValueError as exc:
                    self._error("E_SCHEMA", str(exc))
                    return
                self._json({"overrides": store.overrides(pair_id)})
            finally:
                store.close()

    def _recompare(self, pair_id: str) -> None:
        pair = next((p for p in discover(self.state.root).pairs if p.id == pair_id), None)
        if pair is None:
            self._error("E_SCHEMA", f"no such pair: {pair_id}", 404)
            return
        engine = self.state.engine
        with self.state.store_lock:
            store = self.state.open_store()
            try:
                result = compare_pair(pair, engine, store)
                result.snapshot_id = store.write_snapshot(
                    pair_id=pair.id, plan_hash=result.plan_hash, bundle_hash=result.bundle_hash,
                    engine=engine.info.id, model=engine.resolved_model(),
                    toolchain=self.state.toolchain, totals=result.totals.as_dict(),
                    verdicts=result.verdicts, extras=result.extras, lineage=result.lineage,
                )
            finally:
                store.close()
        self._json(result.as_dict(self.state.root))


def build_server(state: ServerState) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer((state.host, state.port), handler)


def serve(root: Path, engine, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    """Run until interrupted, printing the one URL that carries the token."""
    state = ServerState(root, engine, host, port)
    httpd = build_server(state)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding {host} exposes this UI beyond your machine.", flush=True)
    print(f"plandelta UI: {state.url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
