"""Supervised optimizer evidence from the installed shared wheel (Linux).

Runs the profile's one-step oracles and boundary cases through real solver and
evaluator processes, measures solve latency and program-count scaling, injects
stall/late/crash faults at the trusted spawn seam, and replays every emitted
residual in a fresh isolated process. The host checker is the private local
double ``ssc_core._test_doubles:WorkspaceSphereDouble``, not synthetic-host-v1.
Evidence level: synthetic offline component software; no gate, plant, robot,
real-time guarantee or research-benefit claim.

    python -B docs/reference/verify_optimizer.py --output optimizer-evidence.json \
        --source-revision TESTED_GIT_SHA --wheel-sha256 TESTED_WHEEL_SHA256
"""
import argparse
import contextlib
from dataclasses import asdict
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import runpy
import statistics
import subprocess
import sys
import tempfile
import time

from ssc_core import (
    NUMERIC_OFFLINE_V1, HostCheckerSpec, SupervisedOptimizer, basic_step_margins, compile_program,
    get_solver_config, to_dict,
)
from ssc_core._test_doubles import WorkspaceSphereDouble
from ssc_core.host_constraints import MeasuredStateView

F = runpy.run_path(str(Path(__file__).with_name("optimizer_fixtures.py")))
scenario, BOTTLE, CUP, KP_BOTTLE, KP_CUP = F["scenario"], F["BOTTLE"], F["CUP"], F["KP_BOTTLE"], F["KP_CUP"]
distance_program, axis_program, division_program = F["distance_program"], F["axis_program"], F["division_program"]
relaxed_solve_budget, infeasible_start = F["relaxed_solve_budget"], F["infeasible_start"]
CLASSIFICATION_BUDGET_S = F["CLASSIFICATION_BUDGET_S"]
L, A, ORIGIN = F["L"], F["A"], F["ORIGIN"]
CHECKER = HostCheckerSpec("ssc_core._test_doubles:WorkspaceSphereDouble")
POSITION_ATOL_M, ANGLE_ATOL_RAD, REPLAY_TOL = 1e-6, 1e-5, 1e-9
AXIS = {"axis_id": "axis-bottle-approach", "object_id": BOTTLE, "origin_keypoint_id": KP_BOTTLE,
        "unit_direction_xyz": [math.cos(A), math.sin(A), 0.], "directed": True}

# name, scenario kwargs, expectation (status/solver_status plus optional oracles), matrix rows
CASES = (
    ("joystick-only-alpha0", {"alpha_zero": True},
     {"status": "move", "translation": (10 * L / 11, 0, 0), "assistance": 0.}, ["G01"]),
    ("positive-approach", {"keypoints": ((ORIGIN[0] + 2 * L, 0, .3),), "candidates": (BOTTLE,),
                           "programs": (distance_program("program-bottle-approach", BOTTLE, KP_BOTTLE, tolerance=L),)},
     {"status": "move", "translation": (15 * L / 16, 0, 0), "raw_g": (L / 16,), "penalty": (1 / 256,),
      "satisfied": False}, ["G01"]),
    ("axis-alignment", {"candidates": (BOTTLE,), "axes": (AXIS,),
                        "programs": (axis_program("program-bottle-axis", BOTTLE, AXIS["axis_id"]),)},
     {"status": "move", "translation": (10 * L / 11, 0, 0), "rotation": (0, 0, 5 * A / 6), "raw_g": (A / 6,),
      "satisfied": False}, ["G01"]),
    ("reversal", {"current": (-1., 0.), "history": (((1., 0.), True),), "previous": {"kind": "move"}},
     {"status": "move", "translation": (-87 * L / 128, 0, 0), "weights": (5 / 16, 11 / 16)}, ["G01"]),
    ("within-tolerance", {"keypoints": ((ORIGIN[0] + .002, 0, .3),), "candidates": (BOTTLE,),
                          "programs": (distance_program("program-bottle-near", BOTTLE, KP_BOTTLE, tolerance=L),)},
     {"status": "move", "translation": (10 * L / 11, 0, 0), "penalty": (0.,), "satisfied": True}, ["G02"]),
    ("workspace-face-favoured", {"tcp": (.495, 0., .3), "keypoints": ((.53, 0., .3),), "candidates": (BOTTLE,),
                                 "programs": (distance_program("program-bottle-outside", BOTTLE, KP_BOTTLE),)},
     {"status": "move", "max_x": .5, "satisfied": False}, ["C08"]),
    ("excluded-sphere-favoured", {"current": (0., 1.), "tcp": (.45, .04, .3), "keypoints": ((.45, .07, .3),),
                                  "candidates": (BOTTLE,),
                                  "programs": (distance_program("program-bottle-sphere", BOTTLE, KP_BOTTLE),)},
     {"status": "move", "sphere_clearance": .0201, "satisfied": False}, ["C08"]),
    # Classification under the test-only relaxed total budget (PR #29 review F1);
    # the same fixture at the profile budget follows and may also time out.
    ("infeasible-host", infeasible_start(),
     {"status": "hold", "solver_status": "infeasible", "test_solve_budget_s": CLASSIFICATION_BUDGET_S}, ["G07"]),
    ("infeasible-host-profile-budget", infeasible_start(),
     {"status": "hold", "solver_status_in": ["infeasible", "timeout"]}, ["G07"]),
    ("division-error", {"tcp": (.405, 0., .3), "previous": {"kind": "move"},
                        "programs": (division_program("program-bottle-division", BOTTLE),
                                     distance_program("program-cup-distance", CUP, KP_CUP))},
     {"status": "hold", "solver_status": "error", "evaluation_valid": False}, ["G06"]),
    ("neutral-input", {"current": (0., 0.), "history": (((1., 0.), True),), "previous": {"kind": "move"}},
     {"status": "hold", "solver_status": "not_run", "code": "input_not_active"}, ["G07"]),
    ("disabled-input", {"enabled": False, "history": (((1., 0.), True),), "previous": {"kind": "move"}},
     {"status": "hold", "solver_status": "not_run", "code": "input_not_active"}, ["G07"]),
)
FAULTS = (("solver-stall", "stall"), ("late-reply", "late"), ("evaluator-stall", "evaluator_stall"),
          ("solver-crash", "crash"), ("worker-misreports-pose", "lie_pose"))


def gate_equivalent(case, result):
    state, config = case["resolved"].robot_state.payload, case["resolved"].bundle.configuration
    u_xy = case["resolved"].joystick.payload.u_xy
    basic = basic_step_margins(result.next_tcp_pose, state.tcp_pose, u_xy, step_m=L, step_rad=A)
    view = MeasuredStateView(state.frame_id, state.tcp_frame, state.tcp_pose, state.joint_names,
                             state.joint_positions_rad)
    host = WorkspaceSphereDouble()(candidate_pose=result.next_tcp_pose, measured_state=view, u_xy=u_xy,
                                   dt_s=result.horizon_s, configuration=config)
    return {"basic_min": min(basic.values()), "host_feasible": host.feasible,
            "ok": min(basic.values()) >= -NUMERIC_OFFLINE_V1.hard_margin_tolerance and host.feasible}


def rotation_vector(quaternion):
    x, y, z, w = quaternion
    s = math.sqrt(x * x + y * y + z * z)
    return (0., 0., 0.) if s == 0 else tuple(2 * math.atan2(s, w) * v / s for v in (x, y, z))


def summarize(outcome):
    result = outcome.result
    report = json.loads(outcome.report_json) if outcome.report_json else None
    return {"result": to_dict(result), "code": outcome.code, "diagnostic": outcome.diagnostic,
            "elapsed_s": outcome.elapsed_s, "worker_elapsed_s": outcome.worker_elapsed_s,
            "cleanup_s": outcome.cleanup_s, "reaped": outcome.reaped, "report": report,
            "evaluator": json.loads(outcome.evaluator_json) if outcome.evaluator_json else None,
            "host_margins": outcome.host_margins,
            "request_problem": json.loads(outcome.request.problem_json) if outcome.request else None}


def judge(case, outcome, expect):
    result, checks = outcome.result, {}
    checks["status"] = result.status == expect["status"]
    if "solver_status" in expect:
        checks["solver_status"] = result.solver_status == expect["solver_status"]
    if "solver_status_in" in expect:
        checks["solver_status_in"] = result.solver_status in expect["solver_status_in"]
    if "code" in expect:
        checks["code"] = outcome.code == expect["code"]
    if "evaluation_valid" in expect:
        checks["evaluation_valid"] = result.evaluation_valid is expect["evaluation_valid"]
    if result.status == "hold":
        checks["no_target"] = result.next_tcp_pose is None and result.horizon_s is None
        return checks
    delta = [a - b for a, b in zip(result.next_tcp_pose.position_xyz, case["resolved"].robot_state.payload.tcp_pose.position_xyz)]
    if "translation" in expect:
        checks["translation"] = math.dist(delta, expect["translation"]) <= POSITION_ATOL_M
    rotation = rotation_vector(result.next_tcp_pose.quaternion_xyzw)
    checks["rotation"] = math.dist(rotation, expect.get("rotation", (0, 0, 0))) <= ANGLE_ATOL_RAD
    if "raw_g" in expect:
        checks["raw_g"] = all(abs(e.raw_g - g) <= POSITION_ATOL_M * 10 for e, g in zip(result.constraint_evaluations, expect["raw_g"]))
    if "penalty" in expect:
        checks["penalty"] = all(abs(e.penalty - p) <= 1e-4 for e, p in zip(result.constraint_evaluations, expect["penalty"]))
    if "satisfied" in expect:
        checks["satisfied"] = result.constraints_satisfied is expect["satisfied"]
    if "assistance" in expect:
        checks["assistance"] = json.loads(outcome.report_json)["terms"]["assistance"] == expect["assistance"]
    if "weights" in expect:
        checks["weights"] = all(abs(w.weight - e) <= 1e-12 for w, e in zip(case["weights"].weights, expect["weights"]))
    if "max_x" in expect:
        checks["max_x"] = result.next_tcp_pose.position_xyz[0] <= expect["max_x"] + 1e-8 * .2
    if "sphere_clearance" in expect:
        checks["sphere"] = math.dist(result.next_tcp_pose.position_xyz, (.45, .07, .3)) >= expect["sphere_clearance"] - 1e-8 * .2
    checks["identity_echo"] = (result.snapshot_id == case["resolved"].snapshot.snapshot_id and
                               result.weights_id == case["weights"].weights_id and
                               result.horizon_s == case["resolved"].snapshot.dt_s)
    checks["gate_equivalent"] = gate_equivalent(case, result)["ok"]
    return checks


def run_cases():
    records = []
    for name, kwargs, expect, rows in CASES:
        case = scenario(**kwargs)
        budget = expect.get("test_solve_budget_s")
        with relaxed_solve_budget(budget) if budget else contextlib.nullcontext(get_solver_config()) as config:
            solver = SupervisedOptimizer(CHECKER, solver_config=config)
            try:
                preparation = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
                outcome = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                                       command_seq=7, previous_applied=case["previous"],
                                       result_id=f"result-{name}").result(5)
            finally:
                closed = solver.close().result(3)
        checks = judge(case, outcome, expect)
        records.append({"name": name, "matrix_rows": rows, "scenario": {k: repr(v) for k, v in kwargs.items()},
                        "solve_budget_s": config.solve_budget_s,
                        "budget_kind": "test-only relaxed total budget" if budget else "profile solver-slsqp-v1",
                        "expected": {k: list(v) if isinstance(v, tuple) else v for k, v in expect.items()},
                        "preparation": {"ok": preparation.ok, "code": preparation.code,
                                        "evaluator_pids": len(preparation.evaluator_pids),
                                        "environment": dict(preparation.environment)},
                        "outcome": summarize(outcome), "checks": checks, "closed_reaped": closed.reaped,
                        "programs": [to_dict(p.program) for p in case["resolved"].bundle.programs],
                        "ok": all(checks.values()) and closed.reaped is True})
    return records


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def latency(samples=100):
    case = scenario(current=(-1., 0.), history=(((1., 0.), True),), previous={"kind": "move"})
    solver = SupervisedOptimizer(CHECKER)
    solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
    elapsed, calls, statuses = [], [], {}
    try:
        for index in range(samples):
            outcome = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                                   command_seq=index, previous_applied=case["previous"]).result(5)
            statuses[outcome.code] = statuses.get(outcome.code, 0) + 1
            elapsed.append(outcome.elapsed_s)
            if outcome.report_json:
                calls.append(json.loads(outcome.report_json)["evaluator_calls"])
            if not solver.ready:
                solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
    finally:
        solver.close().result(3)
    return {"fixture": "reversal (2 distance programs)", "samples": samples, "codes": statuses,
            "elapsed_ms": {"min": min(elapsed) * 1e3, "median": statistics.median(elapsed) * 1e3,
                           "p95": percentile(elapsed, .95) * 1e3, "max": max(elapsed) * 1e3},
            "evaluator_calls": {"min": min(calls), "max": max(calls)} if calls else None,
            "over_budget": sum(e >= .15 for e in elapsed)}


def scaling(samples=20):
    rows = []
    for count in (2, 4, 8, 16):
        programs = tuple(distance_program(f"program-{obj}-{i:02d}", obj, kp, tolerance=i * 1e-3)
                         for i in range(count // 2) for obj, kp in ((BOTTLE, KP_BOTTLE), (CUP, KP_CUP)))
        case = scenario(current=(-1., 0.), history=(((1., 0.), True),), previous={"kind": "move"}, programs=programs)
        solver = SupervisedOptimizer(CHECKER)
        prep = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
        elapsed, codes = [], {}
        try:
            for index in range(samples):
                if not solver.ready:
                    solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
                outcome = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                                       command_seq=index, previous_applied=case["previous"]).result(5)
                codes[outcome.code] = codes.get(outcome.code, 0) + 1
                elapsed.append(outcome.elapsed_s)
        finally:
            solver.close().result(3)
        rows.append({"programs": count, "preparation_ms": (prep.finished_ns - prep.started_ns) / 1e6,
                     "codes": codes, "median_ms": statistics.median(elapsed) * 1e3,
                     "p95_ms": percentile(elapsed, .95) * 1e3, "max_ms": max(elapsed) * 1e3})
    return rows


def faults():
    records = []
    with tempfile.TemporaryDirectory() as directory:
        for name, mode in FAULTS:
            case = scenario()
            solver = SupervisedOptimizer(CHECKER)
            F["inject"](solver, directory, mode)
            preparation = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
            submitted = time.monotonic_ns()
            future = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"], command_seq=1)
            returned = time.monotonic_ns()
            outcome = future.result(5)
            closed = solver.close().result(3)
            result = outcome.result
            expected = {"stall": "timeout", "late": "timeout", "evaluator_stall": "timeout",
                        "crash": "error", "lie_pose": "infeasible"}[mode]
            records.append({"name": name, "mode": mode, "preparation_ok": preparation.ok,
                            "status": result.status, "solver_status": result.solver_status, "code": outcome.code,
                            "submit_return_ms": (returned - submitted) / 1e6,
                            "decision_to_completion_ms": outcome.elapsed_s * 1e3,
                            "deadline_ms": (outcome.deadline_ns - outcome.started_ns) / 1e6,
                            "cleanup_s": outcome.cleanup_s, "reaped": outcome.reaped, "closed_reaped": closed.reaped,
                            "ok": (result.status == "hold" and result.solver_status == expected and
                                   result.next_tcp_pose is None and closed.reaped is True)})
    return records


def replay_payload(records):
    return [{"name": r["name"], "programs": r["programs"], "problem": r["outcome"]["request_problem"],
             "pose": r["outcome"]["result"].get("next_tcp_pose"),
             "raw_g": [e["raw_g"] for e in r["outcome"]["result"]["constraint_evaluations"]]}
            for r in records if r["outcome"]["result"]["status"] == "move"]


def replay_child(path):
    """Fresh isolated process: recompile programs and re-evaluate residuals at each emitted pose."""
    from scipy.spatial.transform import Rotation
    output = []
    for item in json.loads(Path(path).read_text()):
        programs = {p["program_id"]: compile_program(p, policy=NUMERIC_OFFLINE_V1) for p in item["programs"]}
        rotation = Rotation.from_quat(item["pose"]["quaternion_xyzw"]).as_matrix().tolist()
        values = [programs[t["program_id"]].evaluate(item["pose"]["position_xyz"], rotation, t["keypoints"],
                                                     t["axis_dirs"], t["params"]) for t in item["problem"]["terms"]]
        rebuilt = None
        for name, kwargs, _, _ in CASES:
            if name == item["name"]:
                case = scenario(**kwargs)
                from ssc_core import build_request
                rebuilt = build_request(case["resolved"], case["weights"], intent_config=case["intent_config"],
                                        command_seq=7, previous_applied=case["previous"],
                                        result_id=f"result-{name}").problem()
        output.append({"name": item["name"], "raw_g": values, "problem_equal": rebuilt == item["problem"]})
    print(json.dumps(output))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--wheel-sha256")
    parser.add_argument("--replay-child", type=Path)
    args = parser.parse_args()
    if args.replay_child:
        replay_child(args.replay_child)
        return
    if not all((args.output, args.source_revision, args.wheel_sha256)):
        parser.error("--output, --source-revision and --wheel-sha256 are required")
    if sys.platform != "linux":
        parser.error("solver isolation evidence requires Linux")
    cases = run_cases()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(replay_payload(cases), handle)
    child = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--replay-child", handle.name],
                           check=True, capture_output=True, text=True)
    Path(handle.name).unlink()
    replayed, replay = json.loads(child.stdout), []
    archived = {item["name"]: item for item in replay_payload(cases)}
    for item in replayed:
        original = archived[item["name"]]["raw_g"]
        equal = all(math.isclose(a, b, rel_tol=REPLAY_TOL, abs_tol=REPLAY_TOL) for a, b in zip(original, item["raw_g"]))
        replay.append({"name": item["name"], "archived_raw_g": original, "replayed_raw_g": item["raw_g"],
                       "residuals_within_tolerance": equal and len(original) == len(item["raw_g"]),
                       "problem_rebuilt_identically": item["problem_equal"]})
    fault_records, timing, scale = faults(), latency(), scaling()
    ok = (all(c["ok"] for c in cases) and all(f["ok"] for f in fault_records) and
          all(r["residuals_within_tolerance"] and r["problem_rebuilt_identically"] for r in replay))
    report = {
        "source_revision": args.source_revision, "wheel_sha256": args.wheel_sha256,
        "package_version": importlib.metadata.version("ssc-core"),
        "environment": {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version,
                        "numpy": importlib.metadata.version("numpy"), "scipy": importlib.metadata.version("scipy")},
        "profile_id": "offline-reference-v1", "solver_config": asdict(get_solver_config()),
        "policy": asdict(NUMERIC_OFFLINE_V1), "checker": CHECKER.factory,
        "tolerances": {"position_m": POSITION_ATOL_M, "angle_rad": ANGLE_ATOL_RAD, "replay_atol_rtol": REPLAY_TOL},
        "cases": cases, "faults": fault_records, "fresh_process_replay": replay,
        "latency": timing, "program_count_scaling": scale,
        "evidence_level": ("synthetic offline component software on real Linux solver/evaluator processes; "
                           "local checker double; no gate, plant, robot integration, real-time or benefit claim"),
        "ok": ok,
    }
    args.output.write_text(json.dumps(report, indent=1, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"ok": ok, "cases": len(cases), "faults": len(fault_records), "replayed": len(replay),
                      "latency_ms": timing["elapsed_ms"], "scaling": [(r["programs"], r["median_ms"], r["codes"]) for r in scale]}))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
