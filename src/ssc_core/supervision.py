"""Asynchronous Linux process boundary for numeric-offline-v1.

Never wait on returned Futures in input, release or command-gate callbacks.
A supervisor thread owns all IPC, real deadlines and process teardown. Only
one operation is admitted per evaluator; there is no unbounded work queue.
"""
from concurrent.futures import Future
from dataclasses import dataclass
import json
import math
import os
import platform
import select
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .policy import NUMERIC_OFFLINE_V1
from .records import ConstraintProgram
from .validation import to_dict


@dataclass(frozen=True, slots=True)
class EvaluatorLimits:
    address_space_bytes: int = 256 * 1024 * 1024
    payload_bytes: int = 1024 * 1024
    call_timeout_s: float = 0.01
    preparation_timeout_s: float = 5.0
    teardown_grace_s: float = 0.1


EVALUATOR_LIMITS = EvaluatorLimits()


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """Local evidence, not a validation receipt or a motion decision.

    ``ok`` with ``operation='prepare'`` means a ready interpreter, not a scalar.
    All failures have residual=None. Times use the host's real monotonic clock;
    elapsed_s includes serialization, IPC and cleanup, worker_elapsed_s does not.
    reaped is None for a live successful worker; on retirement it records whether
    wait completed. A cleanup failure retains the sole process handle for retry.
    """
    operation: str
    request_id: str
    ok: bool
    code: str
    residual: float | tuple[float, ...] | None
    stage: str
    diagnostic: str
    started_ns: int
    deadline_ns: int
    finished_ns: int
    worker_elapsed_s: float | None
    cleanup_s: float
    reaped: bool | None
    worker_pid: int | None
    worker_generation: str | None
    program_id: str | None
    source_sha256: str | None
    policy_id: str
    limits: EvaluatorLimits
    environment: tuple[tuple[str, str], ...]

    @property
    def elapsed_s(self) -> float:
        return (self.finished_ns - self.started_ns) / 1e9


class _Failure(Exception):
    def __init__(self, code, diagnostic, stage="supervision"):
        self.code, self.diagnostic, self.stage = code, diagnostic, stage


def _encode(message):
    # Reject non-data objects and bound traversal before json allocates a frame.
    # This is the IPC envelope bound; interpreter shape/grammar checks are separate.
    remaining = EVALUATOR_LIMITS.payload_bytes
    def check(value, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise _Failure("payload_limit", "IPC payload exceeds 1 MiB")
        if depth > 32:
            raise _Failure("invalid_request", "IPC data nesting exceeds 32")
        kind = type(value)
        if kind is str:
            if len(value) > remaining:
                raise _Failure("payload_limit", "IPC payload exceeds 1 MiB")
            remaining -= len(value.encode("utf-8"))
        elif kind in (list, tuple):
            if len(value) > remaining:
                raise _Failure("payload_limit", "IPC payload exceeds 1 MiB")
            for item in value:
                check(item, depth + 1)
        elif kind is dict:
            if len(value) > remaining:
                raise _Failure("payload_limit", "IPC payload exceeds 1 MiB")
            for key, item in value.items():
                if type(key) is not str:
                    raise _Failure("invalid_request", "IPC keys must be strings")
                check(key, depth + 1)
                check(item, depth + 1)
        elif kind in (int, float):
            try:
                valid = math.isfinite(value)
            except OverflowError:
                valid = False
            if not valid:
                raise _Failure("invalid_request", "IPC numbers must be finite")
        elif value is not None and kind is not bool:
            raise _Failure("invalid_request", "IPC accepts plain JSON data only")
        if remaining < 0:
            raise _Failure("payload_limit", "IPC payload exceeds 1 MiB")
    check(message)
    data = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(data) > EVALUATOR_LIMITS.payload_bytes:
        raise _Failure("payload_limit", "IPC payload exceeds 1 MiB")
    return data + b"\n"


def _decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite JSON")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


class SupervisedEvaluator:
    """One prepared program, one in-flight request, fresh IPC after every failure.

    Install the wheel before use: workers launch ``python -I -m ssc_core._worker``.
    prepare(program, motion_disabled=True) starts a fresh worker and compiles the
    artifact there. submit takes only already-resolved numerical inputs. An
    optional absolute deadline_ns (time.monotonic_ns domain) can shorten, never
    extend, the 10 ms call budget. close cancels in-flight work asynchronously.
    """
    _worker_module = "ssc_core._worker"

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._active = None
        self._closed = False
        self._ready = False
        self._process = None
        self._generation = None
        self._program_id = None
        self._source_sha256 = None
        self._environment = tuple(sorted({
            "system": platform.system(), "release": platform.release(),
            "machine": platform.machine(), "python": platform.python_version(),
            "implementation": platform.python_implementation(),
        }.items()))

    @property
    def ready(self):
        with self._lock:
            return self._ready and self._active is None and not self._closed

    def prepare(self, program, *, motion_disabled):
        return self._start("prepare", program, motion_disabled=motion_disabled)

    def submit(self, p_tcp, R_control_tcp, keypoints, axis_dirs, params, *, deadline_ns=None):
        return self._start("evaluate", (p_tcp, R_control_tcp, keypoints, axis_dirs, params),
                           deadline_ns=deadline_ns)

    def close(self):
        """Cancel work and return its cleanup Future (or a new close Future).

        Once closed, this object cannot be prepared again. A failed evaluation
        alone does not close it: explicit preparation is the recovery path.
        """
        with self._lock:
            self._closed = True
            self._ready = False
            self._cancel.set()
            if self._active is not None:
                return self._active
        return self._start("close", None)

    def _outcome(self, operation, request_id, started, deadline, *, code, stage="supervision",
                 diagnostic="", residual=None, worker_elapsed=None, cleanup=0., reaped=None,
                 pid=None, finished=None):
        return EvaluationOutcome(operation, request_id, code == "ok", code, residual, stage,
                                 diagnostic[:512], started, deadline, finished if finished is not None else time.monotonic_ns(),
                                 worker_elapsed, cleanup, reaped, pid, self._generation,
                                 self._program_id, self._source_sha256,
                                 NUMERIC_OFFLINE_V1.policy_id, EVALUATOR_LIMITS, self._environment)

    def _start(self, operation, payload, *, motion_disabled=False, deadline_ns=None):
        started = time.monotonic_ns()
        budget = (EVALUATOR_LIMITS.preparation_timeout_s if operation == "prepare"
                  else EVALUATOR_LIMITS.call_timeout_s)
        deadline = started + int(budget * 1e9)
        invalid_deadline = deadline_ns is not None and (type(deadline_ns) is not int or deadline_ns < 0)
        if deadline_ns is not None and not invalid_deadline:
            deadline = min(deadline, deadline_ns)
        request_id = uuid4().hex
        future = Future()
        # Cancellation must go through close(), so Future.cancel cannot strand a worker.
        future.set_running_or_notify_cancel()
        with self._lock:
            code = None
            if self._active is not None:
                code = "busy"
            elif operation != "close" and self._closed:
                code = "closed"
            elif invalid_deadline:
                code = "invalid_deadline"
            elif operation == "prepare" and motion_disabled is not True:
                code = "motion_not_disabled"
            elif operation == "evaluate" and not self._ready:
                code = "not_prepared"
            if code:
                future.set_result(self._outcome(operation, request_id, started, deadline, code=code))
                return future
            self._active = future
            self._ready = False
            if operation != "close":
                self._cancel.clear()
        thread = threading.Thread(target=self._operate,
                                  args=(future, operation, payload, request_id, started, deadline),
                                  name="ssc-evaluator-supervisor", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            # A failed host thread allocation must not strand an active Future.
            # Retain at most one process for an explicit close/retry; never join
            # it in the caller's callback when no supervisor thread is available.
            with self._lock:
                self._active = None
                self._ready = False
                outcome = self._outcome(
                    operation, request_id, started, deadline, code="supervisor_error",
                    diagnostic="cannot start supervisor thread; close/retry cleanup required",
                    reaped=self._process is None)
            future.set_result(outcome)
        return future

    def _spawn(self):
        environment = os.environ.copy()
        for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS"):
            environment[key] = "1"
        self._process = subprocess.Popen(
            [sys.executable, "-I", "-m", self._worker_module],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, close_fds=True, env=environment,
        )
        os.set_blocking(self._process.stdin.fileno(), False)
        os.set_blocking(self._process.stdout.fileno(), False)

    def _check_deadline(self, deadline):
        if self._cancel.is_set():
            raise _Failure("cancelled", "worker withdrawn by close")
        if time.monotonic_ns() >= deadline:
            raise _Failure("timeout", "original monotonic deadline expired")

    def _normalize_residual(self, residual):
        if type(residual) not in (int, float) or not math.isfinite(residual):
            raise _Failure("malformed_response", "success requires a finite scalar")
        return residual

    def _exchange(self, frame, request_id, deadline):
        process = self._process
        read_fd, write_fd = process.stdout.fileno(), process.stdin.fileno()
        sent = 0
        response = bytearray()
        while True:
            self._check_deadline(deadline)
            if process.poll() is not None:
                raise _Failure("worker_crash", f"worker exited with code {process.returncode}")
            timeout = min(0.001, max(0, (deadline - time.monotonic_ns()) / 1e9))
            readers, writers, _ = select.select([read_fd], [write_fd] if sent < len(frame) else [], [], timeout)
            if writers:
                try:
                    sent += os.write(write_fd, frame[sent:sent + 65536])
                except BlockingIOError:
                    pass
            if readers:
                try:
                    chunk = os.read(read_fd, min(65536, EVALUATOR_LIMITS.payload_bytes + 2 - len(response)))
                except BlockingIOError:
                    continue
                if not chunk:
                    raise _Failure("worker_crash", "worker closed its reply stream")
                response.extend(chunk)
                if len(response) > EVALUATOR_LIMITS.payload_bytes + 1:
                    raise _Failure("malformed_response", "reply exceeds IPC ceiling")
                if b"\n" in response:
                    if not response.endswith(b"\n") or response.count(b"\n") != 1:
                        raise _Failure("malformed_response", "reply contains extra frames")
                    self._check_deadline(deadline)
                    try:
                        result = _decode(response)
                    except (ValueError, UnicodeError, RecursionError):
                        raise _Failure("malformed_response", "reply is not bounded valid JSON") from None
                    if (type(result) is not dict or result.get("request_id") != request_id
                            or result.get("generation") != self._generation):
                        raise _Failure("malformed_response", "reply identity mismatch")
                    return result

    def _retire(self):
        started = time.monotonic_ns()
        process = self._process
        if process is None:
            return 0., True
        # Close poisoned IPC immediately. Keep the process handle until reaped.
        for stream in (process.stdin, process.stdout):
            if stream and not stream.closed:
                stream.close()
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=EVALUATOR_LIMITS.teardown_grace_s)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        try:
            process.wait(timeout=EVALUATOR_LIMITS.teardown_grace_s)
            reaped = True
            self._process = None
        except subprocess.TimeoutExpired:
            # No unlimited join and no new worker while this one remains unreaped.
            reaped = False
        return (time.monotonic_ns() - started) / 1e9, reaped

    def _operate(self, future, operation, payload, request_id, started, deadline):
        code, stage, diagnostic = "ok", "supervision", ""
        residual = worker_elapsed = None
        pid = self._process.pid if self._process is not None else None
        cleanup, reaped = 0., True
        try:
            if operation == "close":
                cleanup, reaped = self._retire()
            else:
                self._check_deadline(deadline)
                if operation == "prepare":
                    cleanup, reaped = self._retire()
                    if not reaped:
                        raise _Failure("cleanup_failed", "old worker has not been reaped")
                    if sys.platform != "linux":
                        raise _Failure("unsupported_platform", "numeric-offline-v1 isolation requires Linux RLIMIT_AS")
                    self._generation = uuid4().hex
                    self._program_id = self._source_sha256 = None
                    if type(payload) is ConstraintProgram:
                        payload = to_dict(payload)
                    if type(payload) is not dict:
                        raise _Failure("invalid_request", "expected a ConstraintProgram or plain dict")
                    # Copy/encode in the supervising thread; callers must retain input
                    # ownership until the Future completes (documented async contract).
                    frame = _encode({"operation": operation, "request_id": request_id,
                                     "generation": self._generation, "program": payload})
                    self._program_id = payload.get("program_id") if type(payload.get("program_id")) is str else None
                    self._source_sha256 = payload.get("source_sha256") if type(payload.get("source_sha256")) is str else None
                    self._check_deadline(deadline)
                    self._spawn()
                else:
                    frame = _encode({"operation": operation, "request_id": request_id,
                                     "generation": self._generation, "inputs": payload})
                pid = self._process.pid
                response = self._exchange(frame, request_id, deadline)
                expected = {"request_id", "generation", "ok", "code", "stage", "diagnostic",
                            "residual", "worker_elapsed_s", "limits"}
                if set(response) != expected or type(response["ok"]) is not bool:
                    raise _Failure("malformed_response", "unexpected response fields")
                if any(type(response[k]) is not str or len(response[k]) > 512 for k in ("code", "stage", "diagnostic")):
                    raise _Failure("malformed_response", "invalid diagnostic fields")
                worker_elapsed = response["worker_elapsed_s"]
                if (type(worker_elapsed) not in (int, float) or not math.isfinite(worker_elapsed)
                        or worker_elapsed < 0 or response["limits"] != {
                            "address_space": [EVALUATOR_LIMITS.address_space_bytes] * 2, "core": [0, 0]}):
                    raise _Failure("malformed_response", "invalid timing or resource-limit evidence")
                if not response["ok"]:
                    if response["residual"] is not None or response["code"] == "ok":
                        raise _Failure("malformed_response", "failure contains a residual or success code")
                    raise _Failure(response["code"], response["diagnostic"], response["stage"])
                residual = response["residual"]
                if response["code"] != "ok" or (operation == "prepare" and residual is not None):
                    raise _Failure("malformed_response", "unexpected successful response")
                if operation == "evaluate":
                    residual = self._normalize_residual(residual)
                self._check_deadline(deadline)
        except _Failure as exc:
            code, stage, diagnostic = exc.code, exc.stage, exc.diagnostic
        except (BrokenPipeError, ConnectionError):
            code, diagnostic = "worker_crash", "worker IPC closed"
        except Exception as exc:
            code, diagnostic = "supervisor_error", type(exc).__name__
        finally:
            if code != "ok":
                residual = None
                try:
                    elapsed, reaped = self._retire()
                    cleanup += elapsed
                except Exception as exc:
                    reaped = False
                    diagnostic += f"; cleanup failed: {type(exc).__name__}"
            if not reaped:
                code, residual = "cleanup_failed", None
            # The completion decision and releasing _active are atomic with
            # close(). Teardown itself runs outside the lock, so callbacks stay
            # responsive even when a child ignores SIGTERM.
            retired_for_close = False
            while True:
                with self._lock:
                    finished = time.monotonic_ns()
                    withdraw = self._closed and operation != "close"
                    late_timeout = code == "ok" and operation != "close" and finished >= deadline
                    if late_timeout:
                        code, residual, diagnostic = "timeout", None, "deadline expired before publication"
                    if withdraw and code == "ok":
                        code, residual, diagnostic = "cancelled", None, "closed before publication"
                    if not ((withdraw or late_timeout) and self._process is not None and not retired_for_close):
                        self._ready = code == "ok" and operation != "close" and not self._closed
                        outcome = self._outcome(
                            operation, request_id, started, deadline, code=code, stage=stage,
                            diagnostic=diagnostic, residual=residual, worker_elapsed=worker_elapsed,
                            cleanup=cleanup, reaped=None if code == "ok" and operation != "close" else reaped,
                            pid=pid, finished=finished)
                        self._active = None
                        break
                try:
                    elapsed, reaped = self._retire()
                    cleanup += elapsed
                except Exception as exc:
                    reaped = False
                    diagnostic += f"; cleanup failed: {type(exc).__name__}"
                retired_for_close = True
                if not reaped:
                    code, residual = "cleanup_failed", None
            future.set_result(outcome)
