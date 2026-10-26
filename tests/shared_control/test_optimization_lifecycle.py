"""Deterministic host publication races; worker IPC is the only substituted boundary.

Real Linux worker teardown is also covered in test_optimization.py. These tests
control scheduling/clock boundaries rather than depending on machine speed.
"""
import json
import math
from pathlib import Path
import runpy
import threading
from types import SimpleNamespace

import pytest

from ssc_core import HostCheckerSpec, SupervisedOptimizer, build_request
from ssc_core import optimization
from ssc_core._solver_math import solve_problem
from ssc_core._test_doubles import WorkspaceSphereDouble

F = runpy.run_path(str(Path(__file__).resolve().parents[2] / "docs/reference/optimizer_fixtures.py"))


@pytest.fixture
def host(monkeypatch):
    case = F["scenario"]()
    request = build_request(case["resolved"], case["weights"],
                            intent_config=case["intent_config"], command_seq=1)
    report = solve_problem(
        request.problem(),
        lambda term, p, rotation: math.dist(p, term["keypoints"][0]) - term["params"][0],
        lambda p, q: (["free"], [1.0]), deadline_ns=10**30,
    )
    clock = SimpleNamespace(now=1_000_000_000)
    monkeypatch.setattr(optimization, "time", SimpleNamespace(monotonic_ns=lambda: clock.now))
    monkeypatch.setattr(optimization, "sys", SimpleNamespace(platform="linux"))

    class WorkerBoundary(SupervisedOptimizer):
        def __init__(self):
            super().__init__(HostCheckerSpec("ssc_core._test_doubles:WorkspaceSphereDouble"))
            self.retired = []

        def _spawn(self):
            self._process = SimpleNamespace(pid=12345)

        def _retire(self, **kwargs):
            if self._process is not None:
                self.retired.append(self._process.pid)
                self._process = None
            return 0., True

        def _exchange(self, frame, *args):
            if json.loads(frame)["operation"] == "prepare":
                return {"status": "ready", "evaluator_pids": {
                    p.program.program_id: 12346 + i for i, p in enumerate(case["resolved"].bundle.programs)}}
            return {"status": "solved", "code": "solved", "diagnostic": "", "evaluation_valid": True,
                    "report": report, "worker_elapsed_s": .001}

    solver = WorkerBoundary()
    yield solver, case, clock
    solver.close().result(2)


def prepare(solver, case):
    return solver.prepare(case["resolved"].bundle, motion_disabled=True)


def submit(solver, case):
    return solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"], command_seq=1)


def assert_retired_hold(solver, outcome, status, code):
    result = outcome.result
    assert (result.status, result.solver_status, outcome.code) == ("hold", status, code)
    assert result.next_tcp_pose is None and result.horizon_s is None
    assert outcome.reaped is True and solver.retired == [12345]
    assert not solver.ready and solver._process is None


@pytest.mark.parametrize("invalid_result", [False, True])
def test_host_only_checker_error_retires_before_future_completes(host, invalid_result):
    solver, case, _ = host
    assert prepare(solver, case).result(2).ok
    def fail(**kwargs):
        if invalid_result:
            return None
        raise RuntimeError("host final recheck failed")
    solver._host_checker = fail
    outcome = submit(solver, case).result(2)
    assert_retired_hold(solver, outcome, "error", "host_checker")
    solver._host_checker = WorkspaceSphereDouble()
    assert submit(solver, case).result(2).code == "not_prepared"
    assert prepare(solver, case).result(2).ok
    assert submit(solver, case).result(2).result.status == "move"


class FirstSupervisorUnlock:
    """Schedule one action after the first publication critical section exits."""
    def __init__(self, action):
        self.lock = threading.Lock()
        self.action = action
        self.used = False

    def __enter__(self):
        self.lock.acquire()

    def __exit__(self, *args):
        run = threading.current_thread().name == "ssc-optimizer-supervisor" and not self.used
        if run:
            self.used = True
        self.lock.release()
        if run:
            self.action()


@pytest.mark.parametrize("operation", ["solve", "prepare"])
def test_close_at_publication_boundary_has_one_committed_result(host, operation):
    solver, case, _ = host
    if operation == "solve":
        assert prepare(solver, case).result(2).ok
    closed = []
    solver._lock = FirstSupervisorUnlock(lambda: closed.append(solver.close()))
    future = submit(solver, case) if operation == "solve" else prepare(solver, case)
    outcome = future.result(2)
    closing = closed[0]
    retired = closing.result(2)
    if closing is future:  # close won before the operation committed
        if operation == "solve":
            assert_retired_hold(solver, outcome, "timeout", "cancelled")
            assert not outcome.result.evaluation_valid
        else:
            assert not outcome.ok and outcome.code == "cancelled" and outcome.reaped
    else:  # operation committed first; close owns a separate retirement
        assert retired.reaped
        assert outcome.result.status == "move" if operation == "solve" else outcome.ok
    assert solver._process is None and solver.retired == [12345] and not solver.ready


@pytest.mark.parametrize("operation", ["solve", "prepare"])
def test_close_before_commit_withdraws_operation_without_blocking(host, operation):
    solver, case, _ = host
    if operation == "solve":
        assert prepare(solver, case).result(2).ok
    entered, resume = threading.Event(), threading.Event()
    exchange = solver._exchange
    def paused(*args):
        reply = exchange(*args)
        entered.set()
        assert resume.wait(2)
        return reply
    solver._exchange = paused
    future = submit(solver, case) if operation == "solve" else prepare(solver, case)
    try:
        assert entered.wait(2)
        assert solver.close() is future and not future.done()
    finally:
        resume.set()
    outcome = future.result(2)
    if operation == "solve":
        assert_retired_hold(solver, outcome, "timeout", "cancelled")
        assert not outcome.result.evaluation_valid
    else:
        assert not outcome.ok and outcome.code == "cancelled" and outcome.reaped
        assert solver._process is None and solver.retired == [12345]


def test_deadline_after_last_computation_check_retires_before_publication(host):
    solver, case, clock = host
    assert prepare(solver, case).result(2).ok
    check = solver._check
    calls = 0
    def last_check(deadline):
        nonlocal calls
        check(deadline)
        calls += 1
        if calls == 2:  # computation finished in time, then the supervisor was descheduled
            clock.now = deadline
    solver._check = last_check
    outcome = submit(solver, case).result(2)
    assert_retired_hold(solver, outcome, "timeout", "timeout")
    assert not outcome.result.evaluation_valid
    assert submit(solver, case).result(2).code == "not_prepared"


def test_deadline_at_publication_boundary_never_commits_an_expired_move(host):
    solver, case, clock = host
    assert prepare(solver, case).result(2).ok
    solver._lock = FirstSupervisorUnlock(lambda: setattr(clock, "now", clock.now + 150_000_000))
    outcome = submit(solver, case).result(2)
    if outcome.result.status == "move":
        assert outcome.finished_ns < outcome.deadline_ns  # decision committed before the clock advance
    else:
        assert_retired_hold(solver, outcome, "timeout", "timeout")


def test_preparation_deadline_at_commit_retires_worker(host):
    solver, case, clock = host
    check = solver._check
    calls = 0
    def last_check(deadline):
        nonlocal calls
        check(deadline)
        calls += 1
        if calls == 3:
            clock.now = deadline
    solver._check = last_check
    outcome = prepare(solver, case).result(2)
    assert not outcome.ok and outcome.code == "preparation_deadline" and outcome.reaped
    assert not solver.ready and solver._process is None and solver.retired == [12345]


def test_close_during_error_retirement_is_rechecked_before_commit(host):
    solver, case, _ = host
    assert prepare(solver, case).result(2).ok
    entered, resume = threading.Event(), threading.Event()
    retire = solver._retire
    def paused(**kwargs):
        entered.set()
        assert resume.wait(2)
        return retire(**kwargs)
    solver._retire = paused
    solver._host_checker = lambda **kwargs: None
    future = submit(solver, case)
    try:
        assert entered.wait(2)
        assert solver.close() is future and not future.done()
    finally:
        resume.set()
    outcome = future.result(2)
    assert_retired_hold(solver, outcome, "timeout", "cancelled")
    assert not outcome.result.evaluation_valid


def test_completion_callback_can_query_and_close_without_deadlock(host):
    solver, case, _ = host
    assert prepare(solver, case).result(2).ok
    entered, resume, called = threading.Event(), threading.Event(), threading.Event()
    exchange = solver._exchange
    def paused(*args):
        entered.set()
        assert resume.wait(2)
        return exchange(*args)
    solver._exchange = paused
    future = submit(solver, case)
    closed = []
    def callback(completed):
        assert completed.result().result.status == "move" and solver.ready
        closed.append(solver.close())
        called.set()
    try:
        assert entered.wait(2)
        future.add_done_callback(callback)
    finally:
        resume.set()
    assert called.wait(2)
    assert closed[0] is not future and closed[0].result(2).reaped


def test_failed_retirement_is_not_retried_indefinitely(host):
    solver, case, _ = host
    assert prepare(solver, case).result(2).ok
    calls = []
    retire = solver._retire
    def fail(**kwargs):
        calls.append(1)
        raise RuntimeError("cleanup failure")
    solver._retire = fail
    solver._host_checker = lambda **kwargs: None
    try:
        outcome = submit(solver, case).result(2)
        assert outcome.result.solver_status == "error" and outcome.reaped is False
        assert calls == [1] and not solver.ready
    finally:
        solver._retire = retire
