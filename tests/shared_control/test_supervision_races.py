"""Deterministic host lifecycle checks; Linux process enforcement is separate."""
from types import SimpleNamespace
import threading

from ssc_core import EVALUATOR_LIMITS, SupervisedEvaluator
from ssc_core import supervision


def prepared_double(monkeypatch):
    """Exercise the real supervisor state machine without OS scheduling noise."""
    evaluator = SupervisedEvaluator()
    evaluator._ready = True
    evaluator._generation = "race-test-generation"
    evaluator._process = SimpleNamespace(pid=12345)
    clock = SimpleNamespace(now=100_000_000)
    monkeypatch.setattr(supervision, "time", SimpleNamespace(monotonic_ns=lambda: clock.now))

    def exchange(frame, request_id, deadline):
        return {"request_id": request_id, "generation": evaluator._generation,
                "ok": True, "code": "ok", "stage": "worker", "diagnostic": "",
                "residual": 5.0, "worker_elapsed_s": 0.001,
                "limits": {"address_space": [EVALUATOR_LIMITS.address_space_bytes] * 2,
                           "core": [0, 0]}}

    retirements = []

    def retire():
        retirements.append(evaluator._process)
        evaluator._process = None
        return 0.0, True

    monkeypatch.setattr(evaluator, "_exchange", exchange)
    monkeypatch.setattr(evaluator, "_retire", retire)
    return evaluator, clock, retirements


def submit(evaluator):
    return evaluator.submit([3, 4, 0], [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [], [], [])


def test_close_during_completion_cannot_orphan_ready_worker(monkeypatch):
    evaluator, _, retirements = prepared_double(monkeypatch)
    constructing, release, close_requested, close_returned = (threading.Event() for _ in range(4))
    original_outcome = evaluator._outcome

    def outcome(operation, *args, **kwargs):
        if operation == "evaluate":
            constructing.set()
            assert release.wait(2)
        return original_outcome(operation, *args, **kwargs)

    monkeypatch.setattr(evaluator, "_outcome", outcome)
    evaluation = submit(evaluator)
    assert constructing.wait(2)
    closing = []

    def close():
        close_requested.set()
        closing.append(evaluator.close())
        close_returned.set()

    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert close_requested.wait(2)
        # Completion is already being committed. close must serialize behind
        # that decision, then own a separate cleanup operation.
        assert not close_returned.wait(0.05)
    finally:
        release.set()
        closer.join(2)
    assert not closer.is_alive()
    assert evaluation.result(2).ok
    assert closing[0] is not evaluation
    assert closing[0].result(2).reaped
    assert retirements and evaluator._process is None and not evaluator.ready


def test_deadline_expiring_after_response_check_discards_scalar(monkeypatch):
    evaluator, clock, retirements = prepared_double(monkeypatch)
    original_check = evaluator._check_deadline
    checks = 0

    def check(deadline):
        nonlocal checks
        original_check(deadline)
        checks += 1
        if checks == 2:  # after the valid response has passed every check
            clock.now = deadline + 1

    monkeypatch.setattr(evaluator, "_check_deadline", check)
    result = submit(evaluator).result(2)
    assert result.code == "timeout" and result.residual is None
    assert result.finished_ns > result.deadline_ns
    assert result.reaped and retirements and evaluator._process is None
    assert not evaluator.ready


def test_close_before_completion_retires_without_holding_callback_lock(monkeypatch):
    evaluator, _, retirements = prepared_double(monkeypatch)
    checked, continue_check, retiring, continue_retirement = (threading.Event() for _ in range(4))
    original_check, original_retire = evaluator._check_deadline, evaluator._retire
    checks = 0

    def check(deadline):
        nonlocal checks
        original_check(deadline)
        checks += 1
        if checks == 2:
            checked.set()
            assert continue_check.wait(2)

    def retire():
        retiring.set()
        assert continue_retirement.wait(2)
        return original_retire()

    monkeypatch.setattr(evaluator, "_check_deadline", check)
    monkeypatch.setattr(evaluator, "_retire", retire)
    evaluation = submit(evaluator)
    try:
        assert checked.wait(2)
        assert evaluator.close() is evaluation
        continue_check.set()
        assert retiring.wait(2)
        # Reading ready and repeating close remain responsive during cleanup.
        assert not evaluator.ready
        assert evaluator.close() is evaluation
        assert not evaluation.done()
    finally:
        continue_check.set()
        continue_retirement.set()
    result = evaluation.result(2)
    assert result.code == "cancelled" and result.residual is None and result.reaped
    assert retirements and evaluator._process is None


def test_thread_creation_failure_does_not_strand_active_future(monkeypatch):
    evaluator, _, retirements = prepared_double(monkeypatch)
    with monkeypatch.context() as patch:
        def fail_start(thread):
            raise RuntimeError("injected host thread exhaustion")
        patch.setattr(threading.Thread, "start", fail_start)
        result = submit(evaluator).result(2)
    assert result.code == "supervisor_error" and result.residual is None
    assert not result.reaped and not evaluator.ready and evaluator._active is None
    assert evaluator.close().result(2).reaped
    assert retirements and evaluator._process is None
