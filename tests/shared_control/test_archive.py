"""Archive integrity, dependency closure and non-authoritative numerical replay."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ssc_core import RegistryError
from ssc_core.archive import ARCHIVE_VERSION, ArchiveBuilder, ArchiveError, RunArchive, inspect_archive
from ssc_core.replay import replay_archive

FIXTURE = Path(os.environ.get("SSC_ARCHIVE_FIXTURE",
    Path(__file__).resolve().parents[1] / "fixtures/shared_control/control_run_v1.json"))
linux = pytest.mark.skipif(sys.platform != "linux", reason="bounded residual replay requires Linux")


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()


def envelope(payload):
    return encode({"archive_version": ARCHIVE_VERSION, "sha256": sha256(encode(payload)).hexdigest(), "payload": payload})


@pytest.fixture
def archive():
    return RunArchive.read(FIXTURE)


def builder_from(archive):
    payload = archive.to_dict()
    builder = ArchiveBuilder(payload["manifest"])
    for record in payload["records"]:
        builder.add_record(record["record_type"], record["data"])
    for message in payload["messages"]:
        builder.add_message(message)
    for resource in payload["resources"]:
        builder.add_resource(resource["resource_id"], archive.resource(resource["resource_id"]), media_type=resource["media_type"])
    for event in payload["events"]:
        builder.add_event(event["event_id"], event["kind"], event["data"])
    return builder


def test_roundtrip_exact_source_inputs_times_and_observations(archive, tmp_path):
    rebuilt = builder_from(archive).seal()
    assert rebuilt.to_dict() == archive.to_dict()
    path = tmp_path / "run.json"
    digest = rebuilt.write(path)
    loaded = RunArchive.read(path, expected_sha256=digest)
    assert path.read_bytes() == archive.to_bytes()
    registry = loaded.registry()
    for identifier in ("snapshot-0", "snapshot-1", "snapshot-2"):
        assert registry.resolve_snapshot(identifier) == archive.registry().resolve_snapshot(identifier)
    for item in archive.to_dict()["records"]:
        if item["record_type"] == "ConstraintProgram":
            actual = registry.get_record("ConstraintProgram", item["data"]["program_id"], session_id=registry.session_id)
            assert actual.source_code.encode("utf-8") == item["data"]["source_code"].encode("utf-8")
    inspected = inspect_archive(loaded)
    assert len(inspected["proposed_results"]) == 4
    assert len(inspected["acceptance_decisions"]) == 4
    assert len(inspected["controller_observations"]) == 1
    assert next(r for r in inspected["proposed_results"] if r["result_id"] == "result-1")["solver_status"] == "solved"
    assert next(e for e in inspected["acceptance_decisions"] if e["data"]["result_id"] == "result-1")["data"]["command_accepted"] is False
    assert not any(e["data"]["result_id"] == "local-hold" for e in inspected["controller_observations"])
    assert loaded.events == archive.events


def test_detached_reads_and_no_overwrite(archive, tmp_path):
    payload = archive.to_dict()
    payload["manifest"]["run_id"] = "changed"
    assert archive.manifest["run_id"] != "changed"
    registry = archive.registry()
    registry.register_record("TaskContext", {"task_id": "extra", "revision": 1, "instruction": "detached"}, session_id=registry.session_id)
    with pytest.raises(RegistryError, match="missing_reference"):
        archive.registry().get_record("TaskContext", {"task_id": "extra", "revision": 1}, session_id=registry.session_id)
    path = tmp_path / "run.json"
    archive.write(path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        archive.write(path)
    assert path.read_bytes() == before
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        archive.write(link)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["link.json", "run.json"]


@pytest.mark.parametrize("kind", ["record", "message", "resource", "event"])
def test_changed_identity_rejected_by_writer(archive, kind):
    builder = builder_from(archive)
    payload = archive.to_dict()
    with pytest.raises((RegistryError, ArchiveError), match="immutable_conflict"):
        if kind == "record":
            program = next(r["data"] for r in payload["records"] if r["record_type"] == "ConstraintProgram")
            program["parameters"][0]["value"] = .123
            builder.add_record("ConstraintProgram", program)
        elif kind == "message":
            message = next(m for m in payload["messages"] if m["message_type"] == "JoystickSample")
            message["payload"]["u_xy"] = [0, 1]
            builder.add_message(message)
        elif kind == "resource":
            builder.add_resource("archive-rgb-v1", b"changed", media_type="application/octet-stream")
        else:
            event = payload["events"][0]
            event["data"]["command_seq"] += 1
            builder.add_event(event["event_id"], event["kind"], event["data"])


@pytest.mark.parametrize("record_type", ["ConstraintProgram", "ProgramExecutionBinding", "ProgramValidationReceipt", "ObjectWeights", "ControlSnapshot"])
def test_missing_record_is_not_substituted(archive, record_type):
    payload = archive.to_dict()
    payload["records"] = [r for r in payload["records"] if r["record_type"] != record_type]
    with pytest.raises((RegistryError, ArchiveError), match="missing_reference|incomplete_record"):
        RunArchive.from_bytes(envelope(payload))


@pytest.mark.parametrize("message_type", ["SceneGeometry", "SceneObservation", "TaskContext", "SessionConfig", "SemanticContext", "JoystickSample", "RobotState", "ControlResult"])
def test_missing_enveloped_dependency(archive, message_type):
    payload = archive.to_dict()
    payload["messages"] = [m for m in payload["messages"] if m["message_type"] != message_type]
    with pytest.raises((RegistryError, ArchiveError), match="missing_reference|incomplete_record"):
        RunArchive.from_bytes(envelope(payload))


@pytest.mark.parametrize("resource_id", ["offline-reference-v1", "numeric-offline-v1", "solver-slsqp-v1", "intent-linear-v1",
    "synthetic-rotary-v1", "synthetic-host-v1", "archive-calibration-v1", "archive-rgb-v1", "archive-depth-v1",
    "archive-input-mapping-v1", "fixture-installation-v1", "archive-scenario-v1"])
def test_missing_resource(archive, resource_id):
    payload = archive.to_dict()
    payload["resources"] = [r for r in payload["resources"] if r["resource_id"] != resource_id]
    with pytest.raises(ArchiveError, match="missing_reference"):
        RunArchive.from_bytes(envelope(payload))


def test_checksums_versions_and_strict_json(archive):
    data = json.loads(archive.to_bytes())
    data["payload"]["manifest"]["run_id"] += "tampered"
    with pytest.raises(ArchiveError, match="checksum_mismatch"):
        RunArchive.from_bytes(encode(data))
    with pytest.raises(ArchiveError, match="checksum_mismatch"):
        RunArchive.from_bytes(archive.to_bytes(), expected_sha256="0" * 64)
    data["archive_version"] = "ssc-run-v2"
    with pytest.raises(ArchiveError, match="unsupported_version"):
        RunArchive.from_bytes(encode(data))
    with pytest.raises(ArchiveError, match="duplicate_key"):
        RunArchive.from_bytes(b'{"x":1,"x":2}')
    for invalid in (b'{"x": NaN}', b'{"x": 1e999}', b'{"truncated":'):
        with pytest.raises(ArchiveError, match="invalid_json"):
            RunArchive.from_bytes(invalid)


@pytest.mark.parametrize("mutation,code", [
    ("source", "source_hash_mismatch"), ("binding", "incompatible_reference"),
    ("receipt", "incompatible_reference"), ("resource_bytes", "checksum_mismatch"),
    ("version", "unsupported_contract"), ("revision", "invalid_revision"),
    ("dispatch", "incomplete_record"), ("evaluation", "incomplete_record"),
    ("previous", "missing_reference"), ("age", "invalid_timing"),
    ("clock", "clock_mismatch"), ("evaluation_pose", "incompatible_reference"),
    ("diagnostics", "incomplete_record"), ("duplicate_event", "duplicate_event"),
])
def test_semantic_damage_even_with_recomputed_container_checksum(archive, mutation, code):
    payload = archive.to_dict()
    if mutation in ("source", "binding", "receipt"):
        kind = {"source": "ConstraintProgram", "binding": "ProgramExecutionBinding", "receipt": "ProgramValidationReceipt"}[mutation]
        record = next(r["data"] for r in payload["records"] if r["record_type"] == kind)
        if mutation == "source":
            record["source_code"] += "\n"
        elif mutation == "binding":
            record["normalization_scale"] *= 2
        else:
            record["source_sha256"] = "0" * 64
    elif mutation == "resource_bytes":
        payload["resources"][0]["base64"] = "YQ=="
    elif mutation == "version":
        payload["manifest"]["schema_version"] = "2.0"
    elif mutation == "revision":
        payload["manifest"]["software"]["server"]["revision"] = "main"
    elif mutation in ("dispatch", "evaluation"):
        payload["events"] = [e for e in payload["events"] if e["kind"] != mutation]
    elif mutation == "previous":
        payload["events"] = [e for e in payload["events"] if e["event_id"] != "acceptance-0"]
    elif mutation in ("age", "clock"):
        dispatch = next(e["data"] for e in payload["events"] if e["kind"] == "dispatch")
        if mutation == "age":
            dispatch["inputs"][0]["source_age_ns"] += 1
        else:
            dispatch["dispatched_at"]["clock_id"] = "other-clock"
    elif mutation == "evaluation_pose":
        next(e for e in payload["events"] if e["kind"] == "evaluation")["data"]["pose"]["position_xyz"][0] += .01
    elif mutation == "diagnostics":
        next(m for m in payload["messages"] if m["message_type"] == "ControlResult")["payload"]["constraint_evaluations"].pop()
    else:
        payload["events"].append(deepcopy(payload["events"][0]))
    with pytest.raises((RegistryError, ArchiveError), match=code):
        RunArchive.from_bytes(envelope(payload))


def test_reordered_storage_resolves_identically(archive):
    payload = archive.to_dict()
    for key in ("records", "messages", "resources"):
        payload[key].reverse()
    reordered = RunArchive.from_bytes(envelope(payload))
    assert reordered.registry().resolve_snapshot("snapshot-1") == archive.registry().resolve_snapshot("snapshot-1")
    assert reordered.events == archive.events


@linux
def test_numerical_replay_in_fresh_process_no_runtime_import_or_commands(archive, tmp_path):
    script = '''
import importlib.abc, json, sys
class RejectRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"ssc_runtime", "rclpy", "rospy", "grasp_server"}:
            raise AssertionError("replay tried importing motion runtime: " + fullname)
sys.meta_path.insert(0, RejectRuntime())
from ssc_core.archive import RunArchive
from ssc_core.replay import replay_archive
print(json.dumps(replay_archive(RunArchive.read(sys.argv[1], expected_sha256=sys.argv[2]))))
'''
    completed = subprocess.run([sys.executable, "-I", "-c", script, str(FIXTURE), archive.sha256],
        cwd=tmp_path, text=True, capture_output=True, check=True, timeout=45)
    report = json.loads(completed.stdout)
    assert report["ok"] and report["evaluation_count"] == 4
    for item in report["evaluations"]:
        assert item["actual"]["raw_residual"] == pytest.approx(item["expected"]["raw_g"], abs=1e-9, rel=1e-9)
        assert item["actual"]["penalty"] == pytest.approx(item["expected"]["penalty"], abs=1e-9, rel=1e-9)
        assert item["cleanup"]["reaped"] is True
        assert item["inputs"]["keypoints"]
    assert report["events"] == archive.events
    assert not list(tmp_path.iterdir())


@linux
def test_numerical_mismatch_is_reported_not_repaired(archive):
    payload = archive.to_dict()
    move = next(m["payload"] for m in payload["messages"] if m["message_type"] == "ControlResult")
    move["constraint_evaluations"][0]["raw_g"] += .1
    report = replay_archive(RunArchive.from_bytes(envelope(payload)))
    assert report["ok"] is False
    assert "residual_mismatch" in report["evaluations"][0]["diagnostic"]


@pytest.mark.skipif(sys.platform == "linux", reason="non-Linux fail-closed check")
def test_replay_has_no_unbounded_non_linux_fallback(archive):
    report = replay_archive(archive)
    assert not report["ok"]
    assert all(item["preparation"]["code"] == "unsupported_platform" for item in report["evaluations"])


def test_cli_inspection_and_actionable_error(archive, tmp_path):
    inspected = subprocess.run([sys.executable, "-I", "-m", "ssc_core.replay", str(FIXTURE), "--inspect"],
        capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(inspected.stdout)["events"] == archive.events
    bad = tmp_path / "corrupt.json"
    bad.write_text("{}")
    failed = subprocess.run([sys.executable, "-I", "-m", "ssc_core.replay", str(bad)], capture_output=True, text=True, timeout=10)
    assert failed.returncode == 2
    assert json.loads(failed.stderr)["code"] == "invalid_fields"


def test_exact_utf8_crlf_and_signed_zero_roundtrip(archive):
    builder = builder_from(archive)
    program = deepcopy(next(r["data"] for r in archive.to_dict()["records"] if r["record_type"] == "ConstraintProgram"))
    program["program_id"] = "extra-source-bytes"
    program["source_code"] = "# 几何残差\r\n" + program["source_code"].replace("\n", "\r\n")
    program["source_sha256"] = sha256(program["source_code"].encode("utf-8")).hexdigest()
    program["parameters"][0]["value"] = -0.0
    builder.add_record("ConstraintProgram", program)
    sealed = builder.seal()
    actual = sealed.registry().get_record("ConstraintProgram", program["program_id"], session_id=sealed.manifest["session_id"])
    assert actual.source_code.encode("utf-8") == program["source_code"].encode("utf-8")
    assert actual.parameters[0].value.hex() == (-0.0).hex()
    program["parameters"][0]["value"] = 0.0
    with pytest.raises(RegistryError, match="immutable_conflict"):
        builder.add_record("ConstraintProgram", program)


@pytest.mark.parametrize("kind", ["record", "message", "resource"])
def test_reader_rejects_conflicting_duplicate_identities(archive, kind):
    payload = archive.to_dict()
    if kind == "record":
        copy = deepcopy(next(r for r in payload["records"] if r["record_type"] == "ConstraintProgram"))
        copy["data"]["description"] += " changed"
        payload["records"].append(copy)
    elif kind == "message":
        copy = deepcopy(next(m for m in payload["messages"] if m["message_type"] == "JoystickSample"))
        copy["meta"]["sequence"] += 1
        payload["messages"].append(copy)
    else:
        copy = deepcopy(payload["resources"][0])
        copy["media_type"] += "; changed"
        payload["resources"].append(copy)
    with pytest.raises((ArchiveError, RegistryError), match="immutable_conflict"):
        RunArchive.from_bytes(envelope(payload))


def test_duplicate_diagnostics_cannot_be_collapsed_during_replay(archive):
    payload = archive.to_dict()
    diagnostics = next(m for m in payload["messages"] if m["message_type"] == "ControlResult")["payload"]["constraint_evaluations"]
    hidden = deepcopy(diagnostics[0])
    hidden["raw_g"] += 1
    diagnostics.insert(0, hidden)
    with pytest.raises(ArchiveError, match="duplicate_evaluation"):
        RunArchive.from_bytes(envelope(payload))


@pytest.mark.parametrize("identity", ["numeric-offline-v1", "solver-slsqp-v1", "intent-linear-v1"])
def test_changed_configuration_is_not_silently_replaced(archive, identity):
    import base64
    payload = archive.to_dict()
    resource = next(r for r in payload["resources"] if r["resource_id"] == identity)
    value = json.loads(base64.b64decode(resource["base64"]))
    value["changed"] = True
    content = encode(value)
    resource["base64"] = base64.b64encode(content).decode()
    resource["sha256"] = sha256(content).hexdigest()
    with pytest.raises(ArchiveError, match="unsupported_configuration"):
        RunArchive.from_bytes(envelope(payload))


@linux
def test_forbidden_generated_source_never_executes_even_with_consistent_hashes(archive, tmp_path):
    payload = archive.to_dict()
    program = next(r["data"] for r in payload["records"] if r["record_type"] == "ConstraintProgram")
    marker = tmp_path / "must-not-exist"
    program["source_code"] = (
        "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n"
        f"    open({str(marker)!r}, 'w')\n    return 0\n")
    digest = sha256(program["source_code"].encode()).hexdigest()
    identifier = program["program_id"]

    def update_refs(value):
        if isinstance(value, dict):
            if value.get("program_id") == identifier and "source_sha256" in value:
                value["source_sha256"] = digest
            for child in value.values():
                update_refs(child)
        elif isinstance(value, list):
            for child in value:
                update_refs(child)
    update_refs(payload)
    report = replay_archive(RunArchive.from_bytes(envelope(payload)))
    assert not report["ok"]
    assert not marker.exists()
    assert any(not item["preparation"]["ok"] and item["preparation"]["stage"] == "compile" for item in report["evaluations"])
