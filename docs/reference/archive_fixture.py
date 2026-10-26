"""Build a fixed evidence fixture; no ROS2 imports or live controller operations.

Proposals, decisions, and the single observation are explicitly scripted fixture
data, not optimizer/gate/plant integration evidence. Host program validation and
intent fusion are real. Numerical expectations use an analytical distance oracle.
"""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
import sys

from ssc_core import (
    EVALUATOR_LIMITS, NUMERIC_OFFLINE_V1, HostBindingPolicy, HostNormalization,
    HostProgramValidator, SessionRegistry, UnitReview, estimate_intent,
    feature_definition_digest, get_intent_config, get_solver_config,
    program_content_digest, to_dict,
)
from ssc_core.archive import ArchiveBuilder, environment

ROOT = Path(__file__).resolve().parents[2]
ROS2_REVISION = "4ea685cdc46ca6857462c5e25b0c8d53a1154db2"
ROS2_WHEEL = "444056500e64b5d91e357d5b8aa9756b1fe6e966b57253e5f01b72cc1b39dcf9"


def stamp(ns):
    seconds, nanos = divmod(ns, 1_000_000_000)
    return {"clock_id": "synthetic-clock-offline-v1", "sec": seconds, "nanosec": nanos}


def build_fixture(*, source_revision, wheel_sha256):
    env = environment()
    manifest = {"run_id": "archive-fixed-fixture-v1", "session_id": "session-archive-fixture-v1",
        "schema_version": "1.0", "function_api_version": "geometry-residual-v1",
        "profile_id": "offline-reference-v1", "evaluator_policy_id": "numeric-offline-v1",
        "solver_config_id": "solver-slsqp-v1", "installation_id": "fixture-installation-v1",
        "scenario_id": "archive-scenario-v1", "environment": env,
        "software": {"server": {"revision": source_revision, "package_version": env["packages"]["ssc-core"],
                                 "wheel_sha256": wheel_sha256},
                     "ros2": {"revision": ROS2_REVISION, "package_version": "0.5.0", "wheel_sha256": ROS2_WHEEL}},
        "evidence_scope": "Synthetic fixed archive fixture: real host program validation and intent fusion; "
                          "analytical proposals and scripted decisions/observation. ROS2 revision/wheel is the "
                          "recorded consumer baseline, not an executed version-pair integration. No hardware evidence.",
        "replay_tolerances": {"atol": 1e-9, "rtol": 1e-9}}
    writer = ArchiveBuilder(manifest)
    registry = SessionRegistry(manifest["session_id"], policy=NUMERIC_OFFLINE_V1)

    def resource(identifier, value):
        writer.add_resource(identifier, json.dumps(value, sort_keys=True, allow_nan=False).encode(),
                            media_type="application/json")

    def record(kind, value):
        parsed = registry.register_record(kind, value, session_id=registry.session_id)
        writer.add_record(kind, parsed)
        return parsed

    def message(kind, value, identifier, sequence, ns):
        data = {"message_type": kind, "payload": value, "meta": {
            "schema_version": "1.0", "session_id": registry.session_id, "message_id": identifier,
            "source_id": "archive-fixture-" + kind, "sequence": sequence, "produced_at": stamp(ns)}}
        writer.add_message(data)
        return registry.register_message(data)

    profile = (ROOT / "docs/wiki/Offline-Reference-Profile.md").read_bytes()
    for name in ("offline-reference-v1", "synthetic-rotary-v1", "synthetic-host-v1"):
        writer.add_resource(name, profile, media_type="text/markdown; charset=utf-8")
    resource("numeric-offline-v1", {"validation": asdict(NUMERIC_OFFLINE_V1), "execution": asdict(EVALUATOR_LIMITS)})
    resource("solver-slsqp-v1", asdict(get_solver_config()))
    resource("intent-linear-v1", asdict(get_intent_config("intent-linear-v1")))
    resource("fixture-installation-v1", {
        "command": "python -m pip install ssc_core wheel with test extra",
        "freeze": subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True),
        "server": manifest["software"]["server"], "ros2_reference_only": manifest["software"]["ros2"]})
    resource("archive-scenario-v1", {
        "scenario": "three dispatched snapshots: accepted analytic move, solved but rejected move, timeout hold; local release",
        "ticks_ns": [10_000_000_000, 10_200_000_000, 10_400_000_000],
        "joystick_xy": [1, 0], "target_x": [.405, .410],
        "proposals_and_decisions": "scripted fixtures, not optimizer/controller execution",
        "observation": "one explicitly scripted sample; absent later observations remain absent"})
    resource("archive-input-mapping-v1", {"axes": "base XY", "dead_zone": 0, "source": "scripted normalized values"})

    catalog = json.loads((ROOT / "tests/fixtures/shared_control/bottle_cup_offline_v1.json").read_bytes())
    messages = {entry["message_type"]: entry for entry in catalog["messages"]}
    observation = messages["SceneObservation"]["payload"]
    observation["observation_id"] = "archive-observation-v1"
    for name, identifier, shape, content in (("rgb_ref", "archive-rgb-v1", [1, 1, 3], bytes([0, 0, 0])),
                                            ("depth_ref", "archive-depth-v1", [1, 1], bytes([0, 0, 0, 0]))):
        observation[name]["asset_id"] = identifier
        observation[name]["shape"] = shape
        writer.add_resource(identifier, content, media_type=observation[name]["media_type"])
    observation["calibration_id"] = "archive-calibration-v1"
    resource("archive-calibration-v1", {"scope": "synthetic metadata only; no capture adapter",
        "K": observation["K"], "projection_model": observation["projection_model"],
        "transform": observation["T_control_camera"]})
    for kind in ("SceneGeometry", "SemanticContext"):
        messages[kind]["payload"]["observation_id"] = observation["observation_id"]
    for kind, original in messages.items():
        message(kind, original["payload"], "archive-" + kind, original["meta"]["sequence"], 10_000_000_000)
    config = messages["SessionConfig"]["payload"]
    geometry = messages["SceneGeometry"]["payload"]
    semantic = messages["SemanticContext"]["payload"]
    task = messages["TaskContext"]["payload"]
    programs = [record("ConstraintProgram", p) for p in catalog["constraint_programs"]]
    reviews = tuple(UnitReview("archive-unit-review-" + str(i), program_content_digest(p),
        geometry["scene_id"], geometry["feature_set_id"], feature_definition_digest(geometry),
        p.output_unit, tuple(x.unit for x in p.parameters), "archive fixture formula review",
        "norm(TCP - approach point) - distance tolerance: metres; synthetic point identities from profile")
        for i, p in enumerate(programs))
    authority = HostProgramValidator(registry, unit_reviews=reviews,
        binding_policies=tuple(HostBindingPolicy(config["config_id"], p.program_id, HostNormalization("m", .01, 1.))
                               for p in programs))
    bindings = []
    for i, program in enumerate(programs):
        report = authority.validate(program, geometry=geometry, config_id=config["config_id"], motion_disabled=True)
        if report.receipt.status != "accepted":
            raise ValueError(report.receipt.diagnostics)
        writer.add_record("ProgramValidationReceipt", report.receipt)
        resource("validation-evidence-" + str(i), asdict(report))
        binding = authority.create_binding("archive-binding-" + str(i), receipt_id=report.receipt.receipt_id,
            geometry_ref={"geometry_id": geometry["geometry_id"], "revision": geometry["revision"]}, config_id=config["config_id"])
        bindings.append(binding)
        writer.add_record("ProgramExecutionBinding", binding)

    for i, ns in enumerate((10_000_000_000, 10_200_000_000, 10_400_000_000)):
        joystick = message("JoystickSample", {"sampled_at": stamp(ns - 5_000_000),
            "mapping_id": "archive-input-mapping-v1", "u_xy": [1, 0], "enabled": True}, f"joystick-{i}", i, ns)
        state = message("RobotState", {"sampled_at": stamp(ns - 2_000_000),
            "frame_id": config["control_frame"], "tcp_frame": config["tcp_frame"],
            "tcp_pose": catalog["initial_pose"], "joint_names": catalog["initial_joint_names"],
            "joint_positions_rad": catalog["initial_joint_positions_rad"],
            "state_source": "fk_from_measured_joints"}, f"state-{i}", i, ns)
        snapshot = record("ControlSnapshot", {"snapshot_id": f"snapshot-{i}", "snapshot_seq": i,
            "control_epoch": 1, "config_id": config["config_id"], "task_ref": {"task_id": task["task_id"], "revision": task["revision"]},
            "dt_s": .2, "joystick_ref": joystick.meta.message_id, "robot_state_ref": state.meta.message_id,
            "joystick_history_refs": [f"joystick-{j}" for j in range(i)],
            "geometry_ref": {"geometry_id": geometry["geometry_id"], "revision": geometry["revision"]},
            "semantic_id": semantic["semantic_id"], "program_refs": semantic["program_refs"],
            "execution_binding_refs": [b.binding_id for b in bindings],
            "previous_applied_command_id": "applied-0" if i else None})
        resolved = registry.resolve_snapshot(snapshot.snapshot_id)
        weight = estimate_intent(resolved, get_intent_config("intent-linear-v1"), weights_id=f"weights-{i}", policy=NUMERIC_OFFLINE_V1).weights
        record("ObjectWeights", weight)
        input_messages = [joystick, state, *resolved.joystick_history]
        writer.add_event(f"dispatch-{i}", "dispatch", {"snapshot_id": snapshot.snapshot_id, "command_seq": i + 1,
            "created_at": stamp(ns), "dispatched_at": stamp(ns + 1_000_000),
            "expires_at": stamp(ns + 200_000_000), "solve_deadline": stamp(ns + 151_000_000),
            "inputs": [{"message_id": m.meta.message_id, "received_at": to_dict(m.meta.produced_at),
                        "source_age_ns": ns - (m.payload.sampled_at.sec * 1_000_000_000 + m.payload.sampled_at.nanosec)}
                       for m in input_messages]})
        proposal = {"result_id": f"result-{i}", "snapshot_id": snapshot.snapshot_id, "weights_id": weight.weights_id,
            "control_epoch": 1, "command_seq": i + 1, "source_joystick_seq": i, "source_robot_state_seq": i,
            "status": "move", "evaluation_valid": True, "solver_status": "solved", "constraints_satisfied": False,
            "solve_time_s": .01, "constraint_evaluations": []}
        if i < 2:
            pose = {"position_xyz": [.405 + .005 * i, 0., .3], "quaternion_xyzw": [0., 0., 0., 1.]}
            for program in resolved.bundle.programs:
                raw = math.dist(pose["position_xyz"], program.keypoints[0]) - program.params[0]
                proposal["constraint_evaluations"].append({"program_id": program.program.program_id, "raw_g": raw,
                    "normalization_scale": .01, "penalty": max(0., raw / .01) ** 2})
            proposal.update(next_tcp_pose=pose, horizon_s=.2)
        else:
            proposal.update(status="hold", evaluation_valid=False, solver_status="timeout", constraints_satisfied=None,
                            solve_time_s=.15, reason="scripted_solver_timeout")
        elapsed = 152_000_000 if i == 2 else 11_000_000
        message("ControlResult", proposal, f"message-result-{i}", i, ns + elapsed)
        if i < 2:
            writer.add_event(f"evaluation-{i}", "evaluation", {"result_id": f"result-{i}", "pose": pose})
        writer.add_event(f"arrival-{i}", "result_arrival", {"result_id": f"result-{i}", "received_at": stamp(ns + elapsed + 1_000_000)})
        writer.add_event(f"acceptance-{i}", "acceptance", {"result_id": f"result-{i}", "command_accepted": i != 1,
            "reason": "scripted_earlier_epoch_after_release" if i == 1 else "scripted_accepted",
            "checked_at": stamp(ns + elapsed + 2_000_000), "applied_command_id": f"applied-{i}" if i != 1 else None})
        if i == 0:
            writer.add_event("request-0", "controller_request", {"at": stamp(ns + elapsed + 2_000_000), "result_id": "result-0",
                "detail": {"action": "apply", "pose": pose, "provenance": "scripted request fixture"}})
            writer.add_event("observation-0", "controller_observation", {"at": stamp(ns + 20_000_000), "result_id": "result-0",
                "detail": {"provenance": "scripted independent observation fixture, not plant output",
                           "observed_position_xyz": [.401, 0., .3]}})
    record("ControlResult", {"result_id": "local-hold", "snapshot_id": None, "weights_id": None, "control_epoch": 2,
        "command_seq": 4, "source_joystick_seq": None, "source_robot_state_seq": None, "status": "hold",
        "evaluation_valid": False, "solver_status": "not_run", "constraints_satisfied": None, "solve_time_s": 0,
        "constraint_evaluations": [], "reason": "scripted_release"})
    writer.add_event("local-hold-accepted", "acceptance", {"result_id": "local-hold", "command_accepted": True,
        "reason": "scripted_local_release", "checked_at": stamp(10_600_000_000), "applied_command_id": "applied-local-hold"})
    writer.add_event("local-stop-request", "controller_request", {"at": stamp(10_600_000_000), "result_id": "local-hold",
        "detail": {"actions": ["cancel", "stop"], "provenance": "scripted request; no observed stop supplied"}})
    return writer.seal()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    archive = build_fixture(source_revision=args.source_revision, wheel_sha256=args.wheel_sha256)
    archive.write(args.output)
    print(json.dumps({"path": args.output, "sha256": archive.sha256}))


if __name__ == "__main__":
    main()
