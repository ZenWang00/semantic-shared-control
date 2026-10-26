"""Input validation, real supervised solves, hold semantics and teardown evidence.

Linux tests run the installed solver worker, which spawns real restricted
evaluator workers for every candidate. The host checker is the private
``ssc_core._test_doubles`` double injected through the trusted factory path;
no robot repository is imported. Fault injection replaces only a test
instance's spawn seam; request data cannot select a fault.
"""
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import runpy
import sys
import threading
import time

import pytest

from ssc_core import (
    AppliedCommandReference, HostCheckerSpec, OptimizationInputError, SupervisedOptimizer,
    basic_step_margins, build_request, get_intent_config, get_solver_config, residual_diagnostic, to_dict,
)
from ssc_core._test_doubles import WorkspaceSphereDouble
from ssc_core.host_constraints import MeasuredStateView
from ssc_core.penalties import HostNormalization
from ssc_core.records import ControlResult

HELPER = Path(__file__).resolve().parents[2] / "docs/reference/optimizer_fixtures.py"
F = runpy.run_path(str(HELPER))
scenario, BOTTLE, CUP = F["scenario"], F["BOTTLE"], F["CUP"]
KP_BOTTLE, KP_CUP = F["KP_BOTTLE"], F["KP_CUP"]
distance_program, axis_program, division_program = F["distance_program"], F["axis_program"], F["division_program"]
relaxed_solve_budget, infeasible_start = F["relaxed_solve_budget"], F["infeasible_start"]
CLASSIFICATION_BUDGET_S = F["CLASSIFICATION_BUDGET_S"]
L, A, ORIGIN = F["L"], F["A"], F["ORIGIN"]
DOUBLE = HostCheckerSpec("ssc_core._test_doubles:WorkspaceSphereDouble")
linux = pytest.mark.skipif(sys.platform != "linux", reason="solver isolation acceptance is Linux-only")


def request(case, **kwargs):
    return build_request(case["resolved"], case["weights"], intent_config=case["intent_config"],
                         command_seq=kwargs.pop("command_seq", 5), previous_applied=case["previous"], **kwargs)


def code_of(case, **kwargs):
    with pytest.raises(OptimizationInputError) as error:
        request(case, **kwargs)
    return error.value.code


# ----- pure validation (any platform) -----------------------------------------

def test_request_echoes_identities_and_is_canonical():
    case = scenario()
    first, second = request(case, result_id="r1"), request(case, result_id="r1")
    assert first == second and first.problem_json == second.problem_json
    assert (first.snapshot_id, first.weights_id, first.control_epoch, first.command_seq) == (
        "snapshot-optimizer-fixture", "weights-optimizer-fixture", 0, 5)
    assert (first.source_joystick_seq, first.source_robot_state_seq, first.dt_s) == (100, 100, .2)
    problem = first.problem()
    assert problem["L_m"] == pytest.approx(L) and problem["A_rad"] == pytest.approx(A)
    assert problem["xy_gain"] == pytest.approx(1.) and problem["alpha"] == .5 and problem["smoothing"] == .1
    assert [t["program_id"] for t in problem["terms"]] == sorted(t["program_id"] for t in problem["terms"])
    assert {t["object_id"]: t["object_weight"] for t in problem["terms"]} == {BOTTLE: .75, CUP: .25}
    assert first.previous_reference == "none" and problem["x_prev"] == problem["r_prev"] == [0., 0., 0.]


@pytest.mark.parametrize("kwargs", [{"current": (0., 0.)}, {"enabled": False}])
def test_neutral_or_disabled_input_never_reaches_the_solver(kwargs):
    case = scenario(history=(((1., 0.), True),), previous={"kind": "move"}, **kwargs)
    assert code_of(case) == "input_not_active"


def test_weights_must_be_the_estimator_output_for_this_snapshot():
    case = scenario()
    weights = case["weights"]
    swapped = replace(weights, weights=tuple(replace(w, weight=1 - w.weight) for w in weights.weights))
    assert code_of(dict(case, weights=swapped)) == "weights_mismatch"
    assert code_of(dict(case, weights=replace(weights, assistance_strength=.25))) == "weights_mismatch"
    assert code_of(dict(case, weights=replace(weights, snapshot_id="other"))) == "weights_mismatch"
    other = scenario(current=(-1., 0.))["weights"]
    assert code_of(dict(case, weights=other)) == "weights_mismatch"
    assert code_of(dict(case, intent_config=get_intent_config("intent-linear-alpha0-v1"))).startswith("invalid_input")
    assert code_of(dict(case, intent_config=replace(case["intent_config"], alpha_active=.7))) == "invalid_config"


def test_previous_reference_rules():
    moving = request(scenario(current=(-1., 0.), history=(((1., 0.), True),), previous={"kind": "move"}))
    assert moving.previous_reference == "move"
    assert moving.problem()["x_prev"] == pytest.approx([1., 0., 0.]) and moving.problem()["r_prev"] == [0., 0., 0.]
    turning = request(scenario(previous={"kind": "move", "velocity": (0, 0, 0), "angular": (0, 0, .25)}))
    assert turning.problem()["r_prev"] == pytest.approx([0., 0., .5])
    assert request(scenario(previous={"kind": "hold"})).previous_reference == "hold"
    old = request(scenario(previous={"kind": "move", "epoch": 0}, control_epoch=1))
    assert old.previous_reference == "old_epoch" and old.problem()["x_prev"] == [0., 0., 0.]
    assert code_of(scenario(previous={"kind": "move", "epoch": 2}, control_epoch=1)) == "previous_reference"
    assert code_of(scenario(previous={"kind": "move", "accepted": False})) == "previous_reference"
    assert code_of(scenario(previous={"kind": "move", "session": "other-session"})) == "previous_reference"
    missing = scenario(previous={"kind": "move"})
    assert code_of(dict(missing, previous=None)) == "previous_reference"
    unexpected = scenario()
    assert code_of(dict(unexpected, previous=missing["previous"])) == "previous_reference"
    stray = dict(missing, previous=AppliedCommandReference(missing["previous"].result,
                                                           missing["previous"].acceptance, None))
    assert code_of(stray) == "previous_reference"


def test_context_and_binding_checks():
    case = scenario()
    prepared = {p.program.program_id: to_dict(p.program) for p in case["resolved"].bundle.programs}
    assert request(case, prepared_programs=prepared)
    changed = dict(prepared)
    changed[next(iter(changed))] = dict(changed[next(iter(changed))], description="different")
    assert code_of(case, prepared_programs=changed) == "context_mismatch"
    assert code_of(case, prepared_programs={k: v for k, v in list(prepared.items())[:1]}) == "context_mismatch"
    bundle = case["resolved"].bundle
    rejected = replace(bundle, receipts=tuple(replace(r_, status="rejected") for r_ in bundle.receipts))
    # The estimator recheck or the optimizer's own binding checks reject; never a solve.
    for bad in (rejected, replace(bundle, execution_bindings=bundle.execution_bindings[:1])):
        code = code_of(dict(case, resolved=replace(case["resolved"], bundle=bad)))
        assert code in ("binding_reference", "binding_coverage") or code.startswith("invalid_input_")
    with pytest.raises(ValueError):
        get_solver_config("solver-other")
    assert code_of(case, solver_config=replace(get_solver_config(), maxiter=50)) == "invalid_config"


def test_solve_turns_invalid_or_inactive_input_into_not_run_hold():
    solver = SupervisedOptimizer(DOUBLE)  # validation precedes admission; no process needed
    case = scenario()
    weights = replace(case["weights"], assistance_strength=0.)
    outcome = solver.solve(case["resolved"], weights, intent_config=case["intent_config"],
                           command_seq=2, result_id="result-invalid").result(1)
    result = outcome.result
    assert (result.status, result.solver_status, result.evaluation_valid, result.solve_time_s) == (
        "hold", "not_run", False, 0)
    assert outcome.code == "weights_mismatch" and result.reason.startswith("weights_mismatch")
    assert (result.result_id, result.command_seq, result.snapshot_id) == ("result-invalid", 2, case["resolved"].snapshot.snapshot_id)
    neutral = scenario(current=(0., 0.), history=(((1., 0.), True),), previous={"kind": "move"})
    outcome = solver.solve(neutral["resolved"], neutral["weights"], intent_config=neutral["intent_config"],
                           command_seq=3, previous_applied=neutral["previous"]).result(1)
    assert (outcome.code, outcome.result.status, outcome.result.next_tcp_pose) == ("input_not_active", "hold", None)
    with pytest.raises(TypeError):
        solver.solve(object(), case["weights"], intent_config=case["intent_config"], command_seq=1)
    with pytest.raises(ValueError):
        solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"], command_seq=-1)
    solver.close().result(2)


# ----- real supervised process tests (Linux) ----------------------------------

@pytest.fixture(scope="module")
def default_solver():
    case = scenario()
    solver = SupervisedOptimizer(DOUBLE)
    preparation = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
    assert preparation.ok, preparation
    yield solver, preparation
    assert solver.close().result(3).reaped


def prepared(case, spec=DOUBLE):
    solver = SupervisedOptimizer(spec)
    outcome = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
    assert outcome.ok, outcome
    return solver


def run(solver, case, **kwargs):
    return solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                        command_seq=kwargs.pop("command_seq", 9), previous_applied=case["previous"],
                        **kwargs).result(5)


def gate_equivalent(case, result):
    """Independent recheck as the robot gate would (local double, not ROS2)."""
    state = case["resolved"].robot_state.payload
    config = case["resolved"].bundle.configuration
    u_xy = case["resolved"].joystick.payload.u_xy
    basic = basic_step_margins(result.next_tcp_pose, state.tcp_pose, u_xy, step_m=L, step_rad=A)
    view = MeasuredStateView(state.frame_id, state.tcp_frame, state.tcp_pose, state.joint_names,
                             state.joint_positions_rad)
    host = WorkspaceSphereDouble()(candidate_pose=result.next_tcp_pose, measured_state=view, u_xy=u_xy,
                                   dt_s=result.horizon_s, configuration=config)
    return min(basic.values()) >= -1e-8 and host.feasible


def displacement(result):
    return [a - b for a, b in zip(result.next_tcp_pose.position_xyz, ORIGIN)]


@linux
def test_preparation_lifecycle_limits_and_process_group(default_solver):
    solver, preparation = default_solver
    assert preparation.worker_pid != os.getpid() and os.getpgid(preparation.worker_pid) == preparation.worker_pid
    limits = Path(f"/proc/{preparation.worker_pid}/limits").read_text()
    assert "1073741824" in limits
    assert b"OPENBLAS_NUM_THREADS=1\0" in Path(f"/proc/{preparation.worker_pid}/environ").read_bytes()
    for program_id, pid in preparation.evaluator_pids:
        assert os.getpgid(pid) == preparation.worker_pid
        assert "268435456" in Path(f"/proc/{pid}/limits").read_text()
    assert {"numpy", "scipy"} <= dict(preparation.environment).keys()
    fresh = SupervisedOptimizer(DOUBLE)
    case = scenario()
    assert run(fresh, case).code == "not_prepared"
    assert fresh.prepare(case["resolved"].bundle, motion_disabled=False).result(1).code == "motion_not_disabled"
    assert fresh.close().result(2).reaped
    assert fresh.prepare(case["resolved"].bundle, motion_disabled=True).result(1).code == "closed"


@linux
def test_reversal_proposal_matches_oracle_and_echoes_identity(default_solver):
    solver, _ = default_solver
    case = scenario(current=(-1., 0.), history=(((1., 0.), True),), previous={"kind": "move"})
    outcome = run(solver, case, command_seq=42, result_id="result-reversal")
    result = outcome.result
    assert type(result) is ControlResult and outcome.code == "solved", outcome
    assert (result.status, result.solver_status, result.evaluation_valid) == ("move", "solved", True)
    assert (result.result_id, result.snapshot_id, result.weights_id) == (
        "result-reversal", "snapshot-optimizer-fixture", "weights-optimizer-fixture")
    assert (result.control_epoch, result.command_seq, result.source_joystick_seq,
            result.source_robot_state_seq) == (0, 42, 100, 100)
    assert result.horizon_s == case["resolved"].snapshot.dt_s == .2
    assert math.dist(displacement(result), (-87 * L / 128, 0, 0)) <= 1e-6
    assert abs(math.hypot(*result.next_tcp_pose.quaternion_xyzw) - 1) <= 1e-12
    assert result.next_tcp_pose.quaternion_xyzw[3] >= 0
    assert result.constraints_satisfied is False  # positive residuals are valid intermediate steps
    for evaluation in result.constraint_evaluations:
        expected = residual_diagnostic(evaluation.raw_g, output_unit="m", normalization=HostNormalization("m", L))
        assert evaluation.penalty == expected.penalty and evaluation.normalization_scale == L
    assert {e.program_id for e in result.constraint_evaluations} == {
        "program-bottle-distance-offline-v1", "program-cup-distance-offline-v1"}
    assert gate_equivalent(case, result)
    assert 0 < result.solve_time_s < .15 and outcome.elapsed_s < .15
    report = json.loads(outcome.report_json)
    assert report["scipy_status"] == 0 and report["evaluator_calls"] > 0
    assert outcome.request.previous_reference == "move" and solver.ready


@linux
def test_joystick_only_alpha_zero_and_replay(default_solver):
    case = scenario(alpha_zero=True)
    solver = prepared(case)
    try:
        first, second = run(solver, case), run(solver, case)
        for outcome in (first, second):
            assert outcome.result.status == "move", outcome
            assert math.dist(displacement(outcome.result), (10 * L / 11, 0, 0)) <= 1e-6
            assert json.loads(outcome.report_json)["terms"]["assistance"] == 0
            assert len(outcome.result.constraint_evaluations) == 2  # still evaluated
        assert first.request.problem_json == second.request.problem_json
        assert first.result.next_tcp_pose == second.result.next_tcp_pose
    finally:
        solver.close().result(3)


@linux
def test_positive_residual_approach_returns_unsatisfied_move():
    program = distance_program("program-bottle-approach", BOTTLE, KP_BOTTLE, tolerance=L)
    case = scenario(keypoints=((ORIGIN[0] + 2 * L, 0, .3),), candidates=(BOTTLE,), programs=(program,))
    solver = prepared(case)
    try:
        result = run(solver, case).result
        assert result.status == "move" and result.evaluation_valid and result.constraints_satisfied is False
        assert math.dist(displacement(result), (15 * L / 16, 0, 0)) <= 1e-6
        (evaluation,) = result.constraint_evaluations
        assert evaluation.raw_g == pytest.approx(L / 16, abs=1e-6)
        assert evaluation.penalty == pytest.approx(1 / 256, abs=1e-4)
        assert gate_equivalent(case, result)
    finally:
        solver.close().result(3)


@linux
def test_relation_within_tolerance_has_zero_penalty_and_keeps_raw_residual():
    program = distance_program("program-bottle-near", BOTTLE, KP_BOTTLE, tolerance=L)
    case = scenario(keypoints=((ORIGIN[0] + .002, 0, .3),), candidates=(BOTTLE,), programs=(program,))
    solver = prepared(case)
    try:
        result = run(solver, case).result
        assert result.status == "move" and result.constraints_satisfied is True
        (evaluation,) = result.constraint_evaluations
        assert evaluation.raw_g < 0 and evaluation.penalty == 0
        assert math.dist(displacement(result), (10 * L / 11, 0, 0)) <= 1e-6  # no assistance pull inside tolerance
    finally:
        solver.close().result(3)


@linux
def test_axis_alignment_uses_configured_quaternion_convention():
    axis = {"axis_id": "axis-bottle-approach", "object_id": BOTTLE, "origin_keypoint_id": KP_BOTTLE,
            "unit_direction_xyz": [math.cos(A), math.sin(A), 0.], "directed": True}
    program = axis_program("program-bottle-axis", BOTTLE, "axis-bottle-approach")
    case = scenario(candidates=(BOTTLE,), axes=(axis,), programs=(program,))
    solver = prepared(case)
    try:
        result = run(solver, case).result
        assert result.status == "move", result
        assert math.dist(displacement(result), (10 * L / 11, 0, 0)) <= 1e-6
        x, y, z, w = result.next_tcp_pose.quaternion_xyzw
        assert abs(2 * math.atan2(z, w) - 5 * A / 6) <= 1e-5 and abs(x) + abs(y) <= 1e-6
        assert result.constraint_evaluations[0].raw_g == pytest.approx(A / 6, abs=1e-5)
    finally:
        solver.close().result(3)


@linux
def test_soft_relation_cannot_weaken_host_constraints():
    # Target beyond the workspace face: the solver stops at the host margin.
    outside = distance_program("program-bottle-outside", BOTTLE, KP_BOTTLE)
    case = scenario(tcp=(.495, 0., .3), keypoints=((.53, 0., .3),), candidates=(BOTTLE,), programs=(outside,))
    solver = prepared(case)
    try:
        result = run(solver, case).result
        assert result.status == "move", result
        assert result.next_tcp_pose.position_xyz[0] <= .5 + 1e-8 * .2
        assert result.constraints_satisfied is False and gate_equivalent(case, result)
    finally:
        solver.close().result(3)
    # The excluded sphere bounds the straight path even when the target lies inside it.
    inside = distance_program("program-bottle-sphere", BOTTLE, KP_BOTTLE)
    case = scenario(current=(0., 1.), tcp=(.45, .04, .3), keypoints=((.45, .07, .3),), candidates=(BOTTLE,),
                    programs=(inside,))
    solver = prepared(case)
    try:
        result = run(solver, case).result
        assert result.status == "move", result
        assert math.dist(result.next_tcp_pose.position_xyz, (.45, .07, .3)) >= .0201 - 1e-8 * .2
        assert gate_equivalent(case, result)
    finally:
        solver.close().result(3)


@linux
def test_infeasible_hold_keeps_solver_ready():
    """Classification under the test-only relaxed total budget, so it does not
    depend on host speed (the fixture needs ~177 evaluator calls)."""
    case = scenario(**infeasible_start())
    with relaxed_solve_budget() as config:
        solver = SupervisedOptimizer(DOUBLE, solver_config=config)
        try:
            assert solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10).ok
            outcome = run(solver, case)
            assert outcome.deadline_ns - outcome.started_ns == int(CLASSIFICATION_BUDGET_S * 1e9)
            result = outcome.result
            assert (result.status, result.solver_status) == ("hold", "infeasible"), outcome
            assert result.next_tcp_pose is None and result.horizon_s is None and result.reason
            assert "next_tcp_pose" not in to_dict(result) and "horizon_s" not in to_dict(result)
            assert solver.ready
        finally:
            solver.close().result(3)


@linux
def test_infeasible_start_under_profile_budget_is_a_hold_without_target():
    """Same fixture at the profile's 150 ms budget: infeasible or, on a slower
    host, timeout; either way a hold without target and the matching readiness."""
    case = scenario(**infeasible_start())
    solver = prepared(case)
    try:
        outcome = run(solver, case)
        assert outcome.deadline_ns - outcome.started_ns == 150_000_000
        result = outcome.result
        assert result.status == "hold" and result.solver_status in ("infeasible", "timeout"), outcome
        assert result.next_tcp_pose is None and result.horizon_s is None and result.reason
        assert solver.ready is (result.solver_status == "infeasible")
    finally:
        solver.close().result(3)


@linux
def test_division_error_holds_without_partial_or_previous_target():
    case = scenario(tcp=(.405, 0., .3), previous={"kind": "move"},
                    programs=(division_program("program-bottle-division", BOTTLE),
                              distance_program("program-cup-distance", CUP, KP_CUP)))
    solver = prepared(case)
    try:
        outcome = run(solver, case)
        result = outcome.result
        assert (result.status, result.solver_status, result.evaluation_valid) == ("hold", "error", False)
        assert result.next_tcp_pose is None and result.constraint_evaluations == ()
        assert outcome.code.startswith("evaluator_") and "program-bottle-division" in outcome.diagnostic
        assert json.loads(outcome.evaluator_json)["failure"]["program_id"] == "program-bottle-division"
        assert not solver.ready and outcome.reaped is True
        assert run(solver, case).code == "not_prepared"  # explicit preparation is the recovery path
    finally:
        solver.close().result(3)


@linux
@pytest.mark.parametrize("spec,code", [("ssc_core._test_doubles:RaisingDouble", "host_checker"),
                                       ("ssc_core._test_doubles:NotACheckerResult", "host_checker")])
def test_checker_failures_reject(spec, code):
    case = scenario()
    solver = prepared(case, HostCheckerSpec(spec))
    try:
        outcome = run(solver, case)
        assert (outcome.result.status, outcome.result.solver_status, outcome.code) == ("hold", "error", code)
    finally:
        solver.close().result(3)


@linux
def test_unavailable_checker_fails_preparation():
    solver = SupervisedOptimizer(HostCheckerSpec("ssc_core._missing_module:Checker"))
    outcome = solver.prepare(scenario()["resolved"].bundle, motion_disabled=True).result(10)
    assert not outcome.ok and outcome.code == "checker_unavailable"
    with pytest.raises(ValueError):
        HostCheckerSpec("os.system('x')")
    solver.close().result(2)


@linux
def test_caller_deadline_only_shortens_budget(default_solver):
    solver, _ = default_solver
    case = scenario()
    outcome = run(solver, case, deadline_ns=time.monotonic_ns() + 10**12)
    assert outcome.result.status == "move" and outcome.deadline_ns - outcome.started_ns == 150_000_000
    expired = run(solver, case, deadline_ns=time.monotonic_ns() - 1)
    assert (expired.result.status, expired.result.solver_status) == ("hold", "timeout")
    assert expired.result.next_tcp_pose is None
    assert run(solver, case, deadline_ns=-1).code == "invalid_deadline"


# ----- fault injection at the solver process boundary --------------------------

inject = F["inject"]


def live_members(pgid):
    """Running (non-zombie) processes in a group; orphaned zombies await PID 1."""
    members = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except (OSError, IndexError):
                continue
            if int(fields[2]) == pgid and fields[0] != "Z":
                members.append(int(entry.name))
    return members


def group_gone(pgid, wait_s=1.):
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if not live_members(pgid):
            return True
        time.sleep(.01)
    return False


@linux
@pytest.mark.parametrize("invalid_result", [False, True])
def test_host_only_checker_failure_retires_real_process_group(invalid_result):
    case = scenario()
    solver = prepared(case)
    pgid = solver._process.pid
    def fail(**kwargs):
        if invalid_result:
            return None
        raise RuntimeError("host-only final recheck failure")
    solver._host_checker = fail  # worker still has its valid, independent checker
    try:
        outcome = run(solver, case)
        assert (outcome.result.status, outcome.result.solver_status, outcome.code) == ("hold", "error", "host_checker")
        assert outcome.result.next_tcp_pose is None and outcome.result.horizon_s is None
        assert outcome.reaped is True and not solver.ready and group_gone(pgid)
        assert run(solver, case).code == "not_prepared"
        assert solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10).ok
        assert run(solver, case).result.status == "move"
    finally:
        solver.close().result(3)


@linux
@pytest.mark.parametrize("mode,status,code", [
    ("stall", "timeout", "timeout"), ("late", "timeout", "timeout"), ("crash", "error", "worker_crash"),
    ("wrong_id", "error", "malformed_response"), ("bad_limits", "error", "malformed_response"),
    ("bad_quaternion", "error", "malformed_response"), ("drop_evaluation", "error", "malformed_response"),
    ("lie_pose", "infeasible", "host_recheck"),
    ("evaluator_stall", "timeout", "evaluator_timeout"), ("evaluator_crash", "error", "evaluator_worker_crash"),
])
def test_faults_become_reasoned_holds(tmp_path, monkeypatch, mode, status, code):
    case = scenario()
    solver = SupervisedOptimizer(DOUBLE)
    inject(solver, tmp_path, mode, monkeypatch)
    preparation = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
    assert preparation.ok, preparation
    pgid = preparation.worker_pid
    heartbeat, stop = [], threading.Event()
    def beat():
        while not stop.is_set():
            heartbeat.append(time.monotonic())
            time.sleep(.001)
    thread = threading.Thread(target=beat)
    thread.start()
    submitted = time.monotonic()
    future = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"], command_seq=3)
    returned = time.monotonic()
    outcome = future.result(5)
    stop.set()
    thread.join()
    result = outcome.result
    # An evaluator stall may reach the host's own original deadline first.
    expected = (code, "timeout") if mode == "evaluator_stall" else (code,)
    assert (result.status, result.solver_status) == ("hold", status) and outcome.code in expected, outcome
    assert result.next_tcp_pose is None and result.horizon_s is None and result.constraints_satisfied is None
    assert returned - submitted < .05  # validation only; no wait for computation in the caller
    assert outcome.finished_ns - outcome.started_ns < 150_000_000 + 400_000_000  # budget + bounded teardown
    gaps = [b - a for a, b in zip(heartbeat, heartbeat[1:])]
    assert max(gaps) < .05  # host thread stayed responsive during the stall
    if status in ("timeout", "error"):
        assert not solver.ready and outcome.reaped is True and group_gone(pgid)
    else:
        assert solver.ready
    assert solver.close().result(3).reaped
    assert group_gone(pgid)


@linux
def test_close_withdraws_pending_solve(tmp_path, monkeypatch):
    case = scenario()
    solver = SupervisedOptimizer(DOUBLE)
    inject(solver, tmp_path, "stall", monkeypatch)
    pgid = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10).worker_pid
    future = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"], command_seq=4)
    busy = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"], command_seq=5)
    assert busy.result(1).code == "busy" and busy.result().result.solver_status == "not_run"
    solver.close()
    outcome = future.result(3)
    assert outcome.result.status == "hold" and outcome.code in ("cancelled", "timeout")
    assert group_gone(pgid)


@linux
def test_repeated_failures_do_not_leak_process_groups(tmp_path, monkeypatch):
    case = scenario()
    solver = SupervisedOptimizer(DOUBLE)
    inject(solver, tmp_path, "crash", monkeypatch)
    groups = []
    for _ in range(5):
        preparation = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
        assert preparation.ok
        groups.append(preparation.worker_pid)
        outcome = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                               command_seq=1).result(5)
        assert outcome.code == "worker_crash"
    assert all(group_gone(pgid) for pgid in groups)
    assert solver.close().result(2).reaped
