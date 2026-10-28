"""Session-local immutable records and pure cross-record compatibility checks.

Resolution establishes typed-record compatibility, never bundle activation,
input freshness or motion authority. Candidate selection is explicit and replayable.
Opaque resources without a record schema remain the caller's responsibility.
"""

from dataclasses import dataclass
from typing import Any, TypeVar

from . import records as r
from .policy import ValidationPolicy
from .validation import parse_message, parse_record, to_dict


class RegistryError(ValueError):
    """Identity/reference failure with a stable code and a field path."""

    def __init__(self, path: str, code: str, message: str) -> None:
        self.path, self.code, self.message = path, code, message
        super().__init__(f"{path}: {code}: {message}")


@dataclass(frozen=True, slots=True)
class ResolvedBundle:
    session_id: str
    configuration: r.SessionConfig
    task: r.TaskContext
    geometry: r.SceneGeometry
    semantic: r.SemanticContext
    candidate_set: r.CandidateSet

    @property
    def candidates(self) -> tuple[r.CandidatePoint, ...]:
        return self.candidate_set.candidates


@dataclass(frozen=True, slots=True)
class ResolvedSnapshot:
    snapshot: r.ControlSnapshot
    bundle: ResolvedBundle
    joystick: r.Message
    robot_state: r.Message
    joystick_history: tuple[r.Message, ...]
    previous_selection: r.CandidateSelection | None = None
    previous_selection_snapshot: r.ControlSnapshot | None = None


@dataclass(frozen=True, slots=True)
class ResolvedSelection:
    selection: r.CandidateSelection
    snapshot: ResolvedSnapshot
    candidate: r.CandidatePoint | None


_IDENTITIES = {
    r.SessionConfig: "config_id", r.TaskContext: "task_id",
    r.SceneObservation: "observation_id", r.SceneGeometry: "geometry_id",
    r.SemanticContext: "semantic_id", r.CandidateSet: "candidate_set_id",
    r.ControlSnapshot: "snapshot_id", r.CandidateSelection: "selection_id",
    r.ControlResult: "result_id",
}
_TYPES = {cls.__name__: cls for cls in _IDENTITIES}
_R = TypeVar("_R", bound=r.Record)


def _fail(path: str, code: str, message: str) -> None:
    raise RegistryError(path, code, message)


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        _fail(path, "invalid_reference", "expected a nonempty string ID")
    return value


def _same(actual: Any, expected: Any, path: str) -> None:
    if actual != expected:
        _fail(path, "incompatible_reference", f"expected {expected!r}, got {actual!r}")


def _canonical(value: Any) -> tuple:
    """Compare JSON-shaped values without limiting TimePoint's integer seconds.

    Decimal JSON encoding can reject large valid integers. A typed tree keeps
    exact integers, number types and signed zero without global decoder changes.
    """
    if type(value) is dict:
        return ("object", tuple((key, _canonical(item)) for key, item in sorted(value.items())))
    if type(value) in (list, tuple):
        return ("array", tuple(_canonical(item) for item in value))
    if type(value) is float:
        return ("float", value.hex())
    return (type(value).__name__, value)


def _content(record: r.Record) -> tuple:
    value = to_dict(record)
    if type(record) is r.CandidateSet:
        # Candidate sets are unordered. The same stable IDs and values remain
        # identical when a producer changes JSON array order.
        value["candidates"].sort(key=lambda item: item["candidate_id"])
    return _canonical(value)


def _definition(geometry: r.SceneGeometry) -> tuple:
    # Coordinates may change, while observable feature definitions may not.
    # Labels/descriptions do not establish physical feature meaning.
    return (
        tuple(sorted(obj.object_id for obj in geometry.objects)),
        tuple(sorted((kp.keypoint_id, kp.object_id) for kp in geometry.keypoints)),
        tuple(sorted((a.axis_id, a.object_id, a.origin_keypoint_id, a.directed)
                     for a in geometry.axes)),
    )


def _coordinates(geometry: r.SceneGeometry) -> tuple:
    """Geometry version values, independent of storage order or provenance ID."""
    return _canonical((
        geometry.frame_id,
        sorted((kp.keypoint_id, kp.position_xyz) for kp in geometry.keypoints),
        sorted((axis.axis_id, axis.unit_direction_xyz) for axis in geometry.axes),
    ))


class SessionRegistry:
    """One in-memory session; callers serialize access to this mutable registry.

    Standalone artifacts require an explicit session ID at registration and
    lookup. Envelope session IDs are checked without relabeling. No registry
    fallback or external-resource exception is implemented. Dependencies may be
    registered in any order; resolution fails until the typed graph is complete.
    """

    def __init__(self, session_id: str, *, policy: ValidationPolicy) -> None:
        self._session_id = _identifier(session_id, "$.session_id")
        if type(policy) is not ValidationPolicy:
            raise TypeError("policy must be an explicit ValidationPolicy")
        self._policy = policy
        self._records: dict[tuple[type, str | tuple[str, int]], r.Record] = {}
        self._messages: dict[str, r.Message] = {}
        self._definitions: dict[tuple[str, str], tuple] = {}
        self._coordinate_versions: dict[tuple[str, str, int], tuple] = {}

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def policy(self) -> ValidationPolicy:
        return self._policy

    def _session(self, session_id: str) -> None:
        _identifier(session_id, "$.session_id")
        if session_id != self.session_id:
            _fail("$.session_id", "session_mismatch", "reference belongs to another session")

    def _parse(self, cls: type[_R], value: dict | _R) -> _R:
        if isinstance(value, r.Record):
            if type(value) is not cls:
                _fail("$", "invalid_reference", f"expected {cls.__name__}")
            value = to_dict(value)
        return parse_record(cls, value, policy=self.policy)

    @staticmethod
    def _type(record_type: str | type[_R]) -> type[_R]:
        cls = _TYPES.get(record_type) if type(record_type) is str else record_type
        if not isinstance(cls, type) or cls not in _IDENTITIES:
            _fail("$", "unsupported_record", "record has no supported immutable artifact identity")
        return cls

    @staticmethod
    def _key(record: r.Record) -> tuple:
        identity = getattr(record, _IDENTITIES[type(record)])
        if type(record) is r.TaskContext:
            identity = (identity, record.revision)
        return type(record), identity

    def _check_record(self, record: r.Record) -> None:
        previous = self._records.get(self._key(record))
        if previous is not None and _content(previous) != _content(record):
            _fail(f"$.{_IDENTITIES[type(record)]}", "immutable_conflict",
                  "immutable identity is already registered with different content")
        if type(record) is r.SceneGeometry:
            known = self._definitions.get((record.scene_id, record.feature_set_id))
            if known is not None and known != _definition(record):
                _fail("$.feature_set_id", "feature_definition_mismatch",
                      "changed feature membership, ownership, origin or directedness requires a new feature set")
            known_values = self._coordinate_versions.get((record.scene_id, record.feature_set_id, record.revision))
            if known_values is not None and known_values != _coordinates(record):
                _fail("$.revision", "geometry_revision_conflict",
                      "changed coordinates or frame within a feature set require a new geometry revision")

    def _store_record(self, record: r.Record) -> None:
        self._records[self._key(record)] = record
        if type(record) is r.SceneGeometry:
            self._definitions[(record.scene_id, record.feature_set_id)] = _definition(record)
            self._coordinate_versions[(record.scene_id, record.feature_set_id, record.revision)] = _coordinates(record)

    def register_record(
        self, record_type: str | type[_R], data: dict | _R, *, session_id: str,
    ) -> _R:
        """Validate/copy a standalone artifact, rejecting changed immutable IDs."""
        self._session(session_id)
        record = self._parse(self._type(record_type), data)
        self._check_record(record)
        self._store_record(record)
        return self._parse(type(record), record)

    def register_message(self, data: dict | r.Message) -> r.Message:
        """Atomically register the entire envelope and any identifiable payload."""
        message = parse_message(to_dict(data) if type(data) is r.Message else data, policy=self.policy)
        self._session(message.meta.session_id)
        previous = self._messages.get(message.meta.message_id)
        if previous is not None and _content(previous) != _content(message):
            _fail("$.meta.message_id", "immutable_conflict", "message ID already has different content")
        identified = type(message.payload) in _IDENTITIES
        if identified:
            self._check_record(message.payload)
        # All rejecting checks precede either index write.
        if identified:
            self._store_record(message.payload)
        self._messages[message.meta.message_id] = message
        return parse_message(to_dict(message), policy=self.policy)

    def get_record(
        self, record_type: str | type[_R], identity: str | r.TaskRef | dict, *, session_id: str,
    ) -> _R:
        """Detached exact lookup. TaskContext requires a complete TaskRef."""
        self._session(session_id)
        cls = self._type(record_type)
        if cls is r.TaskContext:
            ref = self._parse(r.TaskRef, identity)
            key = (ref.task_id, ref.revision)
        else:
            key = _identifier(identity, "$.reference")
        record = self._records.get((cls, key))
        if record is None:
            _fail("$.reference", "missing_reference", f"{cls.__name__} {key!r} is absent in this session")
        return self._parse(cls, record)

    def get_message(self, message_id: str, *, session_id: str) -> r.Message:
        self._session(session_id)
        message_id = _identifier(message_id, "$.message_id")
        message = self._messages.get(message_id)
        if message is None:
            _fail("$.message_id", "missing_reference", f"message {message_id!r} is absent in this session")
        return parse_message(to_dict(message), policy=self.policy)

    def _get(self, cls: type[_R], identity: Any) -> _R:
        return self.get_record(cls, identity, session_id=self.session_id)

    def _configuration(self, config_id: str) -> r.SessionConfig:
        config = self._get(r.SessionConfig, config_id)
        _same(config.validation_policy_id, self.policy.policy_id, "$.config.validation_policy_id")
        return config

    def _observation(self, observation_id: str, scene_id: str, config: r.SessionConfig) -> r.SceneObservation:
        observation = self._get(r.SceneObservation, observation_id)
        _same(observation.scene_id, scene_id, "$.observation.scene_id")
        _same(observation.T_control_camera.parent_frame, config.control_frame,
              "$.observation.T_control_camera.parent_frame")
        # Integer nanoseconds avoid floating timestamp cancellation at large epochs.
        skew_ns = abs((observation.rgb_stamp.sec - observation.depth_stamp.sec) * 1_000_000_000
                      + observation.rgb_stamp.nanosec - observation.depth_stamp.nanosec)
        if skew_ns > config.max_capture_skew_s * 1_000_000_000:
            _fail("$.observation.depth_stamp", "incompatible_reference", "capture skew exceeds configuration")
        return observation

    def _geometry(self, geometry_ref: r.GeometryRef | dict, config: r.SessionConfig) -> r.SceneGeometry:
        ref = self._parse(r.GeometryRef, geometry_ref)
        geometry = self._get(r.SceneGeometry, ref.geometry_id)
        _same(geometry.revision, ref.revision, "$.geometry_ref.revision")
        _same(geometry.frame_id, config.control_frame, "$.geometry.frame_id")
        self._observation(geometry.observation_id, geometry.scene_id, config)
        return geometry

    def resolve_bundle(
        self, *, config_id: str, task_ref: r.TaskRef | dict, geometry_ref: r.GeometryRef | dict,
        semantic_id: str, candidate_set_id: str,
    ) -> ResolvedBundle:
        """Resolve one compatible data-only graph, without granting motion authority."""
        config = self._configuration(config_id)
        task = self._get(r.TaskContext, self._parse(r.TaskRef, task_ref))
        geometry = self._geometry(geometry_ref, config)
        semantic = self._get(r.SemanticContext, semantic_id)
        candidates = self._get(r.CandidateSet, candidate_set_id)
        for record, prefix in ((semantic, "semantic"), (candidates, "candidate_set")):
            for field, expected in (("task_id", task.task_id), ("task_revision", task.revision),
                                    ("scene_id", geometry.scene_id), ("feature_set_id", geometry.feature_set_id)):
                _same(getattr(record, field), expected, f"$.{prefix}.{field}")
            self._observation(record.observation_id, geometry.scene_id, config)
        _same(candidates.frame_id, config.control_frame, "$.candidate_set.frame_id")
        _same(candidates.observation_id, geometry.observation_id, "$.candidate_set.observation_id")
        objects = {obj.object_id for obj in geometry.objects}
        relevance = {item.object_id for item in semantic.object_relevance}
        owners = {item.object_id for item in candidates.candidates}
        if owners != relevance or not owners <= objects:
            _fail("$.candidate_set.candidates", "candidate_coverage",
                  "candidate owners must exactly cover semantics and exist in geometry")
        return ResolvedBundle(self.session_id, config, task, geometry, semantic, candidates)

    def _input(self, message_id: str, cls: type[_R]) -> r.Message:
        message = self.get_message(message_id, session_id=self.session_id)
        _same(message.message_type, cls.__name__, "$.message_type")
        return message

    def resolve_snapshot(self, snapshot_id: str) -> ResolvedSnapshot:
        """Resolve an existing snapshot, without creating or dispatching one.

        History order, time bounds and applied-command provenance remain assembler
        responsibilities. Only typed input references and frame compatibility are
        checked here; a resolved snapshot is not permission to compute or move.
        """
        snapshot = self._get(r.ControlSnapshot, snapshot_id)
        bundle = self.resolve_bundle(
            config_id=snapshot.config_id, task_ref=snapshot.task_ref, geometry_ref=snapshot.geometry_ref,
            semantic_id=snapshot.semantic_id, candidate_set_id=snapshot.candidate_set_id,
        )
        joystick = self._input(snapshot.joystick_ref, r.JoystickSample)
        state = self._input(snapshot.robot_state_ref, r.RobotState)
        _same(state.payload.frame_id, bundle.configuration.control_frame, "$.robot_state.frame_id")
        _same(state.payload.tcp_frame, bundle.configuration.tcp_frame, "$.robot_state.tcp_frame")
        history = tuple(self._input(ref, r.JoystickSample) for ref in snapshot.joystick_history_refs)
        for sample in history:
            _same(sample.payload.mapping_id, joystick.payload.mapping_id, "$.joystick_history.mapping_id")
            _same(sample.payload.sampled_at.clock_id, joystick.payload.sampled_at.clock_id,
                  "$.joystick_history.sampled_at.clock_id")
        previous = origin = None
        if snapshot.previous_selection_id is not None:
            previous = self._get(r.CandidateSelection, snapshot.previous_selection_id)
            origin = self._get(r.ControlSnapshot, previous.snapshot_id)
            for field in ("control_epoch", "config_id", "task_ref", "geometry_ref", "semantic_id", "candidate_set_id"):
                _same(getattr(origin, field), getattr(snapshot, field), f"$.previous_selection.{field}")
            _same(previous.candidate_set_id, snapshot.candidate_set_id, "$.previous_selection.candidate_set_id")
            if origin.snapshot_seq >= snapshot.snapshot_seq:
                _fail("$.previous_selection_id", "selection_lineage", "previous selection must precede current snapshot")
            candidate_ids = {item.candidate_id for item in bundle.candidates}
            if {item.candidate_id for item in previous.scores} != candidate_ids:
                _fail("$.previous_selection.scores", "candidate_coverage", "previous scores differ from candidates")
        return ResolvedSnapshot(snapshot, bundle, joystick, state, history, previous, origin)

    def resolve_selection(self, selection_id: str) -> ResolvedSelection:
        """Resolve a diagnostic selection and its exact causal snapshot."""
        selection = self._get(r.CandidateSelection, selection_id)
        resolved = self.resolve_snapshot(selection.snapshot_id)
        _same(selection.candidate_set_id, resolved.snapshot.candidate_set_id, "$.selection.candidate_set_id")
        candidates = {item.candidate_id: item for item in resolved.bundle.candidates}
        if set(candidates) != {item.candidate_id for item in selection.scores}:
            _fail("$.selection.scores", "candidate_coverage", "scores must exactly cover candidates")
        return ResolvedSelection(selection, resolved, candidates.get(selection.selected_candidate_id))
