"""YouTube sign-in and the few Data API v3 calls the uploader needs (DESIGN.md §12c).

Standard library only. Sign-in is Google's OAuth 2.0 flow for installed apps: the browser
opens Google's own page, Google sends a one-time code back to a loopback address on this
PC (with PKCE), and the code is exchanged for tokens. The app never sees the password.
The tokens are kept in ``<user config dir>/youtube_token.bin``, encrypted for the Windows
user (DPAPI); elsewhere (development) the file is only made private to the user.

The OAuth client (``google.client_id`` / ``client_secret``) comes from ``upload.toml``:
the owner's own Google Cloud project (docs/UPLOAD_SETUP.md).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import Config, user_config_dir

TOKEN_FILENAME = "youtube_token.bin"

# Daily limits: retrying today does not help.
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"}
# Temporary: retry with backoff.
RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}


class NetworkError(Exception):
    """No answer from the server (no internet, timeout, connection dropped)."""


class ApiError(Exception):
    """The server answered with an error."""

    def __init__(self, status: int, reason: str = "", message: str = ""):
        super().__init__(f"HTTP {status}" + (f" {reason}" if reason else "")
                         + (f": {message}" if message else ""))
        self.status = status
        self.reason = reason
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.status >= 500 or self.status in (408, 429) or self.reason in RETRY_REASONS

    @property
    def quota(self) -> bool:
        return self.reason in QUOTA_REASONS


class AuthError(Exception):
    """Sign-in is missing, was refused or cannot be completed."""


class AuthExpired(AuthError):
    """The stored sign-in no longer works (revoked or expired): connect again."""


def parse_error(status: int, body: bytes) -> ApiError:
    """Google's two error shapes: the API's ``{"error": {...}}`` and OAuth's
    ``{"error": "...", "error_description": "..."}``."""
    reason = message = ""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        data = None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            reason = str(errors[0].get("reason") or "")
        reason = reason or str(error.get("status") or "")
    elif isinstance(error, str):
        reason = error
        message = str(data.get("error_description") or "")
    return ApiError(status, reason, message)


def request(method: str, url: str, *, token: str | None = None,
            params: Mapping[str, str] | None = None, body: Any = None,
            form: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None,
            timeout: float = 30.0) -> tuple[int, Mapping[str, str], Any]:
    """One HTTP request; returns (status, headers, parsed JSON or None). Errors become
    :class:`ApiError` (the server said no) or :class:`NetworkError` (no answer)."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None
    head = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        head["Content-Type"] = "application/json; charset=UTF-8"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("ascii")
        head["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        head["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status, resp_headers = resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        with exc:
            raise parse_error(exc.code, exc.read()) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:  # incl. timeouts
        raise NetworkError(str(getattr(exc, "reason", exc)) or type(exc).__name__) from None
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw.strip() else None
    except ValueError:
        parsed = None
    return status, resp_headers, parsed


# -- token storage ----------------------------------------------------------------------


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Encrypt/decrypt for the signed-in Windows user (CryptProtectData)."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "Windows could not "
                      + ("encrypt" if protect else "decrypt") + " the sign-in")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


class TokenStore:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else user_config_dir() / TOKEN_FILENAME

    def load(self) -> dict[str, Any] | None:
        try:
            raw = self.path.read_bytes()
            if sys.platform == "win32":
                raw = _dpapi(raw, protect=False)
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("refresh_token") else None

    def save(self, data: Mapping[str, Any]) -> None:
        raw = json.dumps(dict(data)).encode("utf-8")
        if sys.platform == "win32":
            raw = _dpapi(raw, protect=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(raw)
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


# -- sign-in ----------------------------------------------------------------------------

_DONE_PAGE = ("<html><body style='font-family:sans-serif;margin:3em'><h2>{}</h2>"
              "<p>You can close this window and go back to Tennis to YouTube.</p></body></html>")


class Auth:
    """The connected YouTube account: connect, a fresh access token on demand, disconnect."""

    def __init__(self, config: Config, store: TokenStore | None = None):
        self.google = config.get("google")
        self.store = store or TokenStore()
        self._lock = threading.Lock()
        self._token: dict[str, Any] | None = None

    @property
    def configured(self) -> bool:
        """An OAuth client id is set (upload.toml)."""
        return bool(str(self.google.get("client_id") or "").strip())

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._load() is not None

    def _load(self) -> dict[str, Any] | None:
        if self._token is None:
            self._token = self.store.load()
        return self._token

    def connect(self, open_browser: Callable[[str], Any] = webbrowser.open,
                timeout_s: float = 300.0, cancel: threading.Event | None = None) -> None:
        """Sign in through the browser. Blocks until Google sends the code back, the user
        refuses, ``cancel`` is set or the time is up."""
        if not self.configured:
            raise AuthError("No Google client id is set: see docs/UPLOAD_SETUP.md "
                            "(upload.toml, [google] client_id).")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(24)
        got: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server naming)
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if "code" in query or "error" in query:
                    got.update({k: v[0] for k, v in query.items()})
                    ok = "code" in query and got.get("state") == state
                    page = _DONE_PAGE.format("Connected to YouTube." if ok
                                             else "YouTube was not connected.")
                else:
                    page = _DONE_PAGE.format("Nothing to do here.")
                raw = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: Any) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 0.25
        redirect = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            url = self.google["auth_uri"] + "?" + urllib.parse.urlencode({
                "client_id": self.google["client_id"], "redirect_uri": redirect,
                "response_type": "code", "scope": " ".join(self.google["scopes"]),
                "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
                "access_type": "offline", "prompt": "consent"})
            open_browser(url)
            deadline = time.monotonic() + timeout_s
            while not got:
                if cancel is not None and cancel.is_set():
                    raise AuthError("cancelled")
                if time.monotonic() > deadline:
                    raise AuthError("Timed out waiting for the sign-in in the browser.")
                server.handle_request()
        finally:
            server.server_close()
        if "error" in got:
            raise AuthError(f"Google did not allow the connection ({got['error']}).")
        if got.get("state") != state:
            raise AuthError("The answer did not match this sign-in; try again.")
        try:
            _, _, data = request("POST", self.google["token_uri"], form={
                "code": got["code"], "client_id": self.google["client_id"],
                "client_secret": self.google.get("client_secret", ""),
                "redirect_uri": redirect, "grant_type": "authorization_code",
                "code_verifier": verifier})
        except ApiError as exc:
            raise AuthError(f"Google refused the sign-in: {exc}") from None
        except NetworkError as exc:
            raise AuthError(f"Could not reach Google to finish the sign-in: {exc}") from None
        if not isinstance(data, dict) or not data.get("refresh_token"):
            raise AuthError("Google did not return a lasting sign-in (no refresh token).")
        with self._lock:
            self._token = self._keep(data, {})
            self.store.save(self._token)

    @staticmethod
    def _keep(data: Mapping[str, Any], old: Mapping[str, Any]) -> dict[str, Any]:
        return {"access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token") or old.get("refresh_token"),
                "expires_at": time.time() + float(data.get("expires_in", 0) or 0),
                "scope": data.get("scope") or old.get("scope", "")}

    def access_token(self, force_refresh: bool = False) -> str:
        """A valid access token, refreshed when it is about to run out."""
        with self._lock:
            token = self._load()
            if token is None:
                raise AuthExpired("Not connected to YouTube.")
            if not force_refresh and token.get("access_token") \
                    and token.get("expires_at", 0) - 60 > time.time():
                return token["access_token"]
            try:
                _, _, data = request("POST", self.google["token_uri"], form={
                    "client_id": self.google["client_id"],
                    "client_secret": self.google.get("client_secret", ""),
                    "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
            except ApiError as exc:
                if exc.status in (400, 401) or exc.reason == "invalid_grant":
                    raise AuthExpired("The YouTube sign-in has expired or was withdrawn: "
                                      "connect again.") from None
                raise
            if not isinstance(data, dict) or not data.get("access_token"):
                raise NetworkError("unexpected answer while renewing the sign-in")
            self._token = self._keep(data, token)
            self.store.save(self._token)
            return self._token["access_token"]

    def disconnect(self) -> None:
        """Forget the sign-in here and ask Google to withdraw it (best effort)."""
        with self._lock:
            token = self._load()
            self._token = None
            self.store.clear()
        if token and self.google.get("revoke_uri"):
            try:
                request("POST", self.google["revoke_uri"], form={"token": token["refresh_token"]},
                        timeout=10)
            except (ApiError, NetworkError):
                pass


class YouTube:
    """Data API calls with the connected account (a 401 renews the token once)."""

    def __init__(self, config: Config, auth: Auth):
        self.auth = auth
        self.api_base = str(config.get("google.api_base")).rstrip("/")
        self.upload_base = str(config.get("google.upload_base")).rstrip("/")

    def call(self, method: str, url: str, **kwargs: Any) -> tuple[int, Mapping[str, str], Any]:
        try:
            return request(method, url, token=self.auth.access_token(), **kwargs)
        except ApiError as exc:
            if exc.status != 401:
                raise
        try:
            return request(method, url, token=self.auth.access_token(force_refresh=True), **kwargs)
        except ApiError as exc:
            if exc.status == 401:
                raise AuthExpired("YouTube no longer accepts the sign-in: connect again.") from None
            raise

    def channel(self) -> dict[str, str] | None:
        """The connected channel: ``{"id", "title"}`` (None: the account has no channel)."""
        _, _, data = self.call("GET", f"{self.api_base}/channels",
                               params={"part": "snippet", "mine": "true"})
        items = (data or {}).get("items") or []
        if not items:
            return None
        return {"id": items[0].get("id", ""), "title": items[0].get("snippet", {}).get("title", "")}

    def playlists(self) -> list[dict[str, str]]:
        """The channel's playlists, ``{"id", "title"}``, by title."""
        found: list[dict[str, str]] = []
        page = None
        while True:
            params = {"part": "snippet", "mine": "true", "maxResults": "50"}
            if page:
                params["pageToken"] = page
            _, _, data = self.call("GET", f"{self.api_base}/playlists", params=params)
            data = data or {}
            found += [{"id": i.get("id", ""), "title": i.get("snippet", {}).get("title", "")}
                      for i in data.get("items") or []]
            page = data.get("nextPageToken")
            if not page or len(found) >= 500:
                return sorted(found, key=lambda p: p["title"].lower())

    def videos(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """``status`` and ``processingDetails`` of the account's own videos, by id (ids
        YouTube no longer has are missing)."""
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), 50):
            _, _, data = self.call("GET", f"{self.api_base}/videos", params={
                "part": "status,processingDetails", "id": ",".join(ids[i:i + 50])})
            for item in (data or {}).get("items") or []:
                out[item.get("id", "")] = item
        return out

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        self.call("POST", f"{self.api_base}/playlistItems", params={"part": "snippet"},
                  body={"snippet": {"playlistId": playlist_id,
                                    "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
