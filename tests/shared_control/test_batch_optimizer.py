"""Candidate batches preserve numeric oracles and fail as a complete unit."""
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import runpy
import signal
import sys
import time
from copy import deepcopy

import pytest

from ssc_core import HostCheckerSpec, SupervisedOptimizer, get_solver_config, to_dict
from ssc_core._batch_evaluator import BatchEvaluator
from ssc_core.supervision import _Failure

F = runpy.run_path(str(Path(__file__).resolve().parents[2] / "docs/reference/optimizer_fixtures.py"))
CONFIG = get_solver_config("solver-slsqp-batch-v2")
CHECKER = HostCheckerSpec("ssc_core._test_doubles:WorkspaceSphereDouble")
IDENTITY = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
linux = pytest.mark.skipif(sys.platform != "linux", reason="batch isolation requires Linux")


def inputs(case):
    from ssc_core import build_request
    return build_request(case["resolved"], case["weights"], intent_config=case["intent_config"],
                         previous_applied=case["previous"], command_seq=1, solver_config=CONFIG).problem()


def test_batch_is_opt_in_and_v1_configuration_content_is_unchanged():
    old = get_solver_config()
    assert old.solver_config_id == "solver-slsqp-v1"
    assert asdict(CONFIG) == dict(asdict(old), solver_config_id="solver-slsqp-batch-v2")
    assert CONFIG.max_programs == 16 and CONFIG.solve_budget_s == .15


@pytest.mark.parametrize("old_id,new_id,session_id", [
    ("intent-linear-v1", "intent-linear-batch-v2", "config-offline-batch-v2"),
    ("intent-linear-alpha0-v1", "intent-linear-batch-alpha0-v2", "config-offline-batch-alpha0-v2"),
])
def test_batch_intent_configuration_only_rebinds_immutable_identities(old_id, new_id, session_id):
    from ssc_core import get_intent_config
    assert asdict(get_intent_config(new_id)) == dict(asdict(get_intent_config(old_id)),
        estimator_config_id=new_id, session_config_id=session_id)


@pytest.mark.parametrize("value", [None, [], [0.], [0., 1., 2.], [0., True], [0., math.nan]])
def test_partial_or_nonfinite_batch_replies_are_rejected(value):
    batch = BatchEvaluator()
    batch._count = 2
    with pytest.raises(_Failure):
        batch._normalize_residual(value)


def test_batch_checked_inputs_match_scalar_and_refresh_changed_bindings():
    from ssc_core._batch_worker import _compile
    from ssc_core.interpreter import InterpreterError
    case = F["scenario"]()
    problem = inputs(case)
    programs = [to_dict(p.program) for p in sorted(case["resolved"].bundle.programs,
                                                 key=lambda p: p.program.program_id)]
    batch = _compile({"programs": programs})
    bindings = [{key: t[key] for key in ("program_id", "keypoints", "axis_dirs", "params")}
                for t in problem["terms"]]
    for x in (.4, .401, .41):
        position = [x, 0., .3]
        expected = [p.evaluate(position, IDENTITY, b["keypoints"], b["axis_dirs"], b["params"])
                    for p, b in zip(batch.compiled, bindings)]
        assert batch.evaluate((position, IDENTITY, bindings, None)) == expected
    old = batch.evaluate((problem["p_tcp"], IDENTITY, bindings, None))
    changed = deepcopy(bindings)
    changed[0]["keypoints"][0][0] += .01
    assert batch.evaluate((problem["p_tcp"], IDENTITY, changed, None)) != old
    assert batch.evaluate((problem["p_tcp"], IDENTITY, bindings, None)) == old
    # Reuse only within one explicitly bound solve, never across solve IDs.
    assert batch.evaluate((problem["p_tcp"], IDENTITY, bindings, "solve-a")) == old
    assert batch.evaluate((problem["p_tcp"], IDENTITY, None, "solve-a")) == old
    with pytest.raises(ValueError, match="solve scope"):
        batch.evaluate((problem["p_tcp"], IDENTITY, None, "solve-b"))
    updated = batch.evaluate((problem["p_tcp"], IDENTITY, changed, "solve-b"))
    assert updated != old
    assert batch.evaluate((problem["p_tcp"], IDENTITY, None, "solve-b")) == updated
    with pytest.raises(ValueError, match="solve scope"):
        batch.evaluate((problem["p_tcp"], IDENTITY, None, "solve-a"))
    # bool equals 0/1 in Python, but must never alias accepted numeric inputs.
    malformed = deepcopy(bindings)
    malformed[0]["keypoints"][0][1] = False
    with pytest.raises(InterpreterError, match="input_type"):
        batch.evaluate((problem["p_tcp"], IDENTITY, malformed, None))
    with pytest.raises(InterpreterError, match="invalid_rotation"):
        batch.evaluate((problem["p_tcp"], [[0., 0., 0.]] * 3, bindings, None))
    with pytest.raises(ValueError, match="identity/order"):
        batch.evaluate((problem["p_tcp"], IDENTITY, list(reversed(bindings)), None))


@linux
def test_one_isolated_process_evaluates_all_programs_and_rejects_wrong_order():
    case = F["scenario"]()
    problem = inputs(case)
    programs = [to_dict(p.program) for p in sorted(case["resolved"].bundle.programs,
                                                 key=lambda p: p.program.program_id)]
    batch = BatchEvaluator()
    try:
        prep = batch.prepare(programs, motion_disabled=True).result(6)
        assert prep.ok and prep.worker_pid != os.getpid()
        assert "268435456" in Path(f"/proc/{prep.worker_pid}/limits").read_text()
        result = batch.submit_batch(problem["p_tcp"], IDENTITY, problem["terms"],
                                    deadline_ns=time.monotonic_ns() + 100_000_000).result(1)
        assert result.ok and result.residual == pytest.approx((.01, .01))
        assert result.deadline_ns - result.started_ns == 10_000_000
        failed = batch.submit_batch(problem["p_tcp"], IDENTITY, list(reversed(problem["terms"])),
                                    deadline_ns=time.monotonic_ns() + 100_000_000).result(1)
        assert not failed.ok and failed.residual is None and failed.reaped
        assert not batch.ready
    finally:
        assert batch.close().result(2).reaped


@linux
def test_last_program_failure_discards_earlier_valid_values():
    case = F["scenario"]()
    problem = inputs(case)
    programs = [to_dict(p.program) for p in sorted(case["resolved"].bundle.programs,
                                                 key=lambda p: p.program.program_id)]
    source = "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n    return 1 / (p_tcp[0] - 0.4)\n"
    programs[-1].update(source_code=source, source_sha256=sha256(source.encode()).hexdigest())
    batch = BatchEvaluator()
    try:
        assert batch.prepare(programs, motion_disabled=True).result(6).ok
        result = batch.submit_batch(problem["p_tcp"], IDENTITY, problem["terms"],
                                    deadline_ns=time.monotonic_ns() + 100_000_000).result(1)
        assert result.code == "division_by_zero" and result.residual is None and result.reaped
        assert programs[-1]["program_id"] in result.diagnostic
    finally:
        batch.close().result(2)


@linux
@pytest.mark.parametrize("shorten", [False, True])
def test_batch_stall_retires_worker_under_original_or_shortened_deadline(shorten):
    case = F["scenario"]()
    problem = inputs(case)
    programs = [to_dict(p.program) for p in sorted(case["resolved"].bundle.programs,
                                                 key=lambda p: p.program.program_id)]
    batch = BatchEvaluator()
    try:
        prep = batch.prepare(programs, motion_disabled=True).result(6)
        assert prep.ok
        os.kill(prep.worker_pid, signal.SIGSTOP)
        deadline = time.monotonic_ns() + (2_000_000 if shorten else 100_000_000)
        result = batch.submit_batch(problem["p_tcp"], IDENTITY, problem["terms"], deadline_ns=deadline).result(1)
        assert result.code == "timeout" and result.residual is None and result.reaped
        assert result.deadline_ns <= deadline and result.deadline_ns - result.started_ns <= 10_000_000
        assert not batch.ready and not Path(f"/proc/{prep.worker_pid}").exists()
    finally:
        batch.close().result(2)


@linux
@pytest.mark.parametrize("variant", ["normal", "reversal", "positive", "infeasible"])
def test_batch_solver_keeps_oracles_and_infeasible_hold(variant):
    kwargs, expected = {}, 25 * .01 / 32
    if variant == "reversal":
        kwargs = dict(current=(-1., 0.), history=(((1., 0.), True),), previous={"kind": "move"})
        expected = -87 * .01 / 128
    elif variant == "positive":
        kwargs = dict(keypoints=((.42, 0., .3),), candidates=(F["BOTTLE"],),
            programs=(F["distance_program"]("program-positive", F["BOTTLE"], F["KP_BOTTLE"], tolerance=.01),))
        expected = 15 * .01 / 16
    elif variant == "infeasible":
        kwargs = F["infeasible_start"]()
    case = F["scenario"](**kwargs)
    solver = SupervisedOptimizer(CHECKER, solver_config=CONFIG)
    try:
        prep = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(6)
        assert prep.ok and len(set(pid for _, pid in prep.evaluator_pids)) == 1
        out = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                           command_seq=1, previous_applied=case["previous"]).result(3)
        assert out.elapsed_s < CONFIG.solve_budget_s
        if variant == "infeasible":
            assert out.result.status == "hold" and out.result.solver_status == "infeasible"
            assert out.result.next_tcp_pose is None and out.result.horizon_s is None
        else:
            assert out.result.status == "move", out.diagnostic
            assert out.result.next_tcp_pose.position_xyz[0] - .4 == pytest.approx(expected, abs=1e-6)
            stats = json.loads(out.evaluator_json)["stats"]
            assert stats["calls"] == stats["batch_calls"] * len(case["resolved"].bundle.programs)
    finally:
        assert solver.close().result(3).reaped


@linux
@pytest.mark.parametrize("mode", ["solver-stall", "batch-stall", "short-deadline", "domain", "alpha0-domain"])
def test_batch_solver_failure_has_no_target_retires_and_requires_prepare(mode):
    kwargs = {}
    if "domain" in mode:
        kwargs = dict(tcp=(.405, 0., .3), alpha_zero=mode == "alpha0-domain",
                      programs=(F["division_program"]("program-bottle-division", F["BOTTLE"]),
                                F["distance_program"]("program-cup-distance", F["CUP"], F["KP_CUP"])))
    case = F["scenario"](**kwargs)
    solver = SupervisedOptimizer(CHECKER, solver_config=CONFIG)
    try:
        prep = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(6)
        assert prep.ok
        if mode == "solver-stall":
            os.kill(prep.worker_pid, signal.SIGSTOP)
        elif mode == "batch-stall":
            os.kill(prep.evaluator_pids[0][1], signal.SIGSTOP)
        deadline = time.monotonic_ns() + (1_000_000 if mode == "short-deadline" else 200_000_000)
        out = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                           command_seq=1, previous_applied=case["previous"], deadline_ns=deadline).result(3)
        expected = "error" if "domain" in mode else "timeout"
        assert out.result.solver_status == expected, out.diagnostic
        assert out.result.status == "hold" and out.result.next_tcp_pose is None and out.result.horizon_s is None
        assert not out.result.constraint_evaluations and not out.result.evaluation_valid
        assert out.reaped and not solver.ready
        assert solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                            command_seq=2).result(1).result.solver_status == "not_run"
        recovered = F["scenario"]()
        assert solver.prepare(recovered["resolved"].bundle, motion_disabled=True).result(6).ok
        good = solver.solve(recovered["resolved"], recovered["weights"], intent_config=recovered["intent_config"],
                            command_seq=3).result(3)
        assert good.result.status == "move", good.diagnostic
    finally:
        assert solver.close().result(3).reaped
