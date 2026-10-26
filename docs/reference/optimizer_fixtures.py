"""Synthetic resolved optimizer inputs (bottle-cup-offline-v1 variants).

Receipts here are compatibility-only metadata: none was issued by a live host
validator and none authorizes activation. Weights come from the real
intent-linear-v1 estimator for the exact snapshot. Each call builds a fresh
session registry; this is a test/evidence fixture, not an archive.
"""

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

from ssc_core import (
    NUMERIC_OFFLINE_V1, AppliedCommandReference, SessionRegistry, estimate_intent, get_intent_config,
    parse_message,
)
from ssc_core.records import CommandAcceptanceRecord, TimePoint

ROOT = Path(__file__).resolve().parents[2]
BOTTLE, CUP = "object-bottle-offline-v1", "object-cup-offline-v1"
KP_BOTTLE, KP_CUP = "kp-bottle-approach-offline-v1", "kp-cup-approach-offline-v1"
ORIGIN = (.4, 0., .3)
L, A = .01, .1
HEADER = "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n"
DISTANCE = HEADER + "    distance = norm(p_tcp - keypoints[0])\n    return distance - params[0]\n"
AXIS = HEADER + "    return acos(clip(dot(R_control_tcp[:, 0], axis_dirs[0]), -1, 1)) - params[0]\n"
DIVISION = HEADER + "    return params[0] / (p_tcp[0] - params[1])\n"


def distance_program(name, obj, keypoint, tolerance=0., scale=L, weight=1.):
    return {"program_id": name, "object_id": obj, "source": DISTANCE, "keypoints": [keypoint], "axes": [],
            "parameters": [("distance_tolerance", tolerance, "m")], "output_unit": "m",
            "scale": scale, "weight": weight}


def axis_program(name, obj, axis, tolerance=0., scale=A, weight=1.):
    return {"program_id": name, "object_id": obj, "source": AXIS, "keypoints": [], "axes": [axis],
            "parameters": [("angle_tolerance", tolerance, "rad")], "output_unit": "rad",
            "scale": scale, "weight": weight}


def division_program(name, obj, numerator=.01, pole=.405):
    return {"program_id": name, "object_id": obj, "source": DIVISION, "keypoints": [], "axes": [],
            "parameters": [("numerator", numerator, "m"), ("pole_x", pole, "m")],
            "output_unit": "dimensionless", "scale": 1., "weight": 1.}


DEFAULT_PROGRAMS = (distance_program("program-bottle-distance-offline-v1", BOTTLE, KP_BOTTLE),
                    distance_program("program-cup-distance-offline-v1", CUP, KP_CUP))


# Test-only total solve budget (PR #29 review F1, owner decision 26 October 2026).
# The infeasible-start fixture needs ~177 evaluator calls, close to the profile's
# 150 ms budget, so its *classification* check runs with the same solver-slsqp-v1
# content and a longer real-wall budget. This is never a profile configuration;
# the per-call 10 ms evaluator budget is unchanged, and profile-budget behaviour
# of the same fixture is checked separately (a hold without target).
CLASSIFICATION_BUDGET_S = 1.0


@contextmanager
def relaxed_solve_budget(seconds=CLASSIFICATION_BUDGET_S):
    """Yield a test-only SolverConfig accepted while the context is active."""
    import ssc_core.optimization as optimization

    original = optimization.get_solver_config
    profile = original()
    relaxed = replace(profile, solve_budget_s=seconds)

    def lookup(solver_config_id="solver-slsqp-v1"):
        return relaxed if solver_config_id == profile.solver_config_id else original(solver_config_id)

    optimization.get_solver_config = lookup
    try:
        yield relaxed
    finally:
        optimization.get_solver_config = original


def infeasible_start():
    """TCP outside the workspace face; every step is hard-infeasible."""
    return {"tcp": (.505, 0., .3), "keypoints": ((.51, 0., .3),), "candidates": (BOTTLE,),
            "programs": (distance_program("program-bottle", BOTTLE, KP_BOTTLE),)}


def _stamp(ns):
    sec, nano = divmod(ns, 1_000_000_000)
    return {"clock_id": "synthetic-clock-offline-v1", "sec": sec, "nanosec": nano}


def scenario(*, current=(1., 0.), enabled=True, history=(), relevance=(.5, .5), alpha_zero=False,
             tcp=ORIGIN, quaternion=(0., 0., 0., 1.), keypoints=None, axes=(), programs=DEFAULT_PROGRAMS,
             candidates=(BOTTLE, CUP), previous=None, control_epoch=0):
    """Return dict(resolved, weights, intent_config, previous, registry, session_id).

    previous: None, or dict(kind="move"|"hold", epoch=int, velocity=(vx, vy, vz) m/s,
    angular=(wx, wy, wz) rad/s, accepted=True, session=None) for the accepted command.
    """
    catalog = json.loads((ROOT / "tests/fixtures/shared_control/bottle_cup_offline_v1.json").read_text())
    messages = {item["message_type"]: item for item in catalog["messages"]}
    geometry, semantic = messages["SceneGeometry"]["payload"], messages["SemanticContext"]["payload"]
    config = messages["SessionConfig"]["payload"]
    if alpha_zero:
        config["config_id"] = "config-offline-alpha0-v1"
    if keypoints:
        for point, position in zip(geometry["keypoints"], keypoints):
            point["position_xyz"] = list(position)
    geometry["axes"] = [dict(axis) for axis in axes]
    geometry["objects"] = [obj for obj in geometry["objects"] if obj["object_id"] in candidates]
    geometry["keypoints"] = [kp for kp in geometry["keypoints"] if kp["object_id"] in candidates]
    semantic["object_relevance"] = [
        {"object_id": obj, "task_relevance": score} for obj, score in zip((BOTTLE, CUP), relevance) if obj in candidates]
    session_id = messages["SessionConfig"]["meta"]["session_id"]
    records = []
    for index, spec in enumerate(programs):
        source = spec["source"]
        program = {"program_id": spec["program_id"], "source_sha256": sha256(source.encode()).hexdigest(),
                   "function_api_version": "geometry-residual-v1", "object_id": spec["object_id"],
                   "description": "Synthetic optimizer fixture relation.", "entrypoint": "evaluate",
                   "source_code": source, "keypoint_bindings": list(spec["keypoints"]),
                   "axis_bindings": list(spec["axes"]),
                   "parameters": [{"name": n, "value": v, "unit": u} for n, v, u in spec["parameters"]],
                   "output_unit": spec["output_unit"]}
        receipt_id, binding_id = f"compatibility-only-optimizer-{index}", f"binding-optimizer-{index}"
        receipt = {"receipt_id": receipt_id, "program_id": program["program_id"],
                   "source_sha256": program["source_sha256"], "function_api_version": "geometry-residual-v1",
                   "scene_id": geometry["scene_id"], "feature_set_id": geometry["feature_set_id"],
                   "validation_geometry_id": geometry["geometry_id"],
                   "validation_geometry_revision": geometry["revision"],
                   "validator_policy_version": NUMERIC_OFFLINE_V1.policy_id, "status": "accepted", "diagnostics": []}
        binding = {"binding_id": binding_id, "program_id": program["program_id"], "config_id": config["config_id"],
                   "normalization_scale": spec["scale"], "weight": spec["weight"], "validation_receipt_id": receipt_id}
        records.append((program, receipt, binding))
    program_refs = [{"program_id": p["program_id"], "source_sha256": p["source_sha256"]} for p, _, _ in records]
    binding_ids = [b["binding_id"] for _, _, b in records]
    semantic["program_refs"] = program_refs
    registry = SessionRegistry(session_id, policy=NUMERIC_OFFLINE_V1)
    for message_data in messages.values():
        registry.register_message(message_data)
    for program, receipt, binding in records:
        registry.register_record("ConstraintProgram", program, session_id=session_id)
        registry.register_record("ProgramValidationReceipt", receipt, session_id=session_id)
        registry.register_record("ProgramExecutionBinding", binding, session_id=session_id)

    def message(kind, payload, identifier, seq, ns=10_000_000_000):
        return registry.register_message({"message_type": kind, "payload": payload,
            "meta": {"schema_version": "1.0", "session_id": session_id, "message_id": identifier,
                     "source_id": "synthetic-optimizer-input", "sequence": seq, "produced_at": _stamp(ns)}})

    def state(identifier, seq, position, quat, ns):
        return message("RobotState", {"sampled_at": _stamp(ns), "frame_id": config["control_frame"],
            "tcp_frame": config["tcp_frame"], "tcp_pose": {"position_xyz": list(position), "quaternion_xyzw": list(quat)},
            "joint_names": catalog["initial_joint_names"], "joint_positions_rad": catalog["initial_joint_positions_rad"],
            "state_source": "fk_from_measured_joints"}, identifier, seq, ns)

    task = messages["TaskContext"]["payload"]
    base = {"config_id": config["config_id"], "task_ref": {"task_id": task["task_id"], "revision": task["revision"]},
            "dt_s": .2, "geometry_ref": {"geometry_id": geometry["geometry_id"], "revision": geometry["revision"]},
            "semantic_id": semantic["semantic_id"], "program_refs": program_refs,
            "execution_binding_refs": binding_ids}
    applied_id, reference = None, None
    if previous is not None:
        message("JoystickSample", {"sampled_at": _stamp(9_800_000_000), "mapping_id": "synthetic-intent-mapping",
                "u_xy": [1, 0], "enabled": True}, "previous-input", 1, 9_800_000_000)
        state("previous-state", 1, tcp, quaternion, 9_800_000_000)
        epoch = previous.get("epoch", control_epoch)
        registry.register_record("ControlSnapshot", dict(base, snapshot_id="snapshot-previous", snapshot_seq=1,
            control_epoch=epoch, joystick_ref="previous-input", robot_state_ref="previous-state",
            joystick_history_refs=[], previous_applied_command_id=None), session_id=session_id)
        source = registry.resolve_snapshot("snapshot-previous")
        velocity, angular = previous.get("velocity", (.05, 0, 0)), previous.get("angular", (0, 0, 0))
        from scipy.spatial.transform import Rotation
        target_q = (Rotation.from_rotvec([w * .2 for w in angular]) * Rotation.from_quat(quaternion)).as_quat()
        target_q = [float(-v if target_q[3] < 0 else v) for v in target_q]
        payload = {"result_id": "result-previous", "snapshot_id": "snapshot-previous", "weights_id": "weights-previous",
                   "control_epoch": epoch, "command_seq": 1, "source_joystick_seq": 1, "source_robot_state_seq": 1,
                   "evaluation_valid": True, "solver_status": "solved", "constraints_satisfied": False,
                   "solve_time_s": .01, "constraint_evaluations": []}
        if previous.get("kind", "move") == "move":
            payload.update(status="move", next_tcp_pose={"position_xyz": [float(p + v * .2) for p, v in zip(tcp, velocity)],
                                                         "quaternion_xyzw": target_q}, horizon_s=.2)
        else:
            payload.update(status="hold", evaluation_valid=False, solver_status="not_run", solve_time_s=0,
                           constraints_satisfied=None, reason="local hold")
        result_message = parse_message({"message_type": "ControlResult", "payload": payload,
            "meta": {"schema_version": "1.0", "session_id": previous.get("session", session_id),
                     "message_id": "previous-result", "source_id": "synthetic-optimizer", "sequence": 1,
                     "produced_at": _stamp(9_900_000_000)}}, policy=NUMERIC_OFFLINE_V1)
        applied_id = "applied-previous"
        acceptance = CommandAcceptanceRecord("result-previous", previous.get("accepted", True), "accepted",
                                             TimePoint(**_stamp(9_900_000_000)),
                                             applied_id if previous.get("accepted", True) else None)
        reference = AppliedCommandReference(result_message, acceptance, source)
    for index, (vector, on) in enumerate(history):
        ns = 10_000_000_000 - (len(history) - index) * 20_000_000
        message("JoystickSample", {"sampled_at": _stamp(ns), "mapping_id": "synthetic-intent-mapping",
                "u_xy": list(vector), "enabled": on}, f"history-{index}", 10 + index, ns)
    message("JoystickSample", {"sampled_at": _stamp(10_000_000_000), "mapping_id": "synthetic-intent-mapping",
            "u_xy": list(current), "enabled": enabled}, "current-input", 100)
    state("measured-state", 100, tcp, quaternion, 10_000_000_000)
    registry.register_record("ControlSnapshot", dict(base, snapshot_id="snapshot-optimizer-fixture", snapshot_seq=2,
        control_epoch=control_epoch, joystick_ref="current-input", robot_state_ref="measured-state",
        joystick_history_refs=[f"history-{index}" for index in range(len(history))],
        previous_applied_command_id=applied_id), session_id=session_id)
    resolved = registry.resolve_snapshot("snapshot-optimizer-fixture")
    intent = get_intent_config("intent-linear-alpha0-v1" if alpha_zero else "intent-linear-v1")
    outcome = estimate_intent(resolved, intent, weights_id="weights-optimizer-fixture", policy=NUMERIC_OFFLINE_V1)
    return {"resolved": resolved, "weights": outcome.weights, "disposition": outcome.disposition,
            "intent_config": intent, "previous": reference, "registry": registry, "session_id": session_id}


# Trusted fault injection for tests and the evidence harness only.
SOLVER_FAULT = '''
import os, signal, sys, time
from ssc_core import _solver_worker, supervision
mode, evaluator_script = sys.argv[1], sys.argv[2]
original_solve, encode = _solver_worker._solve, _solver_worker._encode

def solve(problem, evaluate, host_margins, deadline_ns):
    if mode == "stall":
        time.sleep(60)
    if mode == "late":
        time.sleep(max(0, (deadline_ns - time.monotonic_ns()) / 1e9) + .02)
    if mode == "crash":
        os._exit(17)
    report = original_solve(problem, evaluate, host_margins, deadline_ns)
    if mode == "lie_pose":
        report["position_xyz"][0] = .6  # outside the workspace; host must recheck
    if mode == "bad_quaternion":
        report["quaternion_xyzw"] = [0, 0, 0, 2]
    if mode == "drop_evaluation":
        report["evaluations"] = report["evaluations"][:1]
    return report

def reply(data):
    if data.get("operation") == "solve":
        if mode == "wrong_id":
            data["request_id"] = "old"
        if mode == "bad_limits":
            data["limits"] = {"address_space": [0, 0], "core": [0, 0]}
    return encode(data)

def new_evaluator():
    evaluator = supervision.SupervisedEvaluator()
    if mode.startswith("evaluator_"):
        def spawn():
            import subprocess
            evaluator._process = subprocess.Popen(
                [sys.executable, "-I", evaluator_script, mode[len("evaluator_"):]],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            os.set_blocking(evaluator._process.stdin.fileno(), False)
            os.set_blocking(evaluator._process.stdout.fileno(), False)
        evaluator._spawn = spawn
    return evaluator

if mode in ("stall", "late"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
_solver_worker._solve = solve
_solver_worker._encode = reply
_solver_worker._new_evaluator = new_evaluator
_solver_worker.main()
'''

EVALUATOR_FAULT = '''
import os, signal, sys, time
from ssc_core import _worker
mode, original = sys.argv[1], _worker._evaluate
def evaluate(compiled, inputs):
    if mode == "stall":
        time.sleep(60)
    if mode == "crash":
        os._exit(9)
    return original(compiled, inputs)
if mode == "stall":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
_worker._evaluate = evaluate
_worker.main()
'''


def inject(solver, directory, mode, monkeypatch=None):
    """Replace one trusted instance's spawn seam; request data never selects a fault."""
    solver_script, evaluator_script = Path(directory) / "solver_fault.py", Path(directory) / "evaluator_fault.py"
    solver_script.write_text(SOLVER_FAULT)
    evaluator_script.write_text(EVALUATOR_FAULT)
    original_popen = subprocess.Popen
    def spawn():
        environment = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
        solver._process = original_popen([sys.executable, "-I", str(solver_script), mode, str(evaluator_script)],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         bufsize=0, env=environment, start_new_session=True)
        os.set_blocking(solver._process.stdin.fileno(), False)
        os.set_blocking(solver._process.stdout.fileno(), False)
    if monkeypatch is not None:
        monkeypatch.setattr(solver, "_spawn", spawn)
    else:
        solver._spawn = spawn
