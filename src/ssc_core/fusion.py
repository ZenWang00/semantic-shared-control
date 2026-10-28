"""Pure deterministic selection of one explicit TCP candidate per snapshot."""

from dataclasses import dataclass, fields
from math import fsum, hypot, isfinite
from typing import Literal

from . import records as r
from .errors import ValidationError
from .policy import NUMERIC_CANDIDATES_V2, ValidationPolicy
from .registry import ResolvedBundle, ResolvedSnapshot
from .validation import parse_message, parse_record, to_dict


@dataclass(frozen=True, slots=True)
class IntentConfig:
    estimator_config_id: str
    session_config_id: str
    alpha_active: float
    history_limit: int = 32
    history_window_s: float = 1.0
    direction_epsilon_m: float = 1e-12
    score_floor: float = 0.25
    relevance_coefficient: float = 1.0
    current_coefficient: float = 2.0
    history_coefficient: float = 0.5
    switch_threshold: float = 0.1


def get_intent_config(estimator_config_id: str) -> IntentConfig:
    if estimator_config_id == "intent-candidates-v2":
        return IntentConfig(estimator_config_id, "config-candidates-v2", 0.5)
    if estimator_config_id == "intent-candidates-alpha0-v2":
        return IntentConfig(estimator_config_id, "config-candidates-alpha0-v2", 0.0)
    raise ValueError(f"unsupported estimator configuration: {estimator_config_id!r}")


@dataclass(frozen=True, slots=True)
class IntentOutcome:
    disposition: Literal["active", "disabled", "error"]
    selection: r.CandidateSelection | None
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


def _validate(resolved, config, policy):
    _require(type(policy) is ValidationPolicy and policy == NUMERIC_CANDIDATES_V2,
             "invalid_policy", "candidate selection requires numeric-candidates-v2")
    _require(type(config) is IntentConfig, "invalid_config", "expected IntentConfig")
    expected = get_intent_config(config.estimator_config_id)
    _require(config == expected and all(type(getattr(config, field.name)) is type(getattr(expected, field.name))
                                      for field in fields(expected)),
             "invalid_config", "configuration differs from its immutable definition")
    _require(type(resolved) is ResolvedSnapshot and type(resolved.bundle) is ResolvedBundle,
             "input_type", "expected detached resolved snapshot")
    bundle = resolved.bundle
    snapshot = _record(r.ControlSnapshot, resolved.snapshot, policy)
    session = _record(r.SessionConfig, bundle.configuration, policy)
    task = _record(r.TaskContext, bundle.task, policy)
    geometry = _record(r.SceneGeometry, bundle.geometry, policy)
    semantic = _record(r.SemanticContext, bundle.semantic, policy)
    candidates = _record(r.CandidateSet, bundle.candidate_set, policy)
    _require(snapshot.config_id == session.config_id == config.session_config_id and
             session.validation_policy_id == policy.policy_id and
             session.history_window_s == config.history_window_s,
             "config_reference", "snapshot/session/estimator configuration mismatch")
    _require(snapshot.task_ref == r.TaskRef(task.task_id, task.revision) and
             snapshot.geometry_ref == r.GeometryRef(geometry.geometry_id, geometry.revision) and
             snapshot.semantic_id == semantic.semantic_id and snapshot.candidate_set_id == candidates.candidate_set_id,
             "snapshot_reference", "snapshot has incompatible artifact references")
    for item in (semantic, candidates):
        _require((item.task_id, item.task_revision, item.scene_id, item.feature_set_id) ==
                 (task.task_id, task.revision, geometry.scene_id, geometry.feature_set_id),
                 "snapshot_reference", "task/scene/feature context differs")
    _require(candidates.observation_id == geometry.observation_id and
             candidates.frame_id == geometry.frame_id == session.control_frame,
             "snapshot_reference", "candidate observation or coordinate frame differs")
    relevance = {item.object_id: item.task_relevance for item in semantic.object_relevance}
    _require({point.object_id for point in candidates.candidates} == set(relevance) and
             set(relevance) <= {item.object_id for item in geometry.objects},
             "candidate_coverage", "candidate owners must exactly cover semantic objects")
    previous, origin = resolved.previous_selection, resolved.previous_selection_snapshot
    if snapshot.previous_selection_id is None:
        _require(previous is None and origin is None, "selection_lineage", "unreferenced previous selection")
    else:
        previous = _record(r.CandidateSelection, previous, policy)
        origin = _record(r.ControlSnapshot, origin, policy)
        _require(previous.selection_id == snapshot.previous_selection_id and previous.snapshot_id == origin.snapshot_id and
                 previous.candidate_set_id == snapshot.candidate_set_id and
                 previous.estimator_config_id == config.estimator_config_id and origin.snapshot_seq < snapshot.snapshot_seq and
                 all(getattr(origin, key) == getattr(snapshot, key) for key in
                     ("control_epoch", "config_id", "task_ref", "geometry_ref", "semantic_id", "candidate_set_id")) and
                 {item.candidate_id for item in previous.scores} == {point.candidate_id for point in candidates.candidates},
                 "selection_lineage", "previous selection belongs to another context or a later snapshot")
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
    return snapshot, current.payload, state.payload, history, relevance


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
                    selection_id: str, policy: ValidationPolicy) -> IntentOutcome:
    """Rank candidates, retaining a prior selection unless another wins by >0.1.

    No hidden state is consulted. A neutral input emits a null selection; the
    runtime clears the pinned predecessor on any release or invalidation.
    """
    try:
        snapshot, current, state, history, relevance = _validate(resolved, configuration, policy)
        active = current.enabled and any(value != 0 for value in current.u_xy)
        direction = _direction(current.u_xy) if active else (0.0, 0.0)
        earlier = [_direction(item.payload.u_xy) for item in history
                   if item.payload.enabled and any(value != 0 for value in item.payload.u_xy)]
        mean = tuple(fsum(item[axis] for item in earlier) / len(earlier) for axis in range(2)) if earlier else (0.0, 0.0)
        scores = []
        for point in sorted(resolved.bundle.candidates, key=lambda item: item.candidate_id):
            delta = tuple(float(point.position_xyz[axis]) - float(state.tcp_pose.position_xyz[axis]) for axis in range(3))
            target = _direction(delta[:2], configuration.direction_epsilon_m)
            current_support = (1.0 + fsum(a * b for a, b in zip(direction, target))) / 2.0
            history_support = (1.0 + fsum(a * b for a, b in zip(mean, target))) / 2.0
            score = (configuration.score_floor + configuration.relevance_coefficient * relevance[point.object_id] +
                     configuration.current_coefficient * current_support + configuration.history_coefficient * history_support)
            distance = hypot(*delta)
            _require(isfinite(score) and isfinite(distance), "nonfinite_computation", "invalid candidate support/distance")
            scores.append(r.CandidateScore(point.candidate_id, score, distance))
        best = min(scores, key=lambda item: (-item.score, item.distance_m, item.candidate_id))
        selected = best.candidate_id if active else None
        previous = resolved.previous_selection
        if active and previous is not None and previous.selected_candidate_id is not None:
            incumbent = next(item for item in scores if item.candidate_id == previous.selected_candidate_id)
            # Addition avoids cancellation in (new - old) around the exact threshold.
            if best.score <= incumbent.score + configuration.switch_threshold:
                selected = incumbent.candidate_id
        selection = _record(r.CandidateSelection, r.CandidateSelection(
            selection_id, snapshot.snapshot_id, configuration.estimator_config_id,
            snapshot.candidate_set_id, selected, tuple(scores), configuration.alpha_active if active else 0.0,
        ), policy)
        return IntentOutcome("active" if active else "disabled", selection,
                             "ok" if active else "input_disabled", "" if active else "input released; selection cleared")
    except (ValidationError, _Invalid) as exc:
        return IntentOutcome("error", None, exc.code, str(exc))
    except (TypeError, ValueError, AttributeError, OverflowError) as exc:
        return IntentOutcome("error", None, "invalid_input", str(exc))
