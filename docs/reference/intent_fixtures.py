"""Synthetic resolved inputs for intent tests; receipts establish compatibility only.

No receipt in these fixtures was issued by a live validator and none authorizes
activation. Each variation builds a fresh session registry; this is not an archive.
"""

import json
from pathlib import Path

from ssc_core import NUMERIC_OFFLINE_V1, SessionRegistry


ROOT = Path(__file__).resolve().parents[2]
BOTTLE = "object-bottle-offline-v1"
CUP = "object-cup-offline-v1"


def fixture(*, current=(1, 0), enabled=True, history=(), relevance=(.5, .5),
            alpha_zero=False, positions=None, reverse_storage=False, single=False):
    catalog = json.loads((ROOT / "tests/fixtures/shared_control/bottle_cup_offline_v1.json").read_text())
    messages = {item["message_type"]: item for item in catalog["messages"]}
    geometry = messages["SceneGeometry"]["payload"]
    semantic = messages["SemanticContext"]["payload"]
    config = messages["SessionConfig"]["payload"]
    if alpha_zero:
        config["config_id"] = "config-offline-alpha0-v1"
    if positions:
        for point, position in zip(geometry["keypoints"], positions):
            point["position_xyz"] = list(position)
    for item, score in zip(semantic["object_relevance"], relevance):
        item["task_relevance"] = score
    if single:
        semantic["object_relevance"] = semantic["object_relevance"][:1]
        semantic["program_refs"] = semantic["program_refs"][:1]
        catalog["constraint_programs"] = catalog["constraint_programs"][:1]
        catalog["execution_bindings"] = catalog["execution_bindings"][:1]
    if reverse_storage:
        for name in ("objects", "keypoints", "axes"):
            geometry[name].reverse()
        semantic["object_relevance"].reverse()
        semantic["program_refs"].reverse()
        catalog["constraint_programs"].reverse()
        catalog["execution_bindings"].reverse()
    session_id = messages["SessionConfig"]["meta"]["session_id"]
    registry = SessionRegistry(session_id, policy=NUMERIC_OFFLINE_V1)
    for message in messages.values():
        registry.register_message(message)
    for program in catalog["constraint_programs"]:
        registry.register_record("ConstraintProgram", program, session_id=session_id)
    for index, binding in enumerate(catalog["execution_bindings"]):
        program = next(item for item in catalog["constraint_programs"] if item["program_id"] == binding["program_id"])
        binding["config_id"] = config["config_id"]
        binding["validation_receipt_id"] = f"compatibility-only-{index}"
        receipt = dict(receipt_id=binding["validation_receipt_id"], program_id=program["program_id"],
                       source_sha256=program["source_sha256"], function_api_version=program["function_api_version"],
                       scene_id=geometry["scene_id"], feature_set_id=geometry["feature_set_id"],
                       validation_geometry_id=geometry["geometry_id"], validation_geometry_revision=geometry["revision"],
                       validator_policy_version=NUMERIC_OFFLINE_V1.policy_id, status="accepted", diagnostics=[])
        registry.register_record("ProgramValidationReceipt", receipt, session_id=session_id)
        registry.register_record("ProgramExecutionBinding", binding, session_id=session_id)

    def stamp(ns):
        sec, nano = divmod(ns, 1_000_000_000)
        return {"clock_id": "synthetic-clock-offline-v1", "sec": sec, "nanosec": nano}

    def message(kind, payload, identifier, seq):
        return registry.register_message({"message_type": kind, "payload": payload,
            "meta": {"schema_version": "1.0", "session_id": session_id, "message_id": identifier,
                     "source_id": "synthetic-intent-input", "sequence": seq, "produced_at": stamp(10_000_000_000)}})

    for index, (vector, on) in enumerate(history):
        message("JoystickSample", {"sampled_at": stamp(10_000_000_000 - (len(history) - index) * 20_000_000),
            "mapping_id": "synthetic-intent-mapping", "u_xy": list(vector), "enabled": on}, f"history-{index}", index)
    message("JoystickSample", {"sampled_at": stamp(10_000_000_000), "mapping_id": "synthetic-intent-mapping",
            "u_xy": list(current), "enabled": enabled}, "current-input", 100)
    message("RobotState", {"sampled_at": stamp(10_000_000_000), "frame_id": config["control_frame"],
            "tcp_frame": config["tcp_frame"], "tcp_pose": catalog["initial_pose"],
            "joint_names": catalog["initial_joint_names"], "joint_positions_rad": catalog["initial_joint_positions_rad"],
            "state_source": "fk_from_measured_joints"}, "measured-state", 100)
    task = messages["TaskContext"]["payload"]
    registry.register_record("ControlSnapshot", {"snapshot_id": "snapshot-intent-fixture", "snapshot_seq": 1,
        "control_epoch": 0, "config_id": config["config_id"], "task_ref": {"task_id": task["task_id"], "revision": task["revision"]},
        "dt_s": .2, "joystick_ref": "current-input", "robot_state_ref": "measured-state",
        "joystick_history_refs": [f"history-{index}" for index in range(len(history))],
        "geometry_ref": {"geometry_id": geometry["geometry_id"], "revision": geometry["revision"]},
        "semantic_id": semantic["semantic_id"], "program_refs": semantic["program_refs"],
        "execution_binding_refs": [item["binding_id"] for item in catalog["execution_bindings"]],
        "previous_applied_command_id": None}, session_id=session_id)
    return registry.resolve_snapshot("snapshot-intent-fixture")


CASES = (
    ("no-history", {}, (.75, .25), .5, "active"),
    ("sustained", {"history": (((1, 0), True),)}, (13/16, 3/16), .5, "active"),
    ("reversal", {"current": (-1, 0), "history": (((1, 0), True),)}, (5/16, 11/16), .5, "active"),
    ("adverse-relevance-reversal", {"current": (-1, 0), "history": (((1, 0), True),), "relevance": (1, 0)},
     (7/16, 9/16), .5, "active"),
    ("neutral-old-history", {"current": (0, 0), "history": (((1, 0), True),)}, (9/16, 7/16), 0., "disabled"),
    ("disabled-old-history", {"enabled": False, "history": (((1, 0), True),)}, (9/16, 7/16), 0., "disabled"),
    ("equal-support", {"current": (0, 1)}, (.5, .5), .5, "active"),
    ("zero-relevance", {"relevance": (0, 0)}, (5/6, 1/6), .5, "active"),
    ("ignored-neutral-disabled-history", {"history": (((0, 0), True), ((-1, 0), False))}, (.75, .25), .5, "active"),
    ("mean-of-unit-directions", {"history": (((.1, 0), True), ((-1, 0), True))}, (.75, .25), .5, "active"),
    ("alpha-zero", {"alpha_zero": True}, (.75, .25), 0., "active"),
    ("targets-at-tcp", {"positions": ((.4, 0, .3), (.4, 0, .3))}, (.5, .5), .5, "active"),
    ("single-candidate", {"single": True}, (1.,), .5, "active"),
    ("storage-permutation", {"reverse_storage": True}, (.75, .25), .5, "active"),
    ("nonzero-subnormal-input", {"current": (5e-324, 0)}, (.75, .25), .5, "active"),
)
