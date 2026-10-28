"""Immutable values for schema 2.0 scene, artifact and control records.

Direct dataclass construction is a value-container API, not validation. Use
``parse_record`` or ``parse_message`` at trust boundaries; those functions check
every value and construct immutable nested records and tuples without repairs.
"""

from dataclasses import dataclass
from typing import Literal, TypeAlias

Number: TypeAlias = int | float
Vector2: TypeAlias = tuple[Number, Number]
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
class GeometryRef(Record):
    geometry_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class TaskRef(Record):
    task_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class MessageMeta(Record):
    schema_version: Literal["2.0"]
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


@dataclass(frozen=True, slots=True)
class CandidatePoint(Record):
    """Host-grounded TCP target in CandidateSet.frame_id, in metres."""
    candidate_id: str
    object_id: str
    position_xyz: Vector3


@dataclass(frozen=True, slots=True)
class CandidateSet(Record):
    candidate_set_id: str
    task_id: str
    task_revision: int
    scene_id: str
    feature_set_id: str
    observation_id: str
    frame_id: str
    candidates: tuple[CandidatePoint, ...]


@dataclass(frozen=True, slots=True)
class JoystickSample(Record):
    sampled_at: TimePoint
    mapping_id: str
    u_xy: Vector2
    enabled: bool


@dataclass(frozen=True, slots=True)
class RobotState(Record):
    sampled_at: TimePoint
    frame_id: str
    tcp_frame: str
    tcp_pose: Pose
    joint_names: tuple[str, ...]
    joint_positions_rad: tuple[Number, ...]
    state_source: Literal["measured_feedback", "fk_from_measured_joints"]
    # None represents an absent wire field, never measured zero velocity.
    joint_velocities_radps: tuple[Number, ...] | None = None


@dataclass(frozen=True, slots=True)
class ControlSnapshot(Record):
    snapshot_id: str
    snapshot_seq: int
    control_epoch: int
    config_id: str
    task_ref: TaskRef
    dt_s: Number
    joystick_ref: str
    robot_state_ref: str
    joystick_history_refs: tuple[str, ...]
    geometry_ref: GeometryRef
    semantic_id: str
    candidate_set_id: str
    previous_selection_id: str | None
    previous_applied_command_id: str | None


@dataclass(frozen=True, slots=True)
class CandidateScore(Record):
    candidate_id: str
    score: Number
    distance_m: Number


@dataclass(frozen=True, slots=True)
class CandidateSelection(Record):
    selection_id: str
    snapshot_id: str
    estimator_config_id: str
    candidate_set_id: str
    selected_candidate_id: str | None
    scores: tuple[CandidateScore, ...]
    assistance_strength: Number


@dataclass(frozen=True, slots=True)
class CandidateEvaluation(Record):
    candidate_id: str
    distance_m: Number
    raw_g: Number
    normalization_scale: Number
    penalty: Number


@dataclass(frozen=True, slots=True)
class ControlResult(Record):
    """Candidate proposal; parsers enforce the move/hold field branches."""

    result_id: str
    snapshot_id: str | None
    selection_id: str | None
    control_epoch: int
    command_seq: int
    source_joystick_seq: int | None
    source_robot_state_seq: int | None
    status: Literal["move", "hold"]
    evaluation_valid: bool
    solver_status: Literal["solved", "infeasible", "timeout", "error", "not_run"]
    constraints_satisfied: bool | None
    solve_time_s: Number
    candidate_evaluation: CandidateEvaluation | None
    next_tcp_pose: Pose | None = None
    horizon_s: Number | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CommandAcceptanceRecord(Record):
    result_id: str
    command_accepted: bool
    reason: str
    checked_at: TimePoint
    applied_command_id: str | None


Payload: TypeAlias = (
    SessionConfig | TaskContext | SceneObservation | SceneGeometry | SemanticContext
    | CandidateSet | CandidateSelection | JoystickSample | RobotState
    | ControlSnapshot | ControlResult
)


@dataclass(frozen=True, slots=True)
class Message(Record):
    message_type: str
    meta: MessageMeta
    payload: Payload
