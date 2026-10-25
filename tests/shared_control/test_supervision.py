"""Installed-wheel Linux enforcement, protocol faults and lifecycle evidence."""
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from ssc_core import EVALUATOR_LIMITS, SupervisedEvaluator

IDENTITY = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
INPUTS = ([3, 4, 0], IDENTITY, [], [], [])
HEADER = "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n"


def artifact(body="    return norm(p_tcp)\n"):
    source = HEADER + body
    return {"program_id": "worker-test", "source_sha256": sha256(source.encode()).hexdigest(),
            "function_api_version": "geometry-residual-v1", "object_id": "object-test",
            "description": "Public synthetic isolation fixture", "entrypoint": "evaluate",
            "source_code": source, "keypoint_bindings": [], "axis_bindings": [],
            "parameters": [], "output_unit": "m"}


@pytest.fixture
def worker():
    instance = SupervisedEvaluator()
    yield instance
    assert instance.close().result(2).reaped


def ready(worker, program=None):
    outcome = worker.prepare(program or artifact(), motion_disabled=True).result(6)
    assert outcome.ok, asdict(outcome)
    assert outcome.worker_pid != os.getpid()
    assert worker.ready
    return outcome


linux = pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS acceptance is Linux-only")


def test_preparation_requires_disabled_motion_and_close_is_final(worker):
    assert worker.submit(*INPUTS).result().code == "not_prepared"
    assert worker.prepare(artifact(), motion_disabled=False).result().code == "motion_not_disabled"
    assert worker.prepare(artifact(), motion_disabled=1).result().code == "motion_not_disabled"
    assert worker.close().result(1).ok
    assert worker.prepare(artifact(), motion_disabled=True).result().code == "closed"


@pytest.mark.skipif(sys.platform == "linux", reason="unsupported-platform development check")
def test_non_linux_fails_preparation(worker):
    outcome = worker.prepare(artifact(), motion_disabled=True).result(6)
    assert not outcome.ok and outcome.code == "unsupported_platform"


@linux
def test_real_worker_scalar_limits_and_independent_process(worker):
    preparation = ready(worker)
    pid = preparation.worker_pid
    assert Path(f"/proc/{pid}").exists()
    limits = Path(f"/proc/{pid}/limits").read_text()
    assert "268435456" in limits and "Max core file size" in limits
    environment = Path(f"/proc/{pid}/environ").read_bytes()
    assert b"OPENBLAS_NUM_THREADS=1\0" in environment
    for _ in range(10):
        outcome = worker.submit(*INPUTS).result(1)
        assert outcome.ok and outcome.residual == 5.0, asdict(outcome)
        assert outcome.deadline_ns - outcome.started_ns == 10_000_000
        assert outcome.worker_generation == preparation.worker_generation
        assert outcome.policy_id == "numeric-offline-v1"
        assert outcome.limits.address_space_bytes == 256 * 1024**2
        assert outcome.worker_elapsed_s >= 0
        assert dict(outcome.environment)["system"] == "Linux"
    assert worker.close().result(1).reaped
    assert not Path(f"/proc/{pid}").exists()


@linux
@pytest.mark.parametrize("body,code", [
    ("    return 1\n" + "#" * 8200, "source_limit"),
    ("".join(f"    a{i} = 1\n" for i in range(65)) + "    return 1\n", "ast_limit"),
    ("    return " + "(" * 40 + "1" + "+1)" * 40 + "\n", "ast_limit"),
    ("    return p_tcp + R_control_tcp\n", "shape_mismatch"),
    ("    import os\n    return 1\n", "forbidden_syntax"),
])
def test_compile_limits_enforced_inside_worker(worker, body, code):
    outcome = worker.prepare(artifact(body), motion_disabled=True).result(6)
    assert not outcome.ok and outcome.code == code, asdict(outcome)
    assert outcome.stage == "compile" and outcome.residual is None and outcome.reaped
    assert not worker.ready
    ready(worker)
    assert worker.submit(*INPUTS).result(1).residual == 5.0


@linux
@pytest.mark.parametrize("inputs,code", [
    (([0] * 257, IDENTITY, [], [], []), "input_shape"),
    (([0, 0, 0], [[1, 0, 0]] * 3, [], [], []), "invalid_rotation"),
    (([True, 0, 0], IDENTITY, [], [], []), "input_type"),
    (([math.nan, 0, 0], IDENTITY, [], [], []), "invalid_request"),
    (([0, 0, 0], IDENTITY, [[0, 0, 0]] * 33, [], []), "input_shape"),
])
def test_invalid_numeric_inputs_fail_without_scalar(worker, inputs, code):
    ready(worker)
    outcome = worker.submit(*inputs).result(1)
    assert not outcome.ok and outcome.code == code and outcome.residual is None, asdict(outcome)
    assert outcome.reaped and not worker.ready


@linux
def test_division_error_has_interpreter_reason(worker):
    ready(worker, artifact("    return 1 / p_tcp[0]\n"))
    outcome = worker.submit([0, 0, 0], IDENTITY, [], [], []).result(1)
    assert outcome.code == "division_by_zero" and outcome.stage == "evaluation"
    assert outcome.residual is None and outcome.reaped


@linux
def test_payload_limit_and_invalid_original_deadline(worker):
    oversized = artifact()
    oversized["description"] = "x" * (EVALUATOR_LIMITS.payload_bytes + 1)
    outcome = worker.prepare(oversized, motion_disabled=True).result(6)
    assert outcome.code == "payload_limit" and outcome.worker_pid is None
    ready(worker)
    assert worker.submit(*INPUTS, deadline_ns=True).result().code == "invalid_deadline"
    expired = time.monotonic_ns() - 1
    outcome = worker.submit(*INPUTS, deadline_ns=expired).result(1)
    assert outcome.code == "timeout" and outcome.deadline_ns == expired and outcome.residual is None
    assert outcome.reaped


# Fault injection is only in trusted tests, outside the generated language/API.
FAULT_SCRIPT = '''
import os, signal, sys, time
from ssc_core import _worker
mode = sys.argv[1]
original = _worker._evaluate
encode = _worker._encode

def evaluate(compiled, inputs):
    if mode in ("stall", "partial", "late"):
        time.sleep(60 if mode != "late" else 0.03)
    if mode == "memory":
        bytearray(512 * 1024 * 1024)
    if mode == "crash":
        os._exit(17)
    if mode == "exception":
        raise RuntimeError("injected")
    return original(compiled, inputs)

def reply(data):
    if data["residual"] is not None:
        if mode == "wrong_id":
            data["request_id"] = "old-request"
        if mode == "wrong_generation":
            data["generation"] = "retired-worker"
        if mode == "bad_scalar":
            data["residual"] = True
        if mode == "bad_limits":
            data["limits"]["address_space"] = [0, 0]
        if mode == "bad_json":
            return b"not json\\n"
        if mode == "large_reply":
            return b"x" * (1024 * 1024 + 2)
        if mode == "partial_reply":
            return b'{"request_id":'
        if mode == "duplicate_reply":
            return encode(data) * 2
    return encode(data)

if mode in ("stall", "late"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == "preparation_stall":
    time.sleep(60)
_worker._evaluate = evaluate
_worker._encode = reply
_worker.main()
'''


def inject(worker, tmp_path, mode, monkeypatch):
    script = tmp_path / "fault_worker.py"
    script.write_text(FAULT_SCRIPT)
    original_popen = subprocess.Popen
    # Replace only this instance's spawning seam. Source data cannot pick a command.
    def spawn():
        worker._process = original_popen([sys.executable, "-I", str(script), mode],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, bufsize=0)
        os.set_blocking(worker._process.stdin.fileno(), False)
        os.set_blocking(worker._process.stdout.fileno(), False)
    monkeypatch.setattr(worker, "_spawn", spawn)


@linux
@pytest.mark.parametrize("mode,code", [
    ("memory", "memory_limit"), ("crash", "worker_crash"), ("exception", "worker_exception"),
    ("wrong_id", "malformed_response"), ("wrong_generation", "malformed_response"),
    ("bad_scalar", "malformed_response"), ("bad_limits", "malformed_response"),
    ("bad_json", "malformed_response"), ("large_reply", "malformed_response"),
    ("partial_reply", "timeout"), ("duplicate_reply", "malformed_response"),
])
def test_faults_cannot_be_success(worker, tmp_path, monkeypatch, mode, code):
    inject(worker, tmp_path, mode, monkeypatch)
    prepared = ready(worker)
    outcome = worker.submit(*INPUTS).result(1)
    assert outcome.code == code and not outcome.ok and outcome.residual is None, asdict(outcome)
    assert outcome.reaped and not Path(f"/proc/{prepared.worker_pid}").exists()
    assert worker.submit(*INPUTS).result().code == "not_prepared"


@linux
@pytest.mark.parametrize("mode", ["stall", "late"])
def test_timeout_heartbeat_recovery_and_retired_reply(worker, tmp_path, monkeypatch, mode):
    inject(worker, tmp_path, mode, monkeypatch)
    first = ready(worker)
    beats = []
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(.001):
            beats.append(time.monotonic_ns())
    thread = threading.Thread(target=heartbeat)
    thread.start()
    try:
        start = time.monotonic_ns()
        future = worker.submit(*INPUTS)
        returned = time.monotonic_ns()
        assert worker.submit(*INPUTS).result().code == "busy"
        outcome = future.result(1)
    finally:
        stop.set()
        thread.join(1)
    assert outcome.code == "timeout" and outcome.residual is None, asdict(outcome)
    assert outcome.reaped and outcome.elapsed_s < .25 and outcome.cleanup_s < .15
    assert returned - start < 10_000_000
    assert len(beats) >= 2
    assert not Path(f"/proc/{first.worker_pid}").exists()
    monkeypatch.undo()
    second = ready(worker)
    assert second.worker_generation != first.worker_generation
    value = worker.submit(*INPUTS).result(1)
    assert value.ok and value.residual == 5.0 and value.request_id != outcome.request_id


@linux
def test_close_cancels_and_reaps_pending_work(worker, tmp_path, monkeypatch):
    inject(worker, tmp_path, "stall", monkeypatch)
    prepared = ready(worker)
    future = worker.submit(*INPUTS)
    closed = worker.close()
    assert closed is future
    outcome = closed.result(1)
    assert outcome.code == "cancelled" and outcome.residual is None and outcome.reaped
    assert not Path(f"/proc/{prepared.worker_pid}").exists()


@linux
def test_repeated_failures_do_not_leak_processes_or_handles(worker, tmp_path, monkeypatch):
    baseline = len(list(Path("/proc/self/fd").iterdir()))
    inject(worker, tmp_path, "crash", monkeypatch)
    for _ in range(20):
        prepared = ready(worker)
        outcome = worker.submit(*INPUTS).result(1)
        assert outcome.code == "worker_crash" and outcome.reaped
        assert not Path(f"/proc/{prepared.worker_pid}").exists()
    assert len(list(Path("/proc/self/fd").iterdir())) <= baseline


@linux
def test_preparation_timeout_is_bounded(worker, tmp_path, monkeypatch):
    inject(worker, tmp_path, "preparation_stall", monkeypatch)
    outcome = worker.prepare(artifact(), motion_disabled=True).result(6)
    assert outcome.code == "timeout" and outcome.reaped and outcome.elapsed_s < 5.25
