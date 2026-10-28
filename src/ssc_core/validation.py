"""Strict schema parsing without reference resolution or execution authority.

Cross-record identity and calibration checks belong to later layers.
A policy must be selected explicitly at every parse boundary.
"""

from collections.abc import Callable
from dataclasses import fields
import json
import math
from typing import Any, TypeVar, overload

from .errors import ValidationError
from .policy import ValidationPolicy
from . import records as r

MAX_SAFE_INTEGER = 9007199254740991
_R = TypeVar("_R", bound=r.Record)
_Validator = Callable[[Any, str, ValidationPolicy], Any]


def _fail(path: str, code: str, message: str) -> None:
    raise ValidationError(path, code, message)


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if key.isidentifier() else f"{path}[{json.dumps(key)}]"


def _policy(policy: Any) -> None:
    if type(policy) is not ValidationPolicy:
        _fail("$", "type", "policy must be an explicit ValidationPolicy")


def _object(
    value: Any, path: str, expected: tuple[str, ...], *, optional: tuple[str, ...] = (),
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(path, "type", "expected a JSON object")
    for key in value:
        if type(key) is not str:
            _fail(path, "type", "object keys must be strings")
        if key not in expected:
            _fail(_child(path, key), "unknown_field", "field is not defined by this record")
    for key in expected:
        if key not in value and key not in optional:
            _fail(_child(path, key), "missing_field", "required field is missing")
    return value


def _string(value: Any, path: str, policy: ValidationPolicy) -> str:
    if type(value) is not str:
        _fail(path, "type", "expected a string")
    return value


def _identifier(value: Any, path: str, policy: ValidationPolicy) -> str:
    value = _string(value, path, policy)
    if not value:
        _fail(path, "value", "expected a nonempty string")
    return value


def _explanation(value: Any, path: str, policy: ValidationPolicy) -> str:
    value = _string(value, path, policy)
    if not value.strip():
        _fail(path, "value", "expected nonblank explanatory text")
    return value


def _integer(value: Any, path: str, policy: ValidationPolicy) -> int:
    if type(value) is not int:
        _fail(path, "type", "expected an integer, not a Boolean or converted number")
    return value


def _bounded_integer(minimum: int, maximum: int) -> _Validator:
    def validate(value: Any, path: str, policy: ValidationPolicy) -> int:
        value = _integer(value, path, policy)
        if not minimum <= value <= maximum:
            _fail(path, "range", f"expected an integer in [{minimum}, {maximum}]")
        return value
    return validate


_counter = _bounded_integer(0, MAX_SAFE_INTEGER)
_revision = _bounded_integer(1, MAX_SAFE_INTEGER)
_nanosecond = _bounded_integer(0, 999999999)


def _number(value: Any, path: str, policy: ValidationPolicy) -> r.Number:
    if type(value) not in (int, float):
        _fail(path, "type", "expected a number, not a Boolean or converted string")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _fail(path, "non_finite", "expected a finite number representable as float64")
    return value


def _positive(value: Any, path: str, policy: ValidationPolicy) -> r.Number:
    value = _number(value, path, policy)
    if value <= 0:
        _fail(path, "range", "expected a positive number")
    return value


def _nonnegative(value: Any, path: str, policy: ValidationPolicy) -> r.Number:
    value = _number(value, path, policy)
    if value < 0:
        _fail(path, "range", "expected a nonnegative number")
    return value


def _score(value: Any, path: str, policy: ValidationPolicy) -> r.Number:
    value = _number(value, path, policy)
    if not 0 <= value <= 1:
        _fail(path, "range", "expected a score in [0, 1]")
    return value


def _boolean(value: Any, path: str, policy: ValidationPolicy) -> bool:
    if type(value) is not bool:
        _fail(path, "type", "expected a Boolean")
    return value


def _enum(*allowed: str) -> _Validator:
    def validate(value: Any, path: str, policy: ValidationPolicy) -> str:
        value = _string(value, path, policy)
        if value not in allowed:
            _fail(path, "enum", f"expected one of {allowed!r}")
        return value
    return validate


def _array(
    item: _Validator, *, length: int | None = None,
    minimum: int = 0, limit: str | None = None,
) -> _Validator:
    def validate(value: Any, path: str, policy: ValidationPolicy) -> tuple[Any, ...]:
        if type(value) is not list:
            _fail(path, "type", "expected a JSON array")
        if length is not None and len(value) != length:
            _fail(path, "dimension", f"expected exactly {length} elements")
        if len(value) < minimum:
            _fail(path, "dimension", f"expected at least {minimum} elements")
        if limit is not None and len(value) > getattr(policy, limit):
            _fail(path, "limit", f"exceeds {limit}={getattr(policy, limit)}")
        return tuple(item(v, f"{path}[{i}]", policy) for i, v in enumerate(value))
    return validate


def _nested(cls: type[_R]) -> _Validator:
    return lambda value, path, policy: _parse(cls, value, path, policy)


def _nullable(validate: _Validator) -> _Validator:
    return lambda value, path, policy: None if value is None else validate(value, path, policy)


_vector3 = _array(_number, length=3)
_SCHEMAS: dict[type[r.Record], dict[str, _Validator]] = {
    r.TimePoint: {"clock_id": _identifier, "sec": _integer, "nanosec": _nanosecond},
    r.Pose: {"position_xyz": _vector3, "quaternion_xyzw": _array(_number, length=4)},
    r.StampedTransform: {
        "parent_frame": _identifier, "child_frame": _identifier,
        "sampled_at": _nested(r.TimePoint), "pose": _nested(r.Pose),
    },
    r.AssetRef: {
        "asset_id": _identifier, "media_type": _string, "encoding": _string,
        "shape": _array(_revision),
    },
    r.GeometryRef: {"geometry_id": _identifier, "revision": _revision},
    r.TaskRef: {"task_id": _identifier, "revision": _revision},
    r.MessageMeta: {
        "schema_version": _enum("2.0"), "session_id": _identifier,
        "message_id": _identifier, "source_id": _identifier,
        "sequence": _counter, "produced_at": _nested(r.TimePoint),
    },
    r.SessionConfig: {
        "config_id": _identifier, "control_frame": _identifier,
        "tcp_frame": _identifier, "robot_model_id": _identifier,
        "validation_policy_id": _identifier, "motion_constraint_set_id": _identifier,
        "control_period_s": _positive, "max_xy_speed_mps": _positive,
        "max_linear_speed_mps": _positive, "max_angular_speed_radps": _positive,
        "joystick_timeout_s": _positive, "robot_state_timeout_s": _positive,
        "solve_budget_s": _positive, "max_command_age_s": _positive,
        "history_window_s": _positive, "max_capture_skew_s": _nonnegative,
    },
    r.TaskContext: {
        "task_id": _identifier, "revision": _revision, "instruction": _explanation,
    },
    r.SceneObservation: {
        "observation_id": _identifier, "scene_id": _identifier,
        "rgb_stamp": _nested(r.TimePoint), "depth_stamp": _nested(r.TimePoint),
        "camera_frame": _identifier, "rgb_ref": _nested(r.AssetRef),
        "depth_ref": _nested(r.AssetRef), "K": _array(_vector3, length=3),
        "projection_model": _enum("pinhole_rectified"), "calibration_id": _identifier,
        "T_control_camera": _nested(r.StampedTransform),
    },
    r.Object: {"object_id": _identifier, "label": _string},
    r.Keypoint: {
        "keypoint_id": _identifier, "object_id": _identifier, "position_xyz": _vector3,
    },
    r.Axis: {
        "axis_id": _identifier, "object_id": _identifier,
        "origin_keypoint_id": _identifier, "unit_direction_xyz": _vector3,
        "directed": _boolean,
    },
    r.SceneGeometry: {
        "geometry_id": _identifier, "scene_id": _identifier,
        "feature_set_id": _identifier, "revision": _revision,
        "observation_id": _identifier, "frame_id": _identifier, "mode": _enum("static"),
        "objects": _array(_nested(r.Object), minimum=1),
        "keypoints": _array(_nested(r.Keypoint)),
        "axes": _array(_nested(r.Axis)),
    },
    r.ObjectRelevance: {"object_id": _identifier, "task_relevance": _score},
    r.SemanticContext: {
        "semantic_id": _identifier, "task_id": _identifier, "task_revision": _revision,
        "scene_id": _identifier, "feature_set_id": _identifier,
        "observation_id": _identifier,
        "object_relevance": _array(
            _nested(r.ObjectRelevance), minimum=1, limit="max_candidate_objects",
        ),
    },
    r.CandidatePoint: {
        "candidate_id": _identifier, "object_id": _identifier, "position_xyz": _vector3,
    },
    r.CandidateSet: {
        "candidate_set_id": _identifier, "task_id": _identifier, "task_revision": _revision,
        "scene_id": _identifier, "feature_set_id": _identifier,
        "observation_id": _identifier, "frame_id": _identifier,
        "candidates": _array(_nested(r.CandidatePoint), minimum=1, limit="max_candidates"),
    },
    r.JoystickSample: {
        "sampled_at": _nested(r.TimePoint), "mapping_id": _identifier,
        "u_xy": _array(_number, length=2), "enabled": _boolean,
    },
    r.RobotState: {
        "sampled_at": _nested(r.TimePoint), "frame_id": _identifier,
        "tcp_frame": _identifier, "tcp_pose": _nested(r.Pose),
        "joint_names": _array(_identifier), "joint_positions_rad": _array(_number),
        "joint_velocities_radps": _array(_number),
        "state_source": _enum("measured_feedback", "fk_from_measured_joints"),
    },
    r.ControlSnapshot: {
        "snapshot_id": _identifier, "snapshot_seq": _counter, "control_epoch": _counter,
        "config_id": _identifier, "task_ref": _nested(r.TaskRef), "dt_s": _positive,
        "joystick_ref": _identifier, "robot_state_ref": _identifier,
        "joystick_history_refs": _array(_identifier), "geometry_ref": _nested(r.GeometryRef),
        "semantic_id": _identifier, "candidate_set_id": _identifier,
        "previous_selection_id": _nullable(_identifier),
        "previous_applied_command_id": _nullable(_identifier),
    },
    r.CandidateScore: {
        "candidate_id": _identifier, "score": _positive, "distance_m": _nonnegative,
    },
    r.CandidateSelection: {
        "selection_id": _identifier, "snapshot_id": _identifier,
        "estimator_config_id": _identifier, "candidate_set_id": _identifier,
        "selected_candidate_id": _nullable(_identifier),
        "scores": _array(_nested(r.CandidateScore), minimum=1, limit="max_candidates"),
        "assistance_strength": _score,
    },
    r.CandidateEvaluation: {
        "candidate_id": _identifier, "distance_m": _nonnegative, "raw_g": _number,
        "normalization_scale": _positive, "penalty": _nonnegative,
    },
    r.ControlResult: {
        "result_id": _identifier, "snapshot_id": _nullable(_identifier),
        "selection_id": _nullable(_identifier), "control_epoch": _counter,
        "command_seq": _counter, "source_joystick_seq": _nullable(_counter),
        "source_robot_state_seq": _nullable(_counter), "status": _enum("move", "hold"),
        "evaluation_valid": _boolean,
        "solver_status": _enum("solved", "infeasible", "timeout", "error", "not_run"),
        "constraints_satisfied": _nullable(_boolean), "solve_time_s": _nonnegative,
        "candidate_evaluation": _nullable(_nested(r.CandidateEvaluation)),
        "next_tcp_pose": _nested(r.Pose), "horizon_s": _positive, "reason": _explanation,
    },
    r.CommandAcceptanceRecord: {
        "result_id": _identifier, "command_accepted": _boolean, "reason": _explanation,
        "checked_at": _nested(r.TimePoint), "applied_command_id": _nullable(_identifier),
    },
}
_OPTIONAL_FIELDS: dict[type[r.Record], tuple[str, ...]] = {
    r.RobotState: ("joint_velocities_radps",),
}
_RESULT_BRANCH_FIELDS = ("next_tcp_pose", "horizon_s", "reason")
# Only declared absent fields are omitted. Required nullable fields retain null.
_OMIT_ABSENT_FIELDS = {**_OPTIONAL_FIELDS, r.ControlResult: _RESULT_BRANCH_FIELDS}
_TYPES = {cls.__name__: cls for cls in _SCHEMAS}
_MESSAGE_TYPES = {
    cls.__name__: cls for cls in (
        r.SessionConfig, r.TaskContext, r.SceneObservation, r.SceneGeometry,
        r.SemanticContext, r.CandidateSet, r.CandidateSelection,
        r.JoystickSample, r.RobotState, r.ControlSnapshot, r.ControlResult,
    )
}


def _unique(values: tuple[Any, ...], path: str, field: str | None = None) -> None:
    seen: set[str] = set()
    for i, item in enumerate(values):
        value = getattr(item, field) if field else item
        item_path = f"{path}[{i}]"
        if field:
            item_path = _child(item_path, field)
        if value in seen:
            _fail(item_path, "duplicate", f"duplicate identifier {value!r}")
        seen.add(value)


def _unit_norm(vector: tuple[r.Number, ...], path: str, policy: ValidationPolicy) -> None:
    # hypot avoids overflow/underflow from summing squared components.
    if abs(math.hypot(*vector) - 1) > policy.unit_norm_tolerance:
        _fail(path, "unit_norm", f"unit norm required within {policy.unit_norm_tolerance}")


def _observation(record: r.SceneObservation, path: str) -> None:
    if record.rgb_stamp.clock_id != record.depth_stamp.clock_id:
        _fail(f"{path}.depth_stamp.clock_id", "value", "RGB and depth must share a clock domain")
    if record.T_control_camera.sampled_at != record.rgb_stamp:
        _fail(f"{path}.T_control_camera.sampled_at", "value", "transform must be sampled at rgb_stamp")
    if record.T_control_camera.child_frame != record.camera_frame:
        _fail(f"{path}.T_control_camera.child_frame", "value", "transform child must equal camera_frame")
    if record.rgb_ref.encoding != "uint8_rgb":
        _fail(f"{path}.rgb_ref.encoding", "enum", "RGB encoding must be uint8_rgb")
    if record.depth_ref.encoding != "float32_m":
        _fail(f"{path}.depth_ref.encoding", "enum", "depth encoding must be float32_m")
    if len(record.rgb_ref.shape) != 3 or record.rgb_ref.shape[-1] != 3:
        _fail(f"{path}.rgb_ref.shape", "dimension", "RGB shape must be [H, W, 3]")
    if len(record.depth_ref.shape) != 2:
        _fail(f"{path}.depth_ref.shape", "dimension", "depth shape must be [H, W]")
    if record.rgb_ref.shape[:2] != record.depth_ref.shape:
        _fail(f"{path}.depth_ref.shape", "dimension", "aligned RGB and depth dimensions must match")
    for index in (0, 1):
        if record.K[index][index] <= 0:
            _fail(f"{path}.K[{index}][{index}]", "range", "focal length must be positive")


def _geometry(record: r.SceneGeometry, path: str) -> None:
    _unique(record.objects, f"{path}.objects", "object_id")
    _unique(record.keypoints, f"{path}.keypoints", "keypoint_id")
    _unique(record.axes, f"{path}.axes", "axis_id")
    object_ids = {item.object_id for item in record.objects}
    keypoints = {item.keypoint_id: item for item in record.keypoints}
    for i, keypoint in enumerate(record.keypoints):
        if keypoint.object_id not in object_ids:
            _fail(f"{path}.keypoints[{i}].object_id", "reference", "owner is absent from objects")
    for i, axis in enumerate(record.axes):
        if axis.object_id not in object_ids:
            _fail(f"{path}.axes[{i}].object_id", "reference", "owner is absent from objects")
        origin = keypoints.get(axis.origin_keypoint_id)
        if origin is None:
            _fail(f"{path}.axes[{i}].origin_keypoint_id", "reference", "origin is absent from keypoints")
        if origin.object_id != axis.object_id:
            _fail(f"{path}.axes[{i}].origin_keypoint_id", "reference", "axis origin belongs to another object")


def _robot_state(record: r.RobotState, path: str) -> None:
    _unique(record.joint_names, f"{path}.joint_names")
    for field in ("joint_positions_rad", "joint_velocities_radps"):
        values = getattr(record, field)
        if values is not None and len(values) != len(record.joint_names):
            _fail(f"{path}.{field}", "dimension", "length must match joint_names")


def _control_result(record: r.ControlResult, path: str) -> None:
    if record.solver_status == "not_run" and record.solve_time_s != 0:
        _fail(f"{path}.solve_time_s", "value", "not_run requires zero solve time")
    if record.status == "move":
        for field in ("snapshot_id", "selection_id", "source_joystick_seq", "source_robot_state_seq"):
            if getattr(record, field) is None:
                _fail(f"{path}.{field}", "value", "move requires a nonnull input reference")
        if not record.evaluation_valid:
            _fail(f"{path}.evaluation_valid", "value", "move requires valid evaluation")
        if record.solver_status != "solved":
            _fail(f"{path}.solver_status", "value", "move requires a solved result")
        if record.constraints_satisfied is None:
            _fail(f"{path}.constraints_satisfied", "value", "move requires an evaluated satisfaction flag")


def _parse(cls: type[_R], data: Any, path: str, policy: ValidationPolicy) -> _R:
    schema = _SCHEMAS[cls]
    if cls is r.ControlResult:
        data = _object(data, path, tuple(schema), optional=_RESULT_BRANCH_FIELDS)
        status = schema["status"](data["status"], f"{path}.status", policy)
        branch = ("next_tcp_pose", "horizon_s") if status == "move" else ("reason",)
        schema = {key: validate for key, validate in schema.items()
                  if key not in _RESULT_BRANCH_FIELDS or key in branch}
    data = _object(data, path, tuple(schema), optional=_OPTIONAL_FIELDS.get(cls, ()))
    record = cls(**{key: validate(data[key], _child(path, key), policy)
                    for key, validate in schema.items() if key in data})
    if isinstance(record, r.Pose):
        _unit_norm(record.quaternion_xyzw, f"{path}.quaternion_xyzw", policy)
    elif isinstance(record, r.Axis):
        _unit_norm(record.unit_direction_xyz, f"{path}.unit_direction_xyz", policy)
    elif isinstance(record, r.SceneObservation):
        _observation(record, path)
    elif isinstance(record, r.SceneGeometry):
        _geometry(record, path)
    elif isinstance(record, r.SemanticContext):
        _unique(record.object_relevance, f"{path}.object_relevance", "object_id")
    elif isinstance(record, r.CandidateSet):
        _unique(record.candidates, f"{path}.candidates", "candidate_id")
    elif isinstance(record, r.CandidateSelection):
        _unique(record.scores, f"{path}.scores", "candidate_id")
        if record.selected_candidate_id is not None and record.selected_candidate_id not in {
                item.candidate_id for item in record.scores}:
            _fail(f"{path}.selected_candidate_id", "reference", "selected candidate lacks a score")
        if record.selected_candidate_id is None and record.assistance_strength != 0:
            _fail(f"{path}.assistance_strength", "value", "disabled selection requires zero assistance")
    elif isinstance(record, r.ControlSnapshot):
        _unique(record.joystick_history_refs, f"{path}.joystick_history_refs")
        if record.joystick_ref in record.joystick_history_refs:
            _fail(f"{path}.joystick_history_refs", "reference", "history includes current input")
    elif isinstance(record, r.JoystickSample):
        if math.hypot(*record.u_xy) > 1:
            _fail(f"{path}.u_xy", "range", "joystick vector must lie in the unit disk")
    elif isinstance(record, r.RobotState):
        _robot_state(record, path)
    elif isinstance(record, r.ControlResult):
        _control_result(record, path)
    return record


@overload
def parse_record(record_type: type[_R], data: dict[str, Any], *, policy: ValidationPolicy) -> _R: ...


@overload
def parse_record(record_type: str, data: dict[str, Any], *, policy: ValidationPolicy) -> r.Record: ...


def parse_record(
    record_type: str | type[_R], data: dict[str, Any], *, policy: ValidationPolicy,
) -> r.Record:
    """Validate a supported record by its exact name or exported class.

    Input is JSON-shaped: dictionaries, lists and exact builtin scalars. No
    strings/numbers are coerced, and no default or normalization is applied.
    Only record-local invariants are checked; foreign references remain opaque.
    """
    _policy(policy)
    if type(record_type) is str:
        cls = _TYPES.get(record_type)
    elif isinstance(record_type, type) and record_type in _SCHEMAS:
        cls = record_type
    else:
        cls = None
    if cls is None:
        _fail("$", "enum", "unsupported record type")
    return _parse(cls, data, "$", policy)


def parse_message(data: dict[str, Any], *, policy: ValidationPolicy) -> r.Message:
    """Validate an envelope and supported payload atomically."""
    _policy(policy)
    data = _object(data, "$", ("message_type", "meta", "payload"))
    message_type = _string(data["message_type"], "$.message_type", policy)
    cls = _MESSAGE_TYPES.get(message_type)
    if cls is None:
        _fail("$.message_type", "enum", "unsupported message type")
    return r.Message(
        message_type=message_type,
        meta=_parse(r.MessageMeta, data["meta"], "$.meta", policy),
        payload=_parse(cls, data["payload"], "$.payload", policy),
    )


class _ObjectPairs(list):
    """Retain duplicate JSON keys until paths are available."""


def _decode(value: Any, path: str) -> Any:
    if isinstance(value, _ObjectPairs):
        result = {}
        for key, item in value:
            item_path = _child(path, key)
            if key in result:
                _fail(item_path, "duplicate_key", f"duplicate JSON key {key!r}")
            result[key] = _decode(item, item_path)
        return result
    if type(value) is list:
        return [_decode(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if type(value) is float and not math.isfinite(value):
        _fail(path, "non_finite", "JSON numbers must be finite")
    return value


def loads_message(text: str, *, policy: ValidationPolicy) -> r.Message:
    """Decode JSON while rejecting duplicate keys, nonfinite values and errors."""
    _policy(policy)
    if type(text) is not str:
        _fail("$", "type", "expected JSON text")
    try:
        raw = json.loads(text, object_pairs_hook=_ObjectPairs)
        data = _decode(raw, "$")
    except json.JSONDecodeError as exc:
        raise ValidationError("$", "json", f"{exc.msg} at line {exc.lineno}, column {exc.colno}") from exc
    except (RecursionError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("$", "json", "JSON value exceeds decoder limits") from exc
    return parse_message(data, policy=policy)


def _serialize(value: Any, path: str) -> Any:
    if type(value) in _SCHEMAS or type(value) is r.Message:
        return {field.name: _serialize(getattr(value, field.name), _child(path, field.name))
                for field in fields(value)
                if not (field.name in _OMIT_ABSENT_FIELDS.get(type(value), ())
                        and getattr(value, field.name) is None)}
    if type(value) is tuple:
        return [_serialize(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if type(value) in (str, int, bool) or value is None:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail(path, "non_finite", "cannot serialize a nonfinite number")
        return value
    _fail(path, "type", "cannot serialize a non-record or mutable value")


def to_dict(record: r.Record) -> dict[str, Any]:
    """Return detached JSON-shaped values; this does not revalidate a policy.

    Use parsed records. Directly constructed/changed dataclasses are not made
    valid by serialization; unsupported or nonfinite contents are rejected.
    """
    if type(record) not in _SCHEMAS and type(record) is not r.Message:
        _fail("$", "type", "expected a supported record or message")
    return _serialize(record, "$")
