"""A stand-in for Google's servers on localhost: OAuth, the resumable upload protocol and
the few Data API calls the uploader uses — with switches to make it misbehave the way the
internet does (dropped connections, 5xx answers, lost sessions, expired sign-ins)."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeYouTube:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.videos: dict[str, dict[str, Any]] = {}
        self.playlist_items: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str, dict[str, str]]] = []  # method, path, headers
        self.access_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.codes: dict[str, str] = {}  # code -> PKCE challenge
        self.counter = 0
        # switches
        self.faults: list[tuple] = []  # consumed by upload PUTs with data, in order
        self.create_faults: list[tuple] = []  # consumed when a session is opened
        self.query_faults: list[tuple] = []  # consumed by "how far did you get" requests
        self.processing: list[dict[str, Any]] = []  # answers for videos.list, then "processed"
        self.forced_privacy: str | None = None
        self.deny_sign_in = False
        self.down = False  # every request is answered 503
        self.gate: threading.Event | None = None  # upload PUTs wait for this
        self.channel = {"id": "UC123", "title": "Test Channel"}
        self.playlists = [{"id": "PL1", "title": "Matches"}, {"id": "PL2", "title": "Basketball"}]
        self.playlist_error: tuple[int, str] | None = None

        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                outer.handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                outer.handle(self, "POST")

            def do_PUT(self) -> None:  # noqa: N802
                outer.handle(self, "PUT")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def google(self) -> dict[str, Any]:
        """The ``[google]`` table of upload.toml pointing here."""
        return {"client_id": "client-1", "client_secret": "secret-1",
                "auth_uri": f"{self.base}/auth", "token_uri": f"{self.base}/token",
                "revoke_uri": f"{self.base}/revoke", "api_base": f"{self.base}/api",
                "upload_base": f"{self.base}/upload"}

    def browser(self, url: str) -> None:
        """Stands in for the user's browser: opens the consent page, follows the redirect."""
        import urllib.request

        def go() -> None:
            try:
                urllib.request.urlopen(url, timeout=10).read()
            except OSError:
                pass

        threading.Thread(target=go, daemon=True).start()

    def received(self, video_id: str) -> bytes:
        return bytes(self.videos[video_id]["data"])

    # -- plumbing ----------------------------------------------------------------------

    def _next(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    @staticmethod
    def send(h: BaseHTTPRequestHandler, status: int, body: Any = None,
             headers: dict[str, str] | None = None) -> None:
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        h.send_response(status)
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        if raw:
            h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(raw)))
        h.end_headers()
        h.wfile.write(raw)

    @staticmethod
    def error(h: BaseHTTPRequestHandler, status: int, reason: str, message: str = "") -> None:
        FakeYouTube.send(h, status, {"error": {"code": status, "message": message or reason,
                                              "errors": [{"reason": reason}]}})

    @staticmethod
    def drop(h: BaseHTTPRequestHandler) -> None:
        """Cut the connection without an answer."""
        try:
            h.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        h.close_connection = True

    def _authorized(self, h: BaseHTTPRequestHandler) -> bool:
        token = h.headers.get("Authorization", "").removeprefix("Bearer ")
        if token in self.access_tokens:
            return True
        self.error(h, 401, "authError", "Invalid Credentials")
        return False

    def handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        url = urllib.parse.urlsplit(h.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        with self.lock:
            self.requests.append((method, h.path, dict(h.headers.items())))
        length = int(h.headers.get("Content-Length") or 0)
        route = (method, url.path)
        if self.down:
            h.rfile.read(length)
            return self.error(h, 503, "backendError", "Service unavailable")
        if route == ("PUT", "/upload/videos"):
            return self._put(h, query, length)
        body = h.rfile.read(length)
        if route == ("GET", "/auth"):
            return self._auth(h, query)
        if route == ("POST", "/token"):
            return self._token(h, dict(urllib.parse.parse_qsl(body.decode())))
        if route == ("POST", "/revoke"):
            form = dict(urllib.parse.parse_qsl(body.decode()))
            self.refresh_tokens.discard(form.get("token", ""))
            return self.send(h, 200, {})
        if not self._authorized(h):
            return None
        if route == ("POST", "/upload/videos"):
            return self._create(h, query, json.loads(body or b"{}"))
        if route == ("GET", "/api/channels"):
            return self.send(h, 200, {"items": [{"id": self.channel["id"],
                                                 "snippet": {"title": self.channel["title"]}}]})
        if route == ("GET", "/api/playlists"):
            return self.send(h, 200, {"items": [{"id": p["id"], "snippet": {"title": p["title"]}}
                                                for p in self.playlists]})
        if route == ("GET", "/api/videos"):
            return self._videos(h, query)
        if route == ("POST", "/api/playlistItems"):
            if self.playlist_error:
                return self.error(h, *self.playlist_error)
            snippet = json.loads(body)["snippet"]
            self.playlist_items.append((snippet["playlistId"], snippet["resourceId"]["videoId"]))
            return self.send(h, 200, {"id": "item"})
        return self.error(h, 404, "notFound", h.path)

    # -- OAuth -------------------------------------------------------------------------

    def _auth(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        if self.deny_sign_in:
            target = q["redirect_uri"] + "?" + urllib.parse.urlencode(
                {"error": "access_denied", "state": q.get("state", "")})
        else:
            code = self._next("code")
            self.codes[code] = q.get("code_challenge", "")
            self.last_auth_query = q
            target = q["redirect_uri"] + "?" + urllib.parse.urlencode(
                {"code": code, "state": q.get("state", "")})
        self.send(h, 302, None, {"Location": target})

    def _token(self, h: BaseHTTPRequestHandler, form: dict[str, str]) -> None:
        if form.get("grant_type") == "authorization_code":
            challenge = self.codes.pop(form.get("code", ""), None)
            made = base64.urlsafe_b64encode(hashlib.sha256(
                form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if challenge is None or challenge != made:
                return self.send(h, 400, {"error": "invalid_grant",
                                          "error_description": "bad code or verifier"})
            refresh = self._next("rt-")
            self.refresh_tokens.add(refresh)
        else:
            refresh = form.get("refresh_token", "")
            if refresh not in self.refresh_tokens:
                return self.send(h, 400, {"error": "invalid_grant",
                                          "error_description": "Token has been expired or revoked."})
        access = self._next("at-")
        self.access_tokens.add(access)
        self.send(h, 200, {"access_token": access, "refresh_token": refresh, "expires_in": 3600,
                           "scope": "youtube", "token_type": "Bearer"})

    def expire_access_tokens(self) -> None:
        self.access_tokens.clear()

    def revoke_everything(self) -> None:
        self.access_tokens.clear()
        self.refresh_tokens.clear()

    # -- resumable upload --------------------------------------------------------------

    def _create(self, h: BaseHTTPRequestHandler, q: dict[str, str], body: dict) -> None:
        with self.lock:
            fault = self.create_faults.pop(0) if self.create_faults else None
        if fault:
            return self.error(h, fault[1], fault[2] if len(fault) > 2 else "backendError")
        assert q.get("uploadType") == "resumable"
        sid = self._next("session")
        with self.lock:
            self.sessions[sid] = {"body": body, "total": int(h.headers["X-Upload-Content-Length"]),
                                  "data": bytearray(), "type": h.headers["X-Upload-Content-Type"]}
        self.send(h, 200, None, {"Location": f"{self.base}/upload/videos?upload_id={sid}"})

    def _progress(self, h: BaseHTTPRequestHandler, session: dict) -> None:
        got = len(session["data"])
        self.send(h, 308, None, {"Range": f"bytes=0-{got - 1}"} if got else {})

    def _finish(self, h: BaseHTTPRequestHandler, sid: str, session: dict) -> None:
        with self.lock:
            video_id = session.get("video_id")
            if video_id is None:
                video_id = session["video_id"] = self._next("vid")
                self.videos[video_id] = {"data": session["data"], "body": session["body"],
                                         "polls": 0}
        self.send(h, 200, {"kind": "youtube#video", "id": video_id,
                           "status": {"uploadStatus": "uploaded"}})

    def _put(self, h: BaseHTTPRequestHandler, q: dict[str, str], length: int) -> None:
        content_range = h.headers.get("Content-Range", "")
        session = self.sessions.get(q.get("upload_id", ""))
        is_query = content_range.startswith("bytes */")
        if self.gate is not None and not is_query:
            self.gate.wait(30)
        with self.lock:
            faults = self.query_faults if is_query else self.faults
            fault = faults.pop(0) if faults else None
        if fault and fault[0] == "lose":
            self.sessions.pop(q.get("upload_id", ""), None)
            session = None
        if session is None:
            h.rfile.read(length)
            return self.error(h, 404, "notFound", "Upload session not found")
        token = h.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self.access_tokens:
            h.rfile.read(length)
            return self.error(h, 401, "authError", "Invalid Credentials")
        if is_query:
            h.rfile.read(length)
            if fault and fault[0] == "status":
                return self.error(h, fault[1], "backendError")
            if fault and fault[0] == "drop":
                return self.drop(h)
            if len(session["data"]) == session["total"]:
                return self._finish(h, q["upload_id"], session)
            return self._progress(h, session)
        span, total = content_range.removeprefix("bytes ").split("/")
        start, end = (int(x) for x in span.split("-"))
        assert int(total) == session["total"] and end - start + 1 == length
        if start != len(session["data"]):
            h.rfile.read(length)
            return self.error(h, 400, "badRequest",
                              f"expected offset {len(session['data'])}, got {start}")
        if fault and fault[0] == "status":  # the answer is lost; nothing kept
            h.rfile.read(length)
            return self.error(h, fault[1], fault[2] if len(fault) > 2 else "backendError")
        if fault and fault[0] in ("drop_after", "keep_then_drop"):
            # the connection dies mid-chunk; YouTube keeps what arrived (or a part of it)
            take = min(fault[1], length)
            data = h.rfile.read(take)
            if fault[0] == "keep_then_drop":
                session["data"] += data
            return self.drop(h)
        data = h.rfile.read(length)
        if fault and fault[0] == "partial":  # only the first n bytes are kept
            data = data[:fault[1]]
        session["data"] += data
        if fault and fault[0] == "drop_answer":  # everything kept, but the answer never arrives
            return self.drop(h)
        if len(session["data"]) == session["total"]:
            return self._finish(h, q["upload_id"], session)
        self._progress(h, session)

    # -- videos.list -------------------------------------------------------------------

    def _videos(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        items = []
        for video_id in q.get("id", "").split(","):
            video = self.videos.get(video_id)
            if video is None or video.get("deleted"):
                continue
            with self.lock:
                n = video["polls"]
                video["polls"] += 1
            answer = self.processing[n] if n < len(self.processing) else {
                "status": {"uploadStatus": "processed"},
                "processingDetails": {"processingStatus": "succeeded"}}
            status = dict(answer.get("status", {}))
            status.setdefault("privacyStatus", self.forced_privacy
                              or video["body"]["status"]["privacyStatus"])
            items.append({"id": video_id, "status": status,
                          "processingDetails": answer.get("processingDetails", {})})
        self.send(h, 200, {"items": items})
