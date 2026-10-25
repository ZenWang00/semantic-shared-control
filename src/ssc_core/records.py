"""Immutable values for schema 1.0 scene and artifact records.

Direct dataclass construction is a value-container API, not validation. Use
``parse_record`` or ``parse_message`` at trust boundaries; those functions check
every value and construct immutable nested records and tuples without repairs.
"""

from dataclasses import dataclass
from typing import Literal, TypeAlias

Number: TypeAlias = int | float
Vector3: TypeAlias = tuple[Number, Number, Number]
Quaternion: TypeAlias = tuple[Number, Number, Number, Number]
Matrix3: TypeAlias = tuple[Vector3, Vector3, Vector3]
Unit: TypeAlias = Literal["m", "rad", "dimensionless"]


class Record:
    """Marker for supported immutable value containers."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class TimePoint(Record):
    clock_id: str
    sec: int
    nanosec: int


@dataclass(frozen=True, slots=True)
class Pose(Record):
    position_xyz: Vector3
    quaternion_xyzw: Quaternion


@dataclass(frozen=True, slots=True)
class StampedTransform(Record):
    parent_frame: str
    child_frame: str
    sampled_at: TimePoint
    pose: Pose


@dataclass(frozen=True, slots=True)
class AssetRef(Record):
    asset_id: str
    media_type: str
    encoding: str
    shape: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ProgramRef(Record):
    program_id: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class GeometryRef(Record):
    geometry_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class MessageMeta(Record):
    schema_version: Literal["1.0"]
    session_id: str
    message_id: str
    source_id: str
    sequence: int
    produced_at: TimePoint


@dataclass(frozen=True, slots=True)
class SessionConfig(Record):
    config_id: str
    control_frame: str
    tcp_frame: str
    robot_model_id: str
    validation_policy_id: str
    motion_constraint_set_id: str
    control_period_s: Number
    max_xy_speed_mps: Number
    max_linear_speed_mps: Number
    max_angular_speed_radps: Number
    joystick_timeout_s: Number
    robot_state_timeout_s: Number
    solve_budget_s: Number
    max_command_age_s: Number
    history_window_s: Number
    max_capture_skew_s: Number


@dataclass(frozen=True, slots=True)
class TaskContext(Record):
    task_id: str
    revision: int
    instruction: str


@dataclass(frozen=True, slots=True)
class SceneObservation(Record):
    observation_id: str
    scene_id: str
    rgb_stamp: TimePoint
    depth_stamp: TimePoint
    camera_frame: str
    rgb_ref: AssetRef
    depth_ref: AssetRef
    K: Matrix3
    projection_model: Literal["pinhole_rectified"]
    calibration_id: str
    T_control_camera: StampedTransform


@dataclass(frozen=True, slots=True)
class Object(Record):
    object_id: str
    label: str


@dataclass(frozen=True, slots=True)
class Keypoint(Record):
    keypoint_id: str
    object_id: str
    position_xyz: Vector3


@dataclass(frozen=True, slots=True)
class Axis(Record):
    axis_id: str
    object_id: str
    origin_keypoint_id: str
    unit_direction_xyz: Vector3
    directed: bool


@dataclass(frozen=True, slots=True)
class SceneGeometry(Record):
    geometry_id: str
    scene_id: str
    feature_set_id: str
    revision: int
    observation_id: str
    frame_id: str
    mode: Literal["static"]
    objects: tuple[Object, ...]
    keypoints: tuple[Keypoint, ...]
    axes: tuple[Axis, ...]


@dataclass(frozen=True, slots=True)
class ObjectRelevance(Record):
    object_id: str
    task_relevance: Number


@dataclass(frozen=True, slots=True)
class SemanticContext(Record):
    semantic_id: str
    task_id: str
    task_revision: int
    scene_id: str
    feature_set_id: str
    observation_id: str
    object_relevance: tuple[ObjectRelevance, ...]
    program_refs: tuple[ProgramRef, ...]


@dataclass(frozen=True, slots=True)
class Parameter(Record):
    name: str
    value: Number
    unit: Unit


@dataclass(frozen=True, slots=True)
class ConstraintProgram(Record):
    program_id: str
    source_sha256: str
    function_api_version: Literal["geometry-residual-v1"]
    object_id: str
    description: str
    entrypoint: Literal["evaluate"]
    source_code: str
    keypoint_bindings: tuple[str, ...]
    axis_bindings: tuple[str, ...]
    parameters: tuple[Parameter, ...]
    output_unit: Unit


@dataclass(frozen=True, slots=True)
class ProgramExecutionBinding(Record):
    binding_id: str
    program_id: str
    config_id: str
    normalization_scale: Number
    weight: Number
    validation_receipt_id: str


@dataclass(frozen=True, slots=True)
class ProgramValidationReceipt(Record):
    receipt_id: str
    program_id: str
    source_sha256: str
    function_api_version: Literal["geometry-residual-v1"]
    scene_id: str
    feature_set_id: str
    validation_geometry_id: str
    validation_geometry_revision: int
    validator_policy_version: str
    status: Literal["accepted", "rejected"]
    diagnostics: tuple[str, ...]


Payload: TypeAlias = (
    SessionConfig | TaskContext | SceneObservation | SceneGeometry
    | SemanticContext | ConstraintProgram | Parameter | ProgramExecutionBinding
    | ProgramValidationReceipt
)


@dataclass(frozen=True, slots=True)
class Message(Record):
    message_type: str
    meta: MessageMeta
    payload: Payload
