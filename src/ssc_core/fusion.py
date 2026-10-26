"""Pure, replayable intent-linear-v1 support; no execution or motion authority."""

from dataclasses import dataclass, fields
from hashlib import sha256
from math import fsum, hypot, isfinite
from typing import Literal

from . import records as r
from .errors import ValidationError
from .policy import NUMERIC_OFFLINE_V1, ValidationPolicy
from .registry import ResolvedBundle, ResolvedProgram, ResolvedSnapshot
from .validation import parse_message, parse_record, to_dict


@dataclass(frozen=True, slots=True)
class IntentConfig:
    """Local configuration resource, not a new wire record or tunable estimator."""

    estimator_config_id: str
    session_config_id: str
    alpha_active: float
    approach_keypoints: tuple[tuple[str, str], ...]
    history_limit: int = 32
    history_window_s: float = 1.0
    direction_epsilon_m: float = 1e-12
    score_floor: float = 0.25
    relevance_coefficient: float = 1.0
    current_coefficient: float = 2.0
    history_coefficient: float = 0.5


def get_intent_config(estimator_config_id: str) -> IntentConfig:
    """Return a fresh immutable copy of an approved configuration.

    Archive the complete dataclass content as well as its ID. Unknown IDs and
    altered definitions are rejected; adding a profile requires a code revision.
    """
    if estimator_config_id == "intent-linear-v1":
        session_id, alpha = "config-offline-v1", 0.5
    elif estimator_config_id == "intent-linear-alpha0-v1":
        session_id, alpha = "config-offline-alpha0-v1", 0.0
    elif estimator_config_id == "intent-linear-batch-v2":
        session_id, alpha = "config-offline-batch-v2", 0.5
    elif estimator_config_id == "intent-linear-batch-alpha0-v2":
        session_id, alpha = "config-offline-batch-alpha0-v2", 0.0
    else:
        raise ValueError(f"unsupported estimator configuration: {estimator_config_id!r}")
    return IntentConfig(estimator_config_id, session_id, alpha, (
        ("object-bottle-offline-v1", "kp-bottle-approach-offline-v1"),
        ("object-cup-offline-v1", "kp-cup-approach-offline-v1"),
    ))


@dataclass(frozen=True, slots=True)
class IntentOutcome:
    """active permits further computation only; disabled weights are diagnostic.

    error contains no weights. Callers route disabled/error to their existing
    local hold path; this function never creates a ControlResult or sends motion.
    """

    disposition: Literal["active", "disabled", "error"]
    weights: r.ObjectWeights | None
    code: str
    diagnostic: str


class _Invalid(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _require(condition, code, message):
    if not condition:
        raise _Invalid(code, message)


def _record(cls, value, policy):
    _require(type(value) is cls, "input_type", f"expected {cls.__name__}")
    return parse_record(cls, to_dict(value), policy=policy)


def _message(value, kind, session_id, policy):
    _require(type(value) is r.Message, "input_type", "expected Message")
    message = parse_message(to_dict(value), policy=policy)
    _require(message.message_type == kind and message.meta.session_id == session_id,
             "input_reference", f"expected current-session {kind}")
    return message


def _unique(values, key, name):
    result = {key(value): value for value in values}
    _require(len(result) == len(values), "duplicate_reference", name)
    return result


def _validate(resolved, config, policy):
    """Recheck consumed records and pinned links, even for constructed dataclasses.

    Observation availability, receipt trust, snapshot creation-time freshness and
    accepted-command provenance remain producer/host responsibilities. No live
    registry, clock, authority ledger or hidden estimator state is consulted.
    """
    _require(type(policy) is ValidationPolicy and policy == NUMERIC_OFFLINE_V1,
             "invalid_policy", "intent-linear-v1 requires the complete numeric-offline-v1 policy")
    _require(type(config) is IntentConfig, "invalid_config", "expected IntentConfig")
    try:
        expected = get_intent_config(config.estimator_config_id)
    except ValueError as exc:
        raise _Invalid("invalid_config", str(exc)) from exc
    # Exact types also reject bool-as-number and mutable containers behind an ID.
    _require(config == expected and
             all(type(getattr(config, field.name)) is type(getattr(expected, field.name))
                 for field in fields(expected)),
             "invalid_config", "configuration content differs from its immutable definition")
    _require(type(resolved) is ResolvedSnapshot and type(resolved.bundle) is ResolvedBundle,
             "input_type", "expected ResolvedSnapshot and ResolvedBundle")
    bundle = resolved.bundle
    _require(type(bundle.session_id) is str and bool(bundle.session_id), "input_reference", "session ID is required")
    snapshot = _record(r.ControlSnapshot, resolved.snapshot, policy)
    _unique(snapshot.program_refs, lambda item: item.program_id, "snapshot program IDs")
    _unique(snapshot.execution_binding_refs, lambda item: item, "snapshot binding IDs")
    _unique(snapshot.joystick_history_refs, lambda item: item, "history message IDs")
    _require(snapshot.joystick_ref not in snapshot.joystick_history_refs,
             "invalid_history", "history must exclude the current sample")
    session = _record(r.SessionConfig, bundle.configuration, policy)
    task = _record(r.TaskContext, bundle.task, policy)
    geometry = _record(r.SceneGeometry, bundle.geometry, policy)
    semantic = _record(r.SemanticContext, bundle.semantic, policy)
    _require(snapshot.config_id == session.config_id == config.session_config_id and
             session.validation_policy_id == policy.policy_id and
             session.history_window_s == config.history_window_s,
             "config_reference", "snapshot/session/estimator configuration mismatch")
    _require(snapshot.task_ref == r.TaskRef(task.task_id, task.revision) and
             (semantic.task_id, semantic.task_revision) == (task.task_id, task.revision) and
             snapshot.geometry_ref == r.GeometryRef(geometry.geometry_id, geometry.revision) and
             snapshot.semantic_id == semantic.semantic_id and
             (semantic.scene_id, semantic.feature_set_id) == (geometry.scene_id, geometry.feature_set_id) and
             geometry.frame_id == session.control_frame,
             "snapshot_reference", "task, geometry, semantics or frame differs from pinned snapshot")
    candidates = _unique(tuple(_record(r.Object, obj, policy) for obj in bundle.candidates),
                         lambda obj: obj.object_id, "candidate IDs")
    relevance = {item.object_id: item.task_relevance for item in semantic.object_relevance}
    objects = {obj.object_id: obj for obj in geometry.objects}
    _require(bool(relevance) and set(candidates) == set(relevance) and
             all(objects.get(key) == value for key, value in candidates.items()),
             "candidate_coverage", "candidates must exactly cover nonempty semantics and match geometry")
    keypoints = {point.keypoint_id: point for point in geometry.keypoints}
    mappings = dict(config.approach_keypoints)
    for key in candidates:
        _require(key in mappings and mappings[key] in keypoints and keypoints[mappings[key]].object_id == key,
                 "approach_mapping", f"missing or foreign approach keypoint for {key}")

    _require(len(bundle.programs) <= 16, "program_coverage", "profile permits at most 16 programs")
    programs = []
    for item in bundle.programs:
        _require(type(item) is ResolvedProgram, "input_type", "expected ResolvedProgram")
        program = _record(r.ConstraintProgram, item.program, policy)
        _require(sha256(program.source_code.encode("utf-8")).hexdigest() == program.source_sha256,
                 "program_reference", "source hash mismatch")
        _require(item.geometry == geometry and item.candidate == candidates.get(program.object_id),
                 "program_reference", "program geometry/candidate mismatch")
        axes = {axis.axis_id: axis for axis in geometry.axes}
        _require(all(key in keypoints for key in program.keypoint_bindings) and
                 all(key in axes for key in program.axis_bindings),
                 "program_reference", "missing program feature binding")
        _require(item.keypoints == tuple(keypoints[key].position_xyz for key in program.keypoint_bindings) and
                 item.axis_dirs == tuple(axes[key].unit_direction_xyz for key in program.axis_bindings) and
                 item.params == tuple(param.value for param in program.parameters),
                 "program_reference", "resolved numerical bindings differ from artifacts")
        programs.append(program)
    by_program = _unique(programs, lambda item: item.program_id, "program IDs")
    refs = {r.ProgramRef(item.program_id, item.source_sha256) for item in programs}
    _require(refs == set(snapshot.program_refs) == set(semantic.program_refs) and
             {item.object_id for item in programs} == set(candidates),
             "program_coverage", "each candidate requires its pinned compatible programs")
    bindings = tuple(_record(r.ProgramExecutionBinding, item, policy) for item in bundle.execution_bindings)
    by_binding = _unique(bindings, lambda item: item.binding_id, "binding IDs")
    binding_programs = _unique(bindings, lambda item: item.program_id, "binding program IDs")
    _require(set(by_binding) == set(snapshot.execution_binding_refs) and set(binding_programs) == set(by_program),
             "binding_coverage", "exactly one pinned binding is required per program")
    receipts = tuple(_record(r.ProgramValidationReceipt, item, policy) for item in bundle.receipts)
    by_receipt = _unique(receipts, lambda item: item.receipt_id, "receipt IDs")
    _require(set(by_receipt) == {item.validation_receipt_id for item in bindings},
             "binding_coverage", "missing or extra receipt")
    for binding in bindings:
        program, receipt = by_program[binding.program_id], by_receipt[binding.validation_receipt_id]
        _require(binding.config_id == session.config_id and receipt.status == "accepted" and
                 (receipt.program_id, receipt.source_sha256, receipt.function_api_version) ==
                 (program.program_id, program.source_sha256, program.function_api_version) and
                 (receipt.scene_id, receipt.feature_set_id, receipt.validator_policy_version) ==
                 (geometry.scene_id, geometry.feature_set_id, policy.policy_id),
                 "binding_reference", "incompatible program binding or receipt")
    current = _message(resolved.joystick, "JoystickSample", bundle.session_id, policy)
    state = _message(resolved.robot_state, "RobotState", bundle.session_id, policy)
    _require(current.meta.message_id == snapshot.joystick_ref and state.meta.message_id == snapshot.robot_state_ref and
             state.payload.frame_id == session.control_frame and state.payload.tcp_frame == session.tcp_frame,
             "input_reference", "live input IDs or measured-state frames differ from snapshot")
    _require(len(resolved.joystick_history) <= config.history_limit, "invalid_history", "more than 32 earlier samples")
    history = tuple(_message(item, "JoystickSample", bundle.session_id, policy) for item in resolved.joystick_history)
    _require(tuple(item.meta.message_id for item in history) == snapshot.joystick_history_refs,
             "invalid_history", "history does not match exact ordered snapshot references")
    stamp = lambda sample: sample.payload.sampled_at.sec * 1_000_000_000 + sample.payload.sampled_at.nanosec
    now, previous_time, previous_sequence = stamp(current), None, None
    for item in history:
        _require(item.payload.mapping_id == current.payload.mapping_id and
                 item.payload.sampled_at.clock_id == current.payload.sampled_at.clock_id and
                 item.meta.source_id == current.meta.source_id,
                 "invalid_history", "incomparable history mapping, clock or source")
        time = stamp(item)
        _require(0 <= now - time <= 1_000_000_000 and item.meta.sequence < current.meta.sequence and
                 (previous_time is None or time >= previous_time) and
                 (previous_sequence is None or item.meta.sequence > previous_sequence),
                 "invalid_history", "history must be earlier, ordered, unique and within one second")
        previous_time, previous_sequence = time, item.meta.sequence
    return snapshot, current.payload, state.payload, history, relevance, keypoints


def _direction(vector, epsilon=0.0):
    length = hypot(*vector)
    _require(isfinite(length), "nonfinite_computation", "direction norm is nonfinite")
    if length <= epsilon:
        return (0.0, 0.0)
    # Scale before normalization: a subnormal norm can lose most of its
    # significant bits even though the input is valid and explicitly nonzero.
    scale = max(abs(value) for value in vector)
    scaled = tuple(value / scale for value in vector)
    norm = hypot(*scaled)
    return tuple(value / norm for value in scaled)


def estimate_intent(resolved: ResolvedSnapshot, configuration: IntentConfig, *,
                    weights_id: str, policy: ValidationPolicy) -> IntentOutcome:
    """Estimate once from detached snapshot values; identical inputs replay identically.

    Invalid inputs/configuration or arithmetic return error with no weights.
    Neutral/disabled valid inputs return diagnostic weights, alpha=0 and disabled.
    active (including alpha-zero) never authorizes a command or verifies freshness.
    """
    try:
        snapshot, current, state, history, relevance, keypoints = _validate(resolved, configuration, policy)
        active = current.enabled and any(value != 0 for value in current.u_xy)
        direction = _direction(current.u_xy) if active else (0.0, 0.0)
        earlier = [_direction(item.payload.u_xy) for item in history
                   if item.payload.enabled and any(value != 0 for value in item.payload.u_xy)]
        mean = tuple(fsum(item[axis] for item in earlier) / len(earlier) for axis in range(2)) if earlier else (0.0, 0.0)
        mappings, scores = dict(configuration.approach_keypoints), []
        for object_id in sorted(relevance):
            point = keypoints[mappings[object_id]].position_xyz
            delta = tuple(float(point[axis]) - float(state.tcp_pose.position_xyz[axis]) for axis in range(2))
            target = _direction(delta, configuration.direction_epsilon_m)
            current_support = (1.0 + fsum(a * b for a, b in zip(direction, target))) / 2.0
            history_support = (1.0 + fsum(a * b for a, b in zip(mean, target))) / 2.0
            score = (configuration.score_floor + configuration.relevance_coefficient * relevance[object_id] +
                     configuration.current_coefficient * current_support + configuration.history_coefficient * history_support)
            _require(isfinite(score) and score > 0, "nonfinite_computation", "invalid candidate support")
            scores.append((object_id, score))
        total = fsum(score for _, score in scores)
        weights = parse_record(r.ObjectWeights, {
            "weights_id": weights_id, "snapshot_id": snapshot.snapshot_id,
            "estimator_config_id": configuration.estimator_config_id,
            "weights": [{"object_id": key, "weight": score / total} for key, score in scores],
            "assistance_strength": configuration.alpha_active if active else 0.0,
        }, policy=policy)
        return IntentOutcome("active" if active else "disabled", weights,
                             "ok" if active else "input_disabled", "" if active else "no motion solve; diagnostic weights only")
    except (ValidationError, _Invalid) as exc:
        return IntentOutcome("error", None, exc.code, str(exc))
    except (TypeError, ValueError, AttributeError, OverflowError) as exc:
        return IntentOutcome("error", None, "invalid_input", str(exc))
