"""Strict schema parsing without reference resolution or execution authority.

Hashes are checked for format only. Parsing a receipt does not issue or trust it.
Cross-record identity, calibration, source hashing, and execution checks belong
to later layers. A policy must be selected explicitly at every parse boundary.
"""

from collections.abc import Callable
from dataclasses import fields
import json
import math
import re
from typing import Any, TypeVar, overload

from .errors import ValidationError
from .policy import ValidationPolicy
from . import records as r

MAX_SAFE_INTEGER = 9007199254740991
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_R = TypeVar("_R", bound=r.Record)
_Validator = Callable[[Any, str, ValidationPolicy], Any]


def _fail(path: str, code: str, message: str) -> None:
    raise ValidationError(path, code, message)


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if key.isidentifier() else f"{path}[{json.dumps(key)}]"


def _policy(policy: Any) -> None:
    if type(policy) is not ValidationPolicy:
        _fail("$", "type", "policy must be an explicit ValidationPolicy")


def _object(value: Any, path: str, expected: tuple[str, ...]) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(path, "type", "expected a JSON object")
    for key in value:
        if type(key) is not str:
            _fail(path, "type", "object keys must be strings")
        if key not in expected:
            _fail(_child(path, key), "unknown_field", "field is not defined by this record")
    for key in expected:
        if key not in value:
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


_unit = _enum("m", "rad", "dimensionless")
_abi = _enum("geometry-residual-v1")


def _hash(value: Any, path: str, policy: ValidationPolicy) -> str:
    value = _string(value, path, policy)
    if _HASH.fullmatch(value) is None:
        _fail(path, "value", "expected exactly 64 lowercase hexadecimal characters")
    return value


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


def _source(value: Any, path: str, policy: ValidationPolicy) -> str:
    value = _string(value, path, policy)
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        _fail(path, "value", "source must be encodable as exact UTF-8 bytes")
    if size > policy.max_source_bytes:
        _fail(path, "limit", f"source exceeds {policy.max_source_bytes} UTF-8 bytes")
    return value


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
    r.ProgramRef: {"program_id": _identifier, "source_sha256": _hash},
    r.GeometryRef: {"geometry_id": _identifier, "revision": _revision},
    r.MessageMeta: {
        "schema_version": _enum("1.0"), "session_id": _identifier,
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
        "keypoints": _array(_nested(r.Keypoint), minimum=1),
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
        "program_refs": _array(_nested(r.ProgramRef)),
    },
    r.Parameter: {"name": _identifier, "value": _number, "unit": _unit},
    r.ConstraintProgram: {
        "program_id": _identifier, "source_sha256": _hash,
        "function_api_version": _abi, "object_id": _identifier,
        "description": _string, "entrypoint": _enum("evaluate"), "source_code": _source,
        "keypoint_bindings": _array(_identifier, limit="max_keypoint_bindings"),
        "axis_bindings": _array(_identifier, limit="max_axis_bindings"),
        "parameters": _array(_nested(r.Parameter), limit="max_parameters"),
        "output_unit": _unit,
    },
    r.ProgramExecutionBinding: {
        "binding_id": _identifier, "program_id": _identifier, "config_id": _identifier,
        "normalization_scale": _positive, "weight": _nonnegative,
        "validation_receipt_id": _identifier,
    },
    r.ProgramValidationReceipt: {
        "receipt_id": _identifier, "program_id": _identifier, "source_sha256": _hash,
        "function_api_version": _abi, "scene_id": _identifier,
        "feature_set_id": _identifier, "validation_geometry_id": _identifier,
        "validation_geometry_revision": _revision, "validator_policy_version": _identifier,
        "status": _enum("accepted", "rejected"), "diagnostics": _array(_explanation),
    },
}
_TYPES = {cls.__name__: cls for cls in _SCHEMAS}
_MESSAGE_TYPES = {
    cls.__name__: cls for cls in (
        r.SessionConfig, r.TaskContext, r.SceneObservation, r.SceneGeometry,
        r.SemanticContext, r.ConstraintProgram, r.Parameter,
        r.ProgramExecutionBinding, r.ProgramValidationReceipt,
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


def _parse(cls: type[_R], data: Any, path: str, policy: ValidationPolicy) -> _R:
    schema = _SCHEMAS[cls]
    data = _object(data, path, tuple(schema))
    record = cls(**{key: validate(data[key], _child(path, key), policy)
                    for key, validate in schema.items()})
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
        _unique(record.program_refs, f"{path}.program_refs", "program_id")
    elif isinstance(record, r.ConstraintProgram):
        _unique(record.keypoint_bindings, f"{path}.keypoint_bindings")
        _unique(record.axis_bindings, f"{path}.axis_bindings")
        _unique(record.parameters, f"{path}.parameters", "name")
    elif isinstance(record, r.ProgramValidationReceipt):
        if record.status == "rejected" and not record.diagnostics:
            _fail(f"{path}.diagnostics", "value", "a rejected receipt needs an explanation")
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
    """Validate an envelope and supported scene/artifact payload atomically."""
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
                for field in fields(value)}
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
