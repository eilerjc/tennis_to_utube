"""One resumable upload to YouTube (DESIGN.md §12c): Google's resumable upload protocol.

1. ``POST …/upload/youtube/v3/videos?uploadType=resumable`` with the video's details
   opens a session; its address comes back in ``Location``.
2. The file is sent to that address in pieces (``PUT`` with ``Content-Range``). YouTube
   answers ``308`` with the bytes it has so far (``Range: bytes=0-N``): the checkpoint.
3. After any interruption the session is asked how far it got (``Content-Range: bytes
   */total``) and sending continues from there.
4. The last piece is answered with ``200``/``201`` and the video: only then is it done.

:class:`Upload` runs that for one :class:`Target` until the video is there, retrying
network errors and 5xx answers with exponential backoff for as long as it takes. It never
starts again from zero on its own: a session YouTube no longer has, or a file that
changed, is a failure the user has to decide about (``needs_restart``).
"""

from __future__ import annotations

import http.client
import json
import logging
import mimetypes
import random
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .ytapi import ApiError, AuthExpired, NetworkError, YouTube, parse_error

log = logging.getLogger("tennis_to_utube.upload")

PIECE = 256 * 1024  # written to the socket at a time (speed readings, quick pause)
CHUNK_UNIT = 256 * 1024  # chunks must be a multiple of this (Google's rule)

# What the upload is doing (the queue adds Queued, Paused, Uploaded, … around these).
UPLOADING = "Uploading"
RECONNECTING = "Reconnecting"
RESUMING = "Resuming"


class Stopped(Exception):
    """``should_stop`` asked for it; ``reason`` is what it returned (e.g. "pause")."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class UploadFailed(Exception):
    """Cannot go on without the user. ``needs_restart``: resuming is impossible (the
    session is gone or the file changed); ``auth``: sign in again, then it resumes."""

    def __init__(self, message: str, *, needs_restart: bool = False, auth: bool = False):
        super().__init__(message)
        self.needs_restart = needs_restart
        self.auth = auth


class Target(Protocol):
    """What is being uploaded, and how far it got (the queue's Job)."""

    path: str
    size: int
    mtime_ns: int
    session_uri: str | None
    confirmed: int  # bytes YouTube has confirmed

    def body(self) -> dict[str, Any]: ...


@dataclass
class Hooks:
    """How the upload reports back. All optional."""

    status: Callable[[str], None] = lambda s: None  # UPLOADING / RECONNECTING / RESUMING
    checkpoint: Callable[[], None] = lambda: None  # session or confirmed bytes changed: save
    sent: Callable[[int], None] = lambda n: None  # bytes written to the network
    should_stop: Callable[[], str | None] = lambda: None  # a reason to stop now, or None
    interrupted: Callable[[str], None] = lambda why: None  # a connection problem began
    retrying: Callable[[], None] = lambda: None  # another attempt after a problem
    waited: Callable[[float], None] = lambda seconds: None  # time lost to a problem


def chunk_bytes(chunk_mb: float) -> int:
    """The configured chunk size as a whole number of 256 KiB units (at least one)."""
    return max(1, round(chunk_mb * 1024 * 1024 / CHUNK_UNIT)) * CHUNK_UNIT


def parse_range(value: str | None) -> int:
    """Bytes confirmed, from a ``Range: bytes=0-N`` header (none: nothing yet)."""
    if not value:
        return 0
    try:
        return int(value.rsplit("-", 1)[1]) + 1
    except (IndexError, ValueError):
        raise ApiError(308, "badRange", f"unexpected Range header {value!r}") from None


class Upload:
    def __init__(self, api: YouTube, target: Target, *, chunk_mb: float = 16,
                 first_wait_s: float = 2.0, max_wait_s: float = 60.0, timeout_s: float = 60.0,
                 hooks: Hooks | None = None, sleep: Callable[[float], None] = time.sleep):
        self.api = api
        self.target = target
        self.chunk = chunk_bytes(chunk_mb)
        self.first_wait_s = first_wait_s
        self.max_wait_s = max_wait_s
        self.timeout_s = timeout_s
        self.hooks = hooks or Hooks()
        self._sleep = sleep
        self._conn: http.client.HTTPConnection | None = None
        self._conn_key: tuple[str, str] | None = None

    # -- the protocol ------------------------------------------------------------------

    def create_session(self) -> str:
        t = self.target
        content_type = mimetypes.guess_type(t.path)[0] or "video/*"
        _, headers, _ = self.api.call(
            "POST", f"{self.api.upload_base}/videos",
            params={"uploadType": "resumable", "part": "snippet,status"}, body=t.body(),
            headers={"X-Upload-Content-Length": str(t.size),
                     "X-Upload-Content-Type": content_type}, timeout=self.timeout_s)
        location = next((v for k, v in headers.items() if k.lower() == "location"), None)
        if not location:
            raise ApiError(200, "noSession", "YouTube did not return an upload session")
        return location

    def _connection(self, url: str) -> tuple[http.client.HTTPConnection, str]:
        u = urllib.parse.urlsplit(url)
        key = (u.scheme, u.netloc)
        if self._conn is None or self._conn_key != key:
            self.close()
            cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
            self._conn = cls(u.netloc, timeout=self.timeout_s)
            self._conn_key = key
        return self._conn, u.path + (f"?{u.query}" if u.query else "")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        self._conn = None

    def _answer(self, resp: http.client.HTTPResponse) -> int | dict[str, Any]:
        """Bytes confirmed so far, or the finished video."""
        raw = resp.read()
        if resp.status in (200, 201):
            try:
                video = json.loads(raw.decode("utf-8"))
            except ValueError:
                video = None
            if not isinstance(video, dict) or not video.get("id"):
                raise ApiError(resp.status, "noVideo", "YouTube confirmed the upload without a video id")
            return video
        if resp.status == 308:
            return parse_range(resp.getheader("Range"))
        raise parse_error(resp.status, raw)

    def query(self) -> int | dict[str, Any]:
        """Ask the session how many bytes it has (or get the video if it has them all)."""
        t = self.target
        conn, where = self._connection(t.session_uri or "")
        conn.request("PUT", where, body=b"", headers={
            "Content-Length": "0", "Content-Range": f"bytes */{t.size}",
            "Authorization": f"Bearer {self.api.auth.access_token()}"})
        return self._answer(conn.getresponse())

    def send(self, offset: int) -> int | dict[str, Any]:
        """Send the next chunk from ``offset``."""
        t = self.target
        length = min(self.chunk, t.size - offset)
        conn, where = self._connection(t.session_uri or "")
        token = self.api.auth.access_token()
        try:
            source = open(t.path, "rb")
        except OSError as exc:
            raise UploadFailed(f"Cannot read {Path(t.path).name}: {exc.strerror or exc}") from None
        with source:
            source.seek(offset)
            conn.putrequest("PUT", where)
            conn.putheader("Content-Length", str(length))
            conn.putheader("Content-Range", f"bytes {offset}-{offset + length - 1}/{t.size}")
            conn.putheader("Content-Type", mimetypes.guess_type(t.path)[0] or "video/*")
            conn.putheader("Authorization", f"Bearer {token}")
            conn.endheaders()
            left = length
            while left:
                self._check_stop()
                try:
                    data = source.read(min(PIECE, left))
                except OSError as exc:
                    raise UploadFailed(f"Cannot read {Path(t.path).name}: "
                                       f"{exc.strerror or exc}") from None
                if not data:
                    raise UploadFailed(f"{Path(t.path).name} is shorter than when the upload "
                                       "began.", needs_restart=True)
                conn.send(data)
                left -= len(data)
                self.hooks.sent(len(data))
        return self._answer(conn.getresponse())

    # -- running it --------------------------------------------------------------------

    def _check_stop(self) -> None:
        reason = self.hooks.should_stop()
        if reason:
            self.close()
            raise Stopped(reason)

    def _check_file(self) -> None:
        t = self.target
        try:
            st = Path(t.path).stat()
        except OSError as exc:
            raise UploadFailed(f"Cannot find {Path(t.path).name}: {exc.strerror or exc}") from None
        if st.st_size != t.size or st.st_mtime_ns != t.mtime_ns:
            raise UploadFailed(f"{Path(t.path).name} has changed since it was added, so the "
                               "part already uploaded no longer matches.", needs_restart=True)

    def _wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self._check_stop()
            left = end - time.monotonic()
            if left <= 0:
                return
            self._sleep(min(0.2, left))

    def run(self) -> dict[str, Any]:
        """Upload until YouTube confirms the whole file; returns the video resource.
        Raises :class:`Stopped` or :class:`UploadFailed`."""
        t = self.target
        verify = bool(t.session_uri)  # a session from before: ask where it stands first
        wait = self.first_wait_s
        down_since: float | None = None
        renewed = False
        stalls = 0
        try:
            while True:
                self._check_stop()
                self._check_file()
                try:
                    if not t.session_uri:
                        self.hooks.status(UPLOADING)
                        t.session_uri = self.create_session()
                        t.confirmed = 0
                        self.hooks.checkpoint()
                        log.info("%s: upload session opened", Path(t.path).name)
                        verify = False
                    if verify:
                        self.hooks.status(RESUMING)
                        result = self.query()
                        if not isinstance(result, dict):
                            log.info("%s: YouTube has %d of %d bytes; resuming from there",
                                     Path(t.path).name, result, t.size)
                            t.confirmed = result
                            self.hooks.checkpoint()
                        verify = False
                    else:
                        self.hooks.status(UPLOADING)
                        before = t.confirmed
                        result = self.send(t.confirmed)
                        if not isinstance(result, dict):
                            stalls = 0 if result > before else stalls + 1
                            if stalls >= 5:
                                raise UploadFailed("YouTube is not accepting more of this file.")
                            t.confirmed = result
                            self.hooks.checkpoint()
                    if down_since is not None:
                        self.hooks.waited(time.monotonic() - down_since)
                        down_since = None
                    wait = self.first_wait_s
                    renewed = False
                    if isinstance(result, dict):
                        t.confirmed = t.size
                        self.hooks.checkpoint()
                        log.info("%s: YouTube confirmed the upload, video %s",
                                 Path(t.path).name, result.get("id"))
                        return result
                    continue
                except AuthExpired as exc:
                    raise UploadFailed(f"{exc} Then press Retry: the upload resumes where it "
                                       "stopped.", auth=True) from None
                except ApiError as exc:
                    self.close()
                    if exc.status == 401 and not renewed:
                        renewed = True  # an expired access token: renew it and go on
                        try:
                            self.api.auth.access_token(force_refresh=True)
                        except AuthExpired as expired:
                            raise UploadFailed(f"{expired} Then press Retry: the upload resumes "
                                               "where it stopped.", auth=True) from None
                        except (ApiError, NetworkError):
                            pass
                        verify = bool(t.session_uri)
                        continue
                    if exc.status == 401:
                        raise UploadFailed("YouTube no longer accepts the sign-in: connect "
                                           "again, then press Retry.", auth=True) from None
                    if exc.status in (404, 410) and t.session_uri:
                        raise UploadFailed(
                            "YouTube no longer has this upload session (sessions run out after "
                            "about a week), so it cannot be resumed.", needs_restart=True) from None
                    if exc.quota:
                        raise UploadFailed(
                            "YouTube's daily upload limit for this app or channel is used up "
                            f"({exc.reason}). Press Retry tomorrow: the upload resumes where it "
                            "stopped.") from None
                    if not exc.retryable:
                        raise UploadFailed(f"YouTube refused the upload: {exc}") from None
                    why = str(exc)
                except (NetworkError, OSError, http.client.HTTPException) as exc:
                    self.close()
                    why = str(exc) or type(exc).__name__
                # A temporary problem: wait (longer each time), then ask YouTube where it stands.
                if down_since is None:
                    down_since = time.monotonic()
                    self.hooks.interrupted(why)
                    log.warning("%s: connection problem at %d bytes: %s",
                                Path(t.path).name, t.confirmed, why)
                self.hooks.status(RECONNECTING)
                self._wait(wait * random.uniform(0.75, 1.25))
                wait = min(self.max_wait_s, wait * 2)
                self.hooks.retrying()
                verify = bool(t.session_uri)
        finally:
            if down_since is not None:
                self.hooks.waited(time.monotonic() - down_since)
            self.close()
