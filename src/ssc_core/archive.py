"""Self-contained offline evidence, never restored command or receipt authority.

The archive digest detects corruption, not authorship. Pin the returned digest
outside the archive when transporting it. One archive contains one session;
restart/clock reset starts another archive. All public reads return detached
values. No generated code, controller factory or resource path is imported.
"""

import base64
from dataclasses import asdict, dataclass
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import tempfile

from . import records as r
from .fusion import get_intent_config
from .optimization import get_solver_config
from .policy import NUMERIC_OFFLINE_V1
from .registry import SessionRegistry, _canonical
from .supervision import EVALUATOR_LIMITS
from .validation import parse_record, to_dict

ARCHIVE_VERSION = "ssc-run-v1"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")


class ArchiveError(ValueError):
    """Stable diagnostic code plus the failing archive field/reference."""

    def __init__(self, path, code, detail):
        self.path, self.code = path, code
        super().__init__(f"{path}: {code}: {detail}")


def _require(ok, path, code, detail):
    if not ok:
        raise ArchiveError(path, code, detail)


def _fields(value, names, path):
    _require(type(value) is dict and set(value) == set(names.split()), path,
             "invalid_fields", f"expected exactly: {names}")


def _text(value, path):
    _require(type(value) is str and bool(value.strip()), path, "invalid_value", "expected nonempty string")


def _encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode("ascii")


def _decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "$", "duplicate_key", key)
            result[key] = value
        return result

    def constant(value):
        raise ArchiveError("$", "invalid_json", f"nonfinite constant {value}")

    def floating(value):
        import math
        number = float(value)
        _require(math.isfinite(number), "$", "invalid_json", "nonfinite number")
        return number

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError("$", "invalid_json", str(exc)) from exc


def _copy(value):
    try:
        return _decode(_encode(value))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ArchiveError("$", "invalid_json", str(exc)) from exc


def _digest(value):
    return sha256(_encode(value)).hexdigest()


def environment():
    """Current inspection environment; differences are reported, never hidden."""
    return {"system": platform.system(), "release": platform.release(),
            "machine": platform.machine(), "python": platform.python_version(),
            "packages": {name: importlib.metadata.version(name)
                         for name in ("ssc-core", "numpy", "scipy")}}


def _manifest(value):
    _fields(value, "run_id session_id schema_version function_api_version profile_id "
            "evaluator_policy_id solver_config_id software environment installation_id "
            "scenario_id evidence_scope replay_tolerances", "$.manifest")
    for name in ("run_id", "session_id", "profile_id", "installation_id", "scenario_id", "evidence_scope"):
        _text(value[name], f"$.manifest.{name}")
    for name, expected in (("schema_version", "1.0"), ("function_api_version", "geometry-residual-v1"),
                           ("evaluator_policy_id", "numeric-offline-v1")):
        _require(value[name] == expected, f"$.manifest.{name}", "unsupported_contract", expected)
    get_solver_config(value["solver_config_id"])
    _fields(value["software"], "server ros2", "$.manifest.software")
    for role, software in value["software"].items():
        path = f"$.manifest.software.{role}"
        _fields(software, "revision package_version wheel_sha256", path)
        _require(type(software["revision"]) is str and bool(_REVISION.fullmatch(software["revision"])),
                 path + ".revision", "invalid_revision", "expected full lowercase Git commit SHA")
        _text(software["package_version"], path + ".package_version")
        _require(type(software["wheel_sha256"]) is str and bool(_SHA.fullmatch(software["wheel_sha256"])),
                 path + ".wheel_sha256", "invalid_digest", "expected wheel SHA-256")
    _fields(value["environment"], "system release machine python packages", "$.manifest.environment")
    for name in ("system", "release", "machine", "python"):
        _text(value["environment"][name], "$.manifest.environment." + name)
    packages = value["environment"]["packages"]
    _require(type(packages) is dict and {"ssc-core", "numpy", "scipy"} <= set(packages),
             "$.manifest.environment.packages", "incomplete_record", "need ssc-core, numpy and scipy versions")
    for name, version in packages.items():
        _text(version, "$.manifest.environment.packages." + name)
    _require(packages["ssc-core"] == value["software"]["server"]["package_version"],
             "$.manifest.software.server.package_version", "incompatible_reference", "environment version differs")
    _require(value["replay_tolerances"] == {"atol": 1e-9, "rtol": 1e-9},
             "$.manifest.replay_tolerances", "unsupported_policy", "offline-reference-v1 requires atol=rtol=1e-9")


def _stamp(value, path):
    stamp = parse_record(r.TimePoint, value, policy=NUMERIC_OFFLINE_V1)
    return stamp.clock_id, stamp.sec * 1_000_000_000 + stamp.nanosec


def _counter(value, path):
    _require(type(value) is int and 0 <= value <= 9007199254740991, path,
             "invalid_value", "expected nonnegative interoperable integer")


class ArchiveBuilder:
    """Accumulate detached immutable records, then validate and publish once.

    The caller serializes builder access. Add all dependencies before seal().
    Re-adding identical identities is idempotent; changed content is rejected.
    Resource IDs are scoped to this session and contain bytes, never live URLs.
    """

    def __init__(self, manifest):
        self._manifest = _copy(manifest)
        _manifest(self._manifest)
        self._registry = SessionRegistry(manifest["session_id"], policy=NUMERIC_OFFLINE_V1)
        self._records, self._messages, self._resources, self._events = {}, {}, {}, {}

    def add_record(self, record_type, data):
        record = self._registry.register_record(record_type, data, session_id=self._registry.session_id)
        self._records[SessionRegistry._key(record)] = {"record_type": type(record).__name__, "data": to_dict(record)}
        return record

    def add_message(self, data):
        message = self._registry.register_message(data)
        self._messages[message.meta.message_id] = to_dict(message)
        return message

    @staticmethod
    def _put(store, key, value, path):
        _text(key, path)
        if key in store:
            _require(_canonical(store[key]) == _canonical(value), path, "immutable_conflict",
                     "identity already contains different content")
        store[key] = value

    def add_resource(self, resource_id, content: bytes, *, media_type):
        _require(type(content) is bytes, "$.resources", "invalid_value", "content must be exact bytes")
        _text(media_type, "$.resources.media_type")
        value = {"resource_id": resource_id, "media_type": media_type,
                 "sha256": sha256(content).hexdigest(), "base64": base64.b64encode(content).decode("ascii")}
        self._put(self._resources, resource_id, value, "$.resources." + str(resource_id))

    def add_event(self, event_id, kind, data):
        value = _copy({"event_id": event_id, "kind": kind, "data": data})
        self._put(self._events, event_id, value, "$.events." + str(event_id))

    def seal(self):
        payload = {"manifest": self._manifest, "records": list(self._records.values()),
                   "messages": list(self._messages.values()), "resources": list(self._resources.values()),
                   "events": list(self._events.values())}
        return RunArchive.from_bytes(_encode({"archive_version": ARCHIVE_VERSION,
                                             "sha256": _digest(payload), "payload": payload}))


@dataclass(frozen=True, slots=True)
class RunArchive:
    """Validated immutable byte image. Registry access reconstructs a fresh copy."""

    _bytes: bytes

    def __post_init__(self):
        _require(type(self._bytes) is bytes and len(self._bytes) <= MAX_ARCHIVE_BYTES,
                 "$", "archive_size", "archive must be bytes of at most 64 MiB")
        envelope = _decode(self._bytes)
        _fields(envelope, "archive_version sha256 payload", "$")
        _require(envelope["archive_version"] == ARCHIVE_VERSION, "$.archive_version",
                 "unsupported_version", f"reader supports {ARCHIVE_VERSION}")
        _require(envelope["sha256"] == _digest(envelope["payload"]), "$.sha256",
                 "checksum_mismatch", "payload content changed")
        self._validate(envelope["payload"])

    @classmethod
    def from_bytes(cls, content, *, expected_sha256=None):
        if expected_sha256 is not None:
            _require(sha256(content).hexdigest() == expected_sha256, "$", "checksum_mismatch",
                     "file differs from externally pinned SHA-256")
        return cls(content)

    @classmethod
    def read(cls, path, *, expected_sha256=None):
        with open(path, "rb") as stream:
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
        return cls.from_bytes(data, expected_sha256=expected_sha256)

    @property
    def sha256(self):
        return sha256(self._bytes).hexdigest()

    def to_bytes(self):
        return self._bytes

    def to_dict(self):
        return _decode(self._bytes)["payload"]

    @property
    def manifest(self):
        return self.to_dict()["manifest"]

    @property
    def events(self):
        return self.to_dict()["events"]

    def resource(self, resource_id):
        for value in self.to_dict()["resources"]:
            if value["resource_id"] == resource_id:
                return base64.b64decode(value["base64"], validate=True)
        raise ArchiveError("$.resources", "missing_reference", resource_id)

    def registry(self):
        return self._registry(self.to_dict())

    def write(self, path):
        """Publish a complete file atomically without replacing any existing path."""
        path = Path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(self._bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)  # atomic create-if-absent, including symlink targets
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return self.sha256

    @staticmethod
    def _registry(payload):
        registry = SessionRegistry(payload["manifest"]["session_id"], policy=NUMERIC_OFFLINE_V1)
        for index, record in enumerate(payload["records"]):
            _fields(record, "record_type data", f"$.records[{index}]")
            registry.register_record(record["record_type"], record["data"], session_id=registry.session_id)
        for message in payload["messages"]:
            registry.register_message(message)
        return registry

    @staticmethod
    def _validate(payload):
        _fields(payload, "manifest records messages resources events", "$.payload")
        _manifest(payload["manifest"])
        for name in ("records", "messages", "resources", "events"):
            _require(type(payload[name]) is list, "$.." + name, "invalid_value", "expected array")
        registry = RunArchive._registry(payload)
        resources = {}
        for index, resource in enumerate(payload["resources"]):
            path = f"$.resources[{index}]"
            _fields(resource, "resource_id media_type sha256 base64", path)
            _text(resource["resource_id"], path + ".resource_id")
            _text(resource["media_type"], path + ".media_type")
            try:
                data = base64.b64decode(resource["base64"], validate=True)
            except (ValueError, TypeError) as exc:
                raise ArchiveError(path, "invalid_bytes", str(exc)) from exc
            _require(sha256(data).hexdigest() == resource["sha256"], path, "checksum_mismatch", "resource bytes changed")
            ArchiveBuilder._put(resources, resource["resource_id"], resource, path)

        def resource(identifier):
            _require(identifier in resources, "$.resources", "missing_reference", str(identifier))
            return base64.b64decode(resources[identifier]["base64"], validate=True)

        def settings(identifier, expected):
            _require(_canonical(_decode(resource(identifier))) == _canonical(_copy(expected)),
                     "$.resources." + identifier, "unsupported_configuration", "content differs from installed policy")

        manifest = payload["manifest"]
        for name in ("profile_id", "installation_id", "scenario_id"):
            _require(bool(resource(manifest[name])), "$.manifest." + name, "incomplete_record", "empty resource")
        settings(manifest["evaluator_policy_id"], {"validation": asdict(NUMERIC_OFFLINE_V1),
                                                 "execution": asdict(EVALUATOR_LIMITS)})
        settings(manifest["solver_config_id"], asdict(get_solver_config(manifest["solver_config_id"])))
        # Enumerate the supplied objects, not registry internals. Envelopes may
        # duplicate standalone artifacts; the registry has checked their identity.
        records = [parse_record(item["record_type"], item["data"], policy=NUMERIC_OFFLINE_V1)
                   for item in payload["records"]]
        messages = [registry.get_message(item["meta"]["message_id"], session_id=registry.session_id)
                    for item in payload["messages"]]
        records += [message.payload for message in messages]
        snapshots, results, weights = {}, {}, {}
        for record in records:
            if type(record) is r.SessionConfig:
                resource(record.robot_model_id)
                resource(record.motion_constraint_set_id)
                resource(record.validation_policy_id)
            elif type(record) is r.SceneObservation:
                resource(record.calibration_id)
                resource(record.rgb_ref.asset_id)
                resource(record.depth_ref.asset_id)
            elif type(record) is r.JoystickSample:
                resource(record.mapping_id)
            elif type(record) is r.ControlSnapshot:
                snapshots[record.snapshot_id] = registry.resolve_snapshot(record.snapshot_id)
            elif type(record) is r.ObjectWeights:
                resolved = registry.resolve_weights(record.weights_id)
                settings(record.estimator_config_id, asdict(get_intent_config(record.estimator_config_id)))
                _require(get_intent_config(record.estimator_config_id).session_config_id ==
                         resolved.snapshot.snapshot.config_id, "$.weights.estimator_config_id",
                         "incompatible_reference", record.weights_id)
                weights[record.weights_id] = record
            elif type(record) is r.ControlResult:
                results[record.result_id] = record
        _require(bool(snapshots), "$.records", "incomplete_record", "run requires at least one snapshot")

        def result(identifier):
            _require(identifier in results, "$.events.result_id", "missing_reference", str(identifier))
            return results[identifier]

        dispatches, evaluations, applied, event_ids = {}, {}, {}, {}
        for index, event in enumerate(payload["events"]):
            path = f"$.events[{index}]"
            _fields(event, "event_id kind data", path)
            _text(event["event_id"], path + ".event_id")
            _require(event["event_id"] not in event_ids, path, "duplicate_event", event["event_id"])
            event_ids[event["event_id"]] = event
            kind, data = event["kind"], event["data"]
            if kind == "dispatch":
                _fields(data, "snapshot_id command_seq created_at dispatched_at expires_at solve_deadline inputs", path)
                _require(data["snapshot_id"] in snapshots, path, "missing_reference", "snapshot")
                _counter(data["command_seq"], path + ".command_seq")
                times = [_stamp(data[name], path) for name in ("created_at", "dispatched_at", "expires_at", "solve_deadline")]
                _require(len({t[0] for t in times}) == 1, path, "clock_mismatch", "local dispatch clocks differ")
                created, dispatched, expires, deadline = [t[1] for t in times]
                _require(created <= dispatched < deadline <= expires, path, "invalid_timing", "dispatch time ordering")
                snapshot = snapshots[data["snapshot_id"]]
                expected = {snapshot.joystick.meta.message_id, snapshot.robot_state.meta.message_id,
                            *(m.meta.message_id for m in snapshot.joystick_history)}
                _require(type(data["inputs"]) is list, path, "invalid_value", "inputs must be array")
                actual = set()
                for timed in data["inputs"]:
                    _fields(timed, "message_id received_at source_age_ns", path + ".inputs")
                    message = registry.get_message(timed["message_id"], session_id=registry.session_id)
                    _require(type(message.payload) in (r.JoystickSample, r.RobotState), path, "incompatible_reference", "live input required")
                    sampled = _stamp(to_dict(message.payload.sampled_at), path)
                    received = _stamp(timed["received_at"], path)
                    _counter(timed["source_age_ns"], path)
                    _require(sampled[0] == received[0] == times[0][0], path, "clock_mismatch", "input clock differs")
                    _require(sampled[1] <= received[1] <= created and timed["source_age_ns"] == created - sampled[1],
                             path, "invalid_timing", "source age or reception differs from original sample")
                    _require(timed["message_id"] not in actual, path, "duplicate_input", timed["message_id"])
                    actual.add(timed["message_id"])
                _require(actual == expected, path, "incomplete_record", "dispatch inputs must cover snapshot and history")
                _require(data["snapshot_id"] not in dispatches, path, "duplicate_dispatch", data["snapshot_id"])
                dispatches[data["snapshot_id"]] = data
            elif kind == "acceptance":
                acceptance = parse_record(r.CommandAcceptanceRecord, data, policy=NUMERIC_OFFLINE_V1)
                result(acceptance.result_id)
                if acceptance.command_accepted:
                    ArchiveBuilder._put(applied, acceptance.applied_command_id, data, path)
            elif kind == "evaluation":
                _fields(data, "result_id pose", path)
                candidate = result(data["result_id"])
                pose = parse_record(r.Pose, data["pose"], policy=NUMERIC_OFFLINE_V1)
                _require(bool(candidate.constraint_evaluations), path, "incompatible_reference", "result has no residual diagnostics")
                if candidate.status == "move":
                    _require(pose == candidate.next_tcp_pose, path, "incompatible_reference", "evaluated pose differs from proposed target")
                _require(candidate.result_id not in evaluations, path, "duplicate_evaluation", candidate.result_id)
                evaluations[candidate.result_id] = data
            elif kind == "result_arrival":
                _fields(data, "result_id received_at", path)
                result(data["result_id"])
                _stamp(data["received_at"], path)
            elif kind in ("controller_request", "controller_observation", "lifecycle"):
                # Runtime-specific detail is retained verbatim and never imported.
                _fields(data, "at result_id detail", path)
                _stamp(data["at"], path)
                if data["result_id"] is not None:
                    result(data["result_id"])
                _require(type(data["detail"]) is dict and bool(data["detail"]), path, "incomplete_record", "explicit event detail required")
            else:
                raise ArchiveError(path + ".kind", "unsupported_event", str(kind))
        _require(set(dispatches) == set(snapshots), "$.events", "incomplete_record", "each snapshot needs its original dispatch")
        for identifier, resolved in snapshots.items():
            previous = resolved.snapshot.previous_applied_command_id
            if previous is not None:
                _require(previous in applied, "$.snapshots.previous_applied_command_id", "missing_reference", previous)
                accepted_at = _stamp(applied[previous]["checked_at"], "$.acceptance")
                created_at = _stamp(dispatches[identifier]["created_at"], "$.dispatch")
                _require(accepted_at[0] == created_at[0] and accepted_at[1] <= created_at[1],
                         "$.snapshots.previous_applied_command_id", "invalid_timing", "acceptance must precede snapshot")
        for identifier, candidate in results.items():
            if candidate.snapshot_id is not None:
                _require(candidate.snapshot_id in snapshots, "$.results.snapshot_id", "missing_reference", identifier)
            if candidate.weights_id is not None:
                _require(candidate.weights_id in weights, "$.results.weights_id", "missing_reference", identifier)
                _require(weights[candidate.weights_id].snapshot_id == candidate.snapshot_id,
                         "$.results.weights_id", "incompatible_reference", identifier)
            if candidate.constraint_evaluations or candidate.status == "move":
                _require(identifier in evaluations and candidate.snapshot_id in snapshots,
                         "$.events.evaluation", "incomplete_record", identifier)
                bindings = {b.program_id: b for b in snapshots[candidate.snapshot_id].bundle.execution_bindings}
                diagnostics = {d.program_id: d for d in candidate.constraint_evaluations}
                _require(len(diagnostics) == len(candidate.constraint_evaluations),
                         "$.results.constraint_evaluations", "duplicate_evaluation", identifier)
                _require(set(bindings) == set(diagnostics), "$.results.constraint_evaluations", "incomplete_record", identifier)
                for program_id, diagnostic in diagnostics.items():
                    _require(diagnostic.normalization_scale == bindings[program_id].normalization_scale,
                             "$.results.constraint_evaluations", "incompatible_reference", "normalization differs from binding")


def inspect_archive(archive: RunArchive):
    """Return separately inspectable proposals, decisions and supplied observations."""
    payload = archive.to_dict()
    results = {item["data"]["result_id"]: item["data"] for item in payload["records"]
               if item["record_type"] == "ControlResult"}
    results.update({item["payload"]["result_id"]: item["payload"] for item in payload["messages"]
                    if item["message_type"] == "ControlResult"})
    return {"archive_sha256": archive.sha256, "manifest": archive.manifest,
            "proposed_results": list(results.values()),
            "acceptance_decisions": [e for e in archive.events if e["kind"] == "acceptance"],
            "controller_requests": [e for e in archive.events if e["kind"] == "controller_request"],
            "controller_observations": [e for e in archive.events if e["kind"] == "controller_observation"],
            "events": archive.events}
