"""Session-local immutable records and pure cross-record compatibility checks.

Resolution establishes typed-record compatibility, never receipt trust, bundle
activation, input freshness or motion authority. No source code is executed.
Opaque resources without a record schema remain the caller's responsibility.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
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
class ResolvedProgram:
    program: r.ConstraintProgram
    geometry: r.SceneGeometry
    candidate: r.Object
    keypoints: tuple[r.Vector3, ...]
    axis_dirs: tuple[r.Vector3, ...]
    params: tuple[r.Number, ...]


@dataclass(frozen=True, slots=True)
class ResolvedBundle:
    session_id: str
    configuration: r.SessionConfig
    task: r.TaskContext
    geometry: r.SceneGeometry
    semantic: r.SemanticContext
    candidates: tuple[r.Object, ...]
    programs: tuple[ResolvedProgram, ...]
    execution_bindings: tuple[r.ProgramExecutionBinding, ...]
    receipts: tuple[r.ProgramValidationReceipt, ...]


@dataclass(frozen=True, slots=True)
class ResolvedSnapshot:
    snapshot: r.ControlSnapshot
    bundle: ResolvedBundle
    joystick: r.Message
    robot_state: r.Message
    joystick_history: tuple[r.Message, ...]


@dataclass(frozen=True, slots=True)
class ResolvedWeights:
    weights: r.ObjectWeights
    snapshot: ResolvedSnapshot
    candidate_weights: tuple[r.ObjectWeight, ...]


_IDENTITIES = {
    r.SessionConfig: "config_id", r.TaskContext: "task_id",
    r.SceneObservation: "observation_id", r.SceneGeometry: "geometry_id",
    r.SemanticContext: "semantic_id", r.ConstraintProgram: "program_id",
    r.ProgramExecutionBinding: "binding_id", r.ProgramValidationReceipt: "receipt_id",
    r.ControlSnapshot: "snapshot_id", r.ObjectWeights: "weights_id",
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


def _content(record: r.Record) -> str:
    # Dict insertion order is not content; array order and exact number forms are.
    return json.dumps(to_dict(record), sort_keys=True, ensure_ascii=True, allow_nan=False)


def _definition(geometry: r.SceneGeometry) -> tuple:
    # Coordinates may change, while observable feature definitions may not.
    # Labels/descriptions do not establish physical feature meaning.
    return (
        tuple(sorted(obj.object_id for obj in geometry.objects)),
        tuple(sorted((kp.keypoint_id, kp.object_id) for kp in geometry.keypoints)),
        tuple(sorted((a.axis_id, a.object_id, a.origin_keypoint_id, a.directed)
                     for a in geometry.axes)),
    )


def _coordinates(geometry: r.SceneGeometry) -> str:
    """Geometry version values, independent of storage order or provenance ID."""
    return json.dumps((
        geometry.frame_id,
        sorted((kp.keypoint_id, kp.position_xyz) for kp in geometry.keypoints),
        sorted((axis.axis_id, axis.unit_direction_xyz) for axis in geometry.axes),
    ), ensure_ascii=True, allow_nan=False)


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
        self._coordinate_versions: dict[tuple[str, str, int], str] = {}
        self._execution_settings: dict[tuple[str, str], str] = {}

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
        if type(record) is r.ConstraintProgram:
            digest = sha256(record.source_code.encode("utf-8")).hexdigest()
            if digest != record.source_sha256:
                _fail("$.source_sha256", "source_hash_mismatch", "hash differs from exact UTF-8 source")
        if type(record) is r.SceneGeometry:
            known = self._definitions.get((record.scene_id, record.feature_set_id))
            if known is not None and known != _definition(record):
                _fail("$.feature_set_id", "feature_definition_mismatch",
                      "changed feature membership, ownership, origin or directedness requires a new feature set")
            known_values = self._coordinate_versions.get((record.scene_id, record.feature_set_id, record.revision))
            if known_values is not None and known_values != _coordinates(record):
                _fail("$.revision", "geometry_revision_conflict",
                      "changed coordinates or frame within a feature set require a new geometry revision")
        if type(record) is r.ProgramExecutionBinding:
            known_settings = self._execution_settings.get((record.config_id, record.program_id))
            settings = json.dumps((record.normalization_scale, record.weight), allow_nan=False)
            if known_settings is not None and known_settings != settings:
                _fail("$.config_id", "binding_configuration_conflict",
                      "changed scale or weight for a program requires a new configuration")

    def _store_record(self, record: r.Record) -> None:
        self._records[self._key(record)] = record
        if type(record) is r.SceneGeometry:
            self._definitions[(record.scene_id, record.feature_set_id)] = _definition(record)
            self._coordinate_versions[(record.scene_id, record.feature_set_id, record.revision)] = _coordinates(record)
        if type(record) is r.ProgramExecutionBinding:
            self._execution_settings[(record.config_id, record.program_id)] = json.dumps(
                (record.normalization_scale, record.weight), allow_nan=False)

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

    def _program(self, program_ref: r.ProgramRef | dict, geometry: r.SceneGeometry) -> ResolvedProgram:
        ref = self._parse(r.ProgramRef, program_ref)
        program = self._get(r.ConstraintProgram, ref.program_id)
        _same(program.source_sha256, ref.source_sha256, "$.program_ref.source_sha256")
        objects = {obj.object_id: obj for obj in geometry.objects}
        keypoints = {kp.keypoint_id: kp for kp in geometry.keypoints}
        axes = {axis.axis_id: axis for axis in geometry.axes}
        if program.object_id not in objects:
            _fail("$.program.object_id", "missing_reference", "program candidate is absent from geometry")
        for field, ids, available in (("keypoint_bindings", program.keypoint_bindings, keypoints),
                                      ("axis_bindings", program.axis_bindings, axes)):
            for index, feature_id in enumerate(ids):
                if feature_id not in available:
                    _fail(f"$.program.{field}[{index}]", "missing_reference",
                          f"feature {feature_id!r} is absent from geometry")
        return ResolvedProgram(
            program, geometry, objects[program.object_id],
            tuple(keypoints[key].position_xyz for key in program.keypoint_bindings),
            tuple(axes[key].unit_direction_xyz for key in program.axis_bindings),
            tuple(parameter.value for parameter in program.parameters),
        )

    def resolve_program(
        self, program_ref: r.ProgramRef | dict, *, geometry_ref: r.GeometryRef | dict, config_id: str,
    ) -> ResolvedProgram:
        """Resolve ordered numeric-source tuples, including for later validation probes."""
        return self._program(program_ref, self._geometry(geometry_ref, self._configuration(config_id)))

    def _receipt(
        self, receipt_id: str, program: r.ConstraintProgram, geometry: r.SceneGeometry,
        config: r.SessionConfig,
    ) -> r.ProgramValidationReceipt:
        receipt = self._get(r.ProgramValidationReceipt, receipt_id)
        for field, expected in (
            ("program_id", program.program_id), ("source_sha256", program.source_sha256),
            ("function_api_version", program.function_api_version),
            ("scene_id", geometry.scene_id), ("feature_set_id", geometry.feature_set_id),
            ("validator_policy_version", self.policy.policy_id), ("status", "accepted"),
        ):
            _same(getattr(receipt, field), expected, f"$.receipt.{field}")
        provenance = self._geometry(r.GeometryRef(receipt.validation_geometry_id,
                                                  receipt.validation_geometry_revision), config)
        _same(provenance.scene_id, geometry.scene_id, "$.receipt.validation_geometry.scene_id")
        _same(provenance.feature_set_id, geometry.feature_set_id, "$.receipt.validation_geometry.feature_set_id")
        return receipt

    def resolve_bundle(
        self, *, config_id: str, task_ref: r.TaskRef | dict, geometry_ref: r.GeometryRef | dict,
        semantic_id: str, program_refs: tuple[r.ProgramRef | dict, ...] | list,
        execution_binding_refs: tuple[str, ...] | list[str],
    ) -> ResolvedBundle:
        """Resolve one compatible typed graph; returned receipts remain untrusted."""
        config = self._configuration(config_id)
        task = self._get(r.TaskContext, self._parse(r.TaskRef, task_ref))
        geometry = self._geometry(geometry_ref, config)
        semantic = self._get(r.SemanticContext, semantic_id)
        for field, expected in (("task_id", task.task_id), ("task_revision", task.revision),
                                ("scene_id", geometry.scene_id), ("feature_set_id", geometry.feature_set_id)):
            _same(getattr(semantic, field), expected, f"$.semantic.{field}")
        self._observation(semantic.observation_id, geometry.scene_id, config)
        # Semantic provenance can be an earlier capture of this same static scene.
        if type(program_refs) not in (list, tuple) or type(execution_binding_refs) not in (list, tuple):
            _fail("$.references", "invalid_reference", "references must be lists or tuples")
        refs = tuple(self._parse(r.ProgramRef, ref) for ref in program_refs)
        if len({ref.program_id for ref in refs}) != len(refs) or set(refs) != set(semantic.program_refs):
            _fail("$.program_refs", "program_coverage", "programs must exactly cover semantic program references")
        programs = tuple(self._program(ref, geometry) for ref in refs)
        objects = {obj.object_id: obj for obj in geometry.objects}
        candidate_ids = tuple(item.object_id for item in semantic.object_relevance)
        for candidate_id in candidate_ids:
            if candidate_id not in objects:
                _fail("$.semantic.object_relevance", "missing_reference", f"candidate {candidate_id!r} is absent")
        if {item.program.object_id for item in programs} != set(candidate_ids):
            _fail("$.program_refs", "candidate_coverage", "each candidate needs programs and every program needs a candidate")
        bindings = tuple(self._get(r.ProgramExecutionBinding, ref) for ref in execution_binding_refs)
        by_program = {binding.program_id: binding for binding in bindings}
        if len(by_program) != len(bindings) or set(by_program) != {ref.program_id for ref in refs}:
            _fail("$.execution_binding_refs", "binding_coverage", "exactly one binding is required per program")
        ordered_bindings, receipts = [], []
        for item in programs:
            binding = by_program[item.program.program_id]
            _same(binding.config_id, config.config_id, "$.binding.config_id")
            receipts.append(self._receipt(binding.validation_receipt_id, item.program, geometry, config))
            ordered_bindings.append(binding)
        return ResolvedBundle(self.session_id, config, task, geometry, semantic,
                              tuple(objects[key] for key in candidate_ids), programs,
                              tuple(ordered_bindings), tuple(receipts))

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
            semantic_id=snapshot.semantic_id, program_refs=snapshot.program_refs,
            execution_binding_refs=snapshot.execution_binding_refs,
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
        return ResolvedSnapshot(snapshot, bundle, joystick, state, history)

    def resolve_weights(self, weights_id: str) -> ResolvedWeights:
        """Resolve weights and their exact causal snapshot, preserving candidates."""
        weights = self._get(r.ObjectWeights, weights_id)
        snapshot = self.resolve_snapshot(weights.snapshot_id)
        by_object = {item.object_id: item for item in weights.weights}
        candidate_ids = tuple(obj.object_id for obj in snapshot.bundle.candidates)
        if set(by_object) != set(candidate_ids):
            _fail("$.weights", "candidate_coverage", "weights must exactly cover the semantic candidate set")
        return ResolvedWeights(weights, snapshot, tuple(by_object[key] for key in candidate_ids))
