"""The upload queue (DESIGN.md §12c): the videos to upload, kept on disk, and the
:class:`Manager` that works through them.

Every upload is one JSON file in ``<user config dir>/uploads/`` — details, the resumable
session, the last confirmed byte, counters. It is rewritten (to a temporary file, synced,
then renamed) at every checkpoint, so after a crash or a Windows restart the queue is as
it was and each upload resumes from what YouTube confirmed. Records of finished uploads
stay (hidden from the list when removed, never deleted).

The Manager is the reusable service: the GUI's Upload step and the Upload tool both use
it. No Qt here; the GUI polls :meth:`Manager.snapshot`.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

from .config import Config, load_upload_config, user_config_dir
from .ytapi import ApiError, Auth, AuthError, AuthExpired, NetworkError, YouTube
from .ytupload import RECONNECTING, RESUMING, UPLOADING, Hooks, Stopped, Upload, UploadFailed

log = logging.getLogger("tennis_to_utube.upload")

UPLOADS_DIRNAME = "uploads"
LOG_FILENAME = "upload.log"

QUEUED = "Queued"
PAUSED = "Paused"
UPLOADED = "Uploaded"  # YouTube confirmed it has the whole file (there is a video id)
PROCESSING = "Processing"  # YouTube is preparing it for viewing
COMPLETE = "Complete"  # YouTube finished processing
FAILED = "Failed"
STATUSES = (QUEUED, UPLOADING, PAUSED, RECONNECTING, RESUMING, UPLOADED, PROCESSING, COMPLETE,
            FAILED)
ACTIVE = (UPLOADING, RECONNECTING, RESUMING)  # a worker is on it
TRANSFERRED = (UPLOADED, PROCESSING, COMPLETE)

PRIVACY = ("private", "unlisted", "public")
# YouTube's video categories (the usual ones for these videos first).
CATEGORIES = {"17": "Sports", "22": "People & Blogs", "24": "Entertainment", "27": "Education",
              "26": "Howto & Style", "20": "Gaming", "10": "Music", "1": "Film & Animation",
              "2": "Autos & Vehicles", "15": "Pets & Animals", "19": "Travel & Events",
              "23": "Comedy", "25": "News & Politics", "28": "Science & Technology",
              "29": "Nonprofits & Activism"}

TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX = 500


class QueueBusy(Exception):
    """Another window (or the Upload tool) is already running this queue."""


def check_details(title: str, description: str = "", tags: list[str] | None = None) -> list[str]:
    """What YouTube would refuse about these details (empty: fine)."""
    problems = []
    if not title.strip():
        problems.append("The title is empty.")
    if len(title) > TITLE_MAX:
        problems.append(f"The title is longer than {TITLE_MAX} characters.")
    if "<" in title or ">" in title or "<" in description or ">" in description:
        problems.append("YouTube does not allow < or > in the title or description.")
    if len(description.encode("utf-8")) > DESCRIPTION_MAX:
        problems.append(f"The description is longer than {DESCRIPTION_MAX} bytes.")
    # (a tag with a space counts with its quotes; commas between tags count too)
    if sum(len(t) + (2 if " " in t else 0) + 1 for t in tags or []) > TAGS_MAX:
        problems.append(f"The tags are longer than {TAGS_MAX} characters together.")
    return problems


@dataclass
class Job:
    """One video to upload and everything known about its upload."""

    id: str
    path: str
    size: int
    mtime_ns: int
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    privacy: str = "unlisted"
    category: str = "17"
    made_for_kids: bool = False
    playlist: str = ""  # playlist id, or ""
    status: str = QUEUED
    created: float = 0.0
    session_uri: str | None = None
    confirmed: int = 0  # bytes YouTube has confirmed: the checkpoint
    checkpoint_at: float | None = None
    started: float | None = None  # first byte sent
    transferred_at: float | None = None  # YouTube confirmed the whole file
    completed_at: float | None = None  # YouTube finished processing
    video_id: str | None = None
    error: str = ""
    needs_restart: bool = False  # cannot be resumed: only "Restart from the beginning" helps
    note: str = ""  # processing progress, playlist/privacy remarks
    privacy_actual: str | None = None  # what YouTube says the video is
    playlist_done: bool = False
    # counters for the diagnostics
    sent_bytes: int = 0  # everything written to the network, repeats included
    active_s: float = 0.0  # time spent uploading (not queued or paused)
    reconnect_s: float = 0.0  # of which: waiting for the connection to come back
    retries: int = 0
    interruptions: int = 0
    # where the video id goes when it is known: {"match": path, "target": "trimmed" | "full"}
    link: dict[str, Any] | None = None
    link_done: bool = False
    hidden: bool = False  # removed from the list (the record is kept)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def url(self) -> str | None:
        return f"https://youtu.be/{self.video_id}" if self.video_id else None

    def body(self) -> dict[str, Any]:
        """The video resource sent when the upload session is opened."""
        snippet: dict[str, Any] = {"title": self.title, "description": self.description,
                                   "categoryId": self.category}
        if self.tags:
            snippet["tags"] = list(self.tags)
        return {"snippet": snippet,
                "status": {"privacyStatus": self.privacy,
                           "selfDeclaredMadeForKids": bool(self.made_for_kids)}}

    def to_dict(self) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "extra"}
        return {**self.extra, **data}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        known = {f.name for f in fields(cls)} - {"extra"}
        job = cls(**{k: v for k, v in data.items() if k in known})
        job.extra = {k: v for k, v in data.items() if k not in known}
        return job


def new_job(path: str | os.PathLike[str], title: str | None = None, **details: Any) -> Job:
    """A job for the file as it is now (its size and modified time are remembered: if the
    file changes, the upload will not silently continue with different content)."""
    p = Path(path).resolve()
    st = p.stat()
    if st.st_size == 0:
        raise ValueError(f"{p.name} is empty")
    return Job(id=uuid.uuid4().hex[:12], path=str(p), size=st.st_size, mtime_ns=st.st_mtime_ns,
               title=title if title is not None else p.stem, created=time.time(), **details)


class Store:
    """The job files. Writing is atomic: a crash leaves the old or the new file, never half."""

    def __init__(self, folder: Path | None = None):
        self.folder = folder if folder is not None else user_config_dir() / UPLOADS_DIRNAME

    def path(self, job: Job) -> Path:
        return self.folder / f"{job.id}.json"

    def save(self, job: Job) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.path(job)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(job.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def load_all(self) -> tuple[list[Job], list[str]]:
        """All jobs, oldest first, and warnings about files that could not be read (those
        are renamed ``.bad`` and left for inspection)."""
        jobs, warnings = [], []
        if not self.folder.is_dir():
            return jobs, warnings
        for path in sorted(self.folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                jobs.append(Job.from_dict(data))
            except (OSError, ValueError, TypeError) as exc:
                warnings.append(f"{path.name}: could not read ({exc}); kept as {path.name}.bad")
                try:
                    os.replace(path, path.with_name(path.name + ".bad"))
                except OSError:
                    pass
        jobs.sort(key=lambda j: j.created)
        return jobs, warnings

    def delete(self, job: Job) -> None:
        self.path(job).unlink(missing_ok=True)


class Meter:
    """Bytes per second over the last few seconds."""

    def __init__(self, window_s: float = 5.0, clock: Callable[[], float] = time.monotonic):
        self.window_s = window_s
        self._clock = clock
        self._samples: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def add(self, n: int) -> None:
        with self._lock:
            self._samples.append((self._clock(), n))
            self._trim()

    def _trim(self) -> None:
        limit = self._clock() - self.window_s
        while self._samples and self._samples[0][0] < limit:
            self._samples.popleft()

    def rate(self) -> float:
        with self._lock:
            self._trim()
            if not self._samples:
                return 0.0
            span = max(self._clock() - self._samples[0][0], 0.5)
            return sum(n for _, n in self._samples) / min(span, self.window_s)


def _keep_awake(active: bool) -> None:
    """Ask Windows not to sleep while this thread says so (the display may still turn off)."""
    if sys.platform != "win32":
        return
    import ctypes

    es_continuous, es_system_required = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(
        es_continuous | (es_system_required if active else 0))


def setup_log(folder: Path | None = None) -> Path:
    """Write the uploader's log to ``uploads/upload.log`` (2 MB, three old ones kept)."""
    folder = folder if folder is not None else user_config_dir() / UPLOADS_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_FILENAME
    if not any(getattr(h, "baseFilename", None) == str(path) for h in log.handlers):
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3,
                                                       encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    return path


@dataclass
class _Run:
    """A job being uploaded right now."""

    thread: threading.Thread
    meter: Meter
    since: float  # monotonic: when this run began
    inflight: int = 0  # sent since the last checkpoint
    stop: str | None = None  # "pause" / "stop"
    down_since: float | None = None  # a connection problem began (monotonic)


class Manager:
    """Works through the queue: up to ``parallel`` uploads at a time, the rest in order;
    then follows each video's processing on YouTube. Thread-safe."""

    def __init__(self, config: Config | None = None, auth: Auth | None = None,
                 store: Store | None = None, keep_awake: Callable[[bool], None] = _keep_awake):
        self.config = config or load_upload_config()
        self.auth = auth or Auth(self.config)
        self.api = YouTube(self.config, self.auth)
        self.store = store or Store()
        self.parallel = max(1, int(self.config.get("parallel")))
        self.prevent_sleep = bool(self.config.get("prevent_sleep"))
        self.warnings: list[str] = []
        self.jobs: dict[str, Job] = {}
        self._keep_awake = keep_awake
        self._lock = threading.RLock()
        self._runs: dict[str, _Run] = {}
        self._wake = threading.Event()
        self._closing = False
        self._threads: list[threading.Thread] = []
        self._lock_file: Any = None
        self._check_now = True  # look at processing without waiting for the next poll
        self.holding = False  # nothing new starts (the queue is held)
        self.awake = False  # Windows is being kept awake right now

    # -- opening and closing -----------------------------------------------------------

    def open(self) -> "Manager":
        """Take the queue (one process at a time) and read it. Uploads that were running
        when the app last stopped go back to Queued and resume from their checkpoint."""
        self.store.folder.mkdir(parents=True, exist_ok=True)
        self._lock_file = self._take_lock()
        jobs, self.warnings = self.store.load_all()
        with self._lock:
            for job in jobs:
                if job.status in ACTIVE:
                    log.info("%s: was uploading when the app stopped; back in the queue at "
                             "%d of %d bytes", job.name, job.confirmed, job.size)
                    job.status = QUEUED
                    self.store.save(job)
                self.jobs[job.id] = job
        return self

    def _take_lock(self) -> Any:
        fh = open(self.store.folder / "lock", "a+")
        try:
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise QueueBusy("Uploads are already running in another window of this app (or "
                            "in the Upload tool).") from None
        return fh

    def start(self) -> None:
        """Begin working through the queue (after :meth:`open`)."""
        if self._threads:
            return
        for target in (self._coordinate, self._follow_processing):
            thread = threading.Thread(target=target, name=f"upload-{target.__name__}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def close(self, timeout_s: float = 10.0) -> None:
        """Stop (uploads in progress keep their checkpoint and resume next time)."""
        with self._lock:
            self._closing = True
            for run in self._runs.values():
                run.stop = run.stop or "stop"
            runs = [r.thread for r in self._runs.values()]
        self._wake.set()
        deadline = time.monotonic() + timeout_s
        for thread in runs + self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        self._threads = []
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    # -- the queue ---------------------------------------------------------------------

    def add(self, job: Job) -> Job:
        with self._lock:
            self.jobs[job.id] = job
            self.store.save(job)
        log.info("%s: added (%d bytes), %s, title %r", job.name, job.size, job.privacy, job.title)
        self._wake.set()
        return job

    def _job(self, job_id: str) -> Job:
        return self.jobs[job_id]

    def pause(self, job_id: str) -> None:
        with self._lock:
            job = self._job(job_id)
            run = self._runs.get(job_id)
            if run is not None:
                run.stop = "pause"
            elif job.status == QUEUED:
                self._set(job, PAUSED)

    def resume(self, job_id: str) -> None:
        with self._lock:
            job = self._job(job_id)
            run = self._runs.get(job_id)
            if run is not None and run.stop == "pause":
                run.stop = None  # asked to pause, not paused yet: carry on
            elif job.status == PAUSED:
                self._set(job, QUEUED)
        self._wake.set()

    def retry(self, job_id: str) -> None:
        """Try a failed upload again from its checkpoint (not for ``needs_restart``)."""
        with self._lock:
            job = self._job(job_id)
            if job.status != FAILED or job.needs_restart:
                return
            job.error = ""
            self._set(job, UPLOADED if job.video_id else QUEUED)
            self._check_now = True
        self._wake.set()

    def restart(self, job_id: str) -> None:
        """Start a failed upload again from the beginning — only ever on the user's say-so."""
        with self._lock:
            job = self._job(job_id)
            if job.status != FAILED or job.video_id:
                return
            st = Path(job.path).stat()
            log.info("%s: restarted from the beginning by the user (was at %d bytes)",
                     job.name, job.confirmed)
            job.size, job.mtime_ns = st.st_size, st.st_mtime_ns
            job.session_uri, job.confirmed, job.checkpoint_at = None, 0, None
            job.error, job.needs_restart = "", False
            self._set(job, QUEUED)
        self._wake.set()

    def remove(self, job_id: str) -> bool:
        """Take a job off the list. An unfinished one is forgotten; a transferred one is
        only hidden (its record is kept). False while it is uploading (pause it first)."""
        with self._lock:
            job = self._job(job_id)
            if job_id in self._runs:
                return False
            if job.video_id:
                job.hidden = True
                self.store.save(job)
            else:
                log.info("%s: removed from the queue at %d of %d bytes", job.name,
                         job.confirmed, job.size)
                del self.jobs[job_id]
                self.store.delete(job)
            return True

    def edit(self, job_id: str, **details: Any) -> bool:
        """Change title/description/… of a job that has not started (YouTube takes the
        details when the session opens)."""
        with self._lock:
            job = self._job(job_id)
            if job.session_uri or job_id in self._runs or job.video_id:
                return False
            for key, value in details.items():
                setattr(job, key, value)
            self.store.save(job)
            return True

    def hold(self, holding: bool) -> None:
        """Pause everything / let the queue run again."""
        with self._lock:
            self.holding = holding
            for job_id, job in self.jobs.items():
                if holding:
                    self.pause(job_id)
                elif job.status == PAUSED:
                    self.resume(job_id)
        self._wake.set()

    def mark_linked(self, job_id: str) -> None:
        with self._lock:
            job = self._job(job_id)
            job.link_done = True
            self.store.save(job)

    def _set(self, job: Job, status: str) -> None:
        if job.status != status:
            log.info("%s: %s -> %s", job.name, job.status, status)
        job.status = status
        self.store.save(job)

    # -- what to show ------------------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        """Every visible job with live figures: ``done`` (bytes sent, counting the piece on
        its way), ``percent``, ``speed`` and ``average`` (bytes/s), ``eta_s``, ``active_s``
        and ``reconnect_s`` up to now."""
        now = time.monotonic()
        rows = []
        with self._lock:
            for job in self.jobs.values():
                if job.hidden:
                    continue
                row = job.to_dict()
                run = self._runs.get(job.id)
                active = job.active_s + (now - run.since if run else 0.0)
                reconnect = job.reconnect_s
                if run and run.down_since is not None:
                    reconnect += now - run.down_since
                done = job.size if job.video_id else min(job.size, job.confirmed
                                                         + (run.inflight if run else 0))
                speed = run.meter.rate() if run and job.status == UPLOADING else 0.0
                moving = active - reconnect
                average = job.sent_bytes / moving if moving > 1 else 0.0
                basis = speed if speed > 0 else average
                row.update(
                    name=job.name, url=job.url, done=done,
                    percent=100.0 * done / job.size if job.size else 0.0,
                    speed=speed, average=average, active_s=active, reconnect_s=reconnect,
                    eta_s=(job.size - done) / basis if basis > 0 and not job.video_id else None,
                    remaining=job.size - done, running=run is not None)
                rows.append(row)
        return rows

    def busy(self) -> bool:
        """Something is uploading or waiting to."""
        with self._lock:
            return bool(self._runs) or any(j.status == QUEUED for j in self.jobs.values())

    def uploading(self) -> bool:
        with self._lock:
            return bool(self._runs)

    # -- the workers -------------------------------------------------------------------

    def _coordinate(self) -> None:
        while True:
            with self._lock:
                if self._closing and not self._runs:
                    break
                for job_id in [i for i, r in self._runs.items() if not r.thread.is_alive()]:
                    del self._runs[job_id]
                if not self._closing and not self.holding and self.auth.connected:
                    queued = sorted((j for j in self.jobs.values()
                                     if j.status == QUEUED and j.id not in self._runs),
                                    key=lambda j: j.created)
                    for job in queued[:max(0, self.parallel - len(self._runs))]:
                        self._begin(job)
                want_awake = self.prevent_sleep and bool(self._runs)
            if want_awake != self.awake:
                self.awake = want_awake
                try:
                    self._keep_awake(want_awake)
                except Exception:  # never let this stop the uploads
                    log.exception("could not change the keep-awake state")
            self._wake.wait(0.25)
            self._wake.clear()
        if self.awake:
            self.awake = False
            self._keep_awake(False)

    def _begin(self, job: Job) -> None:
        thread = threading.Thread(target=self._upload, args=(job,), name=f"upload-{job.id}",
                                  daemon=True)
        now = time.monotonic()
        self._runs[job.id] = _Run(thread, Meter(), now)
        if job.started is None:
            job.started = time.time()
        job.error = ""
        thread.start()

    def _upload(self, job: Job) -> None:
        run = self._runs[job.id]

        def status(text: str) -> None:
            with self._lock:
                if job.status != text:
                    if text != UPLOADING:
                        run.inflight = 0
                    self._set(job, text)

        def checkpoint() -> None:
            with self._lock:
                run.inflight = 0
                job.checkpoint_at = time.time()
                self.store.save(job)

        def sent(n: int) -> None:
            run.meter.add(n)
            with self._lock:
                run.inflight += n
                job.sent_bytes += n

        def interrupted(why: str) -> None:
            with self._lock:
                run.down_since = time.monotonic()
                job.interruptions += 1
                job.note = f"Connection problem: {why}"

        def retrying() -> None:
            with self._lock:
                job.retries += 1

        def waited(seconds: float) -> None:
            with self._lock:
                run.down_since = None
                job.reconnect_s += seconds
                if job.note.startswith("Connection problem"):
                    job.note = ""

        retry = self.config.get("retry")
        upload = Upload(self.api, job, chunk_mb=float(self.config.get("chunk_mb")),
                        first_wait_s=float(retry["first_s"]), max_wait_s=float(retry["max_s"]),
                        timeout_s=float(retry["timeout_s"]),
                        hooks=Hooks(status, checkpoint, sent, lambda: run.stop, interrupted,
                                    retrying, waited))
        outcome, error, needs_restart = QUEUED, "", False
        video: dict[str, Any] | None = None
        try:
            video = upload.run()
            outcome = UPLOADED
        except Stopped as stop:
            outcome = PAUSED if stop.reason == "pause" else QUEUED
        except UploadFailed as exc:
            outcome, error, needs_restart = FAILED, str(exc), exc.needs_restart
            log.error("%s: failed at %d of %d bytes: %s", job.name, job.confirmed, job.size, exc)
        except Exception as exc:  # a bug must not lose the checkpoint or stall the queue
            outcome, error = FAILED, f"Unexpected error: {exc!r}"
            log.exception("%s: unexpected error", job.name)
        with self._lock:
            job.active_s += time.monotonic() - run.since
            run.inflight = 0
            if video is not None:
                job.video_id = str(video["id"])
                job.transferred_at = time.time()
                job.note = ""
                self._check_now = True
            job.error, job.needs_restart = error, needs_restart
            self._set(job, outcome)
            del self._runs[job.id]
        self._wake.set()

    # -- after the transfer: processing on YouTube -------------------------------------

    def _follow_processing(self) -> None:
        poll_s = float(self.config.get("processing.poll_s"))
        last = 0.0
        while not self._closing:
            with self._lock:
                due = self._check_now or time.monotonic() - last >= poll_s
                watching = [j for j in self.jobs.values()
                            if j.status in (UPLOADED, PROCESSING) and j.video_id]
            if due and watching and self.auth.connected:
                self._check_now = False
                last = time.monotonic()
                try:
                    self._check_processing(watching)
                except AuthError as exc:
                    self._remark(watching, f"Cannot check processing: {exc}")
                except (ApiError, NetworkError) as exc:
                    log.warning("could not check processing: %s", exc)
                except Exception:
                    log.exception("unexpected error while checking processing")
            time.sleep(min(0.25, poll_s))

    def _remark(self, jobs: list[Job], note: str) -> None:
        with self._lock:
            for job in jobs:
                if job.note != note:
                    job.note = note
                    self.store.save(job)

    def _check_processing(self, watching: list[Job]) -> None:
        found = self.api.videos([j.video_id for j in watching if j.video_id])
        for job in watching:
            item = found.get(job.video_id or "")
            if not job.playlist_done and job.playlist:
                self._add_to_playlist(job)
            with self._lock:
                if item is None:
                    job.error = "YouTube no longer lists this video (was it deleted?)."
                    self._set(job, FAILED)
                    continue
                status = item.get("status") or {}
                details = item.get("processingDetails") or {}
                upload_status = status.get("uploadStatus")
                job.privacy_actual = status.get("privacyStatus") or job.privacy_actual
                notes = []
                if job.privacy_actual and job.privacy_actual != job.privacy:
                    notes.append(f"YouTube made this video {job.privacy_actual}, not "
                                 f"{job.privacy} (for uploads from an app that Google has not "
                                 "audited, it locks them as private).")
                if job.extra.get("playlist_error"):
                    notes.append(job.extra["playlist_error"])
                if upload_status in ("failed", "rejected", "deleted") \
                        or details.get("processingStatus") in ("failed", "terminated"):
                    why = (status.get("failureReason") or status.get("rejectionReason")
                           or details.get("processingFailureReason") or upload_status or "failed")
                    job.error = f"YouTube could not process the video ({why})."
                    job.note = " ".join(notes)
                    self._set(job, FAILED)
                elif upload_status == "processed":
                    job.completed_at = time.time()
                    job.note = " ".join(notes)
                    self._set(job, COMPLETE)
                else:
                    progress = details.get("processingProgress") or {}
                    try:
                        total, parts = int(progress["partsTotal"]), int(progress["partsProcessed"])
                        left = int(progress.get("timeLeftMs", 0)) // 60000
                        notes.insert(0, f"{100 * parts // max(total, 1)}% processed"
                                     + (f", about {left} min left" if left else ""))
                    except (KeyError, ValueError, TypeError):
                        pass
                    job.note = " ".join(notes)
                    self._set(job, PROCESSING)

    def _add_to_playlist(self, job: Job) -> None:
        try:
            self.api.add_to_playlist(job.playlist, job.video_id or "")
        except (NetworkError, AuthError):
            return  # next time
        except ApiError as exc:
            if exc.retryable:
                return
            job.extra["playlist_error"] = f"Not added to the playlist: {exc.message or exc}."
            log.warning("%s: not added to playlist %s: %s", job.name, job.playlist, exc)
        with self._lock:
            job.playlist_done = True
            self.store.save(job)
