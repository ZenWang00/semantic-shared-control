"""Trust-boundary unit tests and separately marked real Linux worker tests."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import sys

import pytest

from ssc_core import NUMERIC_OFFLINE_V1, RegistryError, SessionRegistry, ValidationError, to_dict
from ssc_core.penalties import HostNormalization
from ssc_core.program_validation import NumericalValidation
from ssc_core.receipts import (
    AcceptanceError, HostBindingPolicy, HostProgramValidator, UnitReview,
    feature_definition_digest, program_content_digest,
)
import ssc_core.receipts as receipts


def inputs(catalog):
    program = deepcopy(catalog["constraint_programs"][0])
    geometry = deepcopy(next(m["payload"] for m in catalog["messages"] if m["message_type"] == "SceneGeometry"))
    return program, geometry


def review_for(program, geometry):
    return UnitReview("review-distance-v1", program_content_digest(program), geometry["scene_id"],
                      geometry["feature_set_id"], feature_definition_digest(geometry), program["output_unit"],
                      tuple(p["unit"] for p in program["parameters"]), "test fixture author",
                      "norm of metre displacement minus a metre tolerance; bound target is an approach point")


def authority_for(catalog, *, program=None, geometry=None, reviews=None, unit="m", policy=NUMERIC_OFFLINE_V1):
    default_program, default_geometry = inputs(catalog)
    program = program or default_program
    geometry = geometry or default_geometry
    registry = SessionRegistry("session-offline-v1", policy=policy)
    for message in catalog["messages"]:
        if message["message_type"] != "SceneGeometry":
            registry.register_message(message)
    review = review_for(program, geometry)
    authority = HostProgramValidator(registry, unit_reviews=(review,) if reviews is None else reviews,
        binding_policies=(HostBindingPolicy("config-offline-v1", program["program_id"], HostNormalization(unit, .01)),))
    return authority, registry, program, geometry


def run(authority, program, geometry):
    return authority.validate(program, geometry=geometry, config_id="config-offline-v1", motion_disabled=True)


def refs(program, geometry):
    return {"program_ref": {"program_id": program["program_id"], "source_sha256": program["source_sha256"]},
            "geometry_ref": {"geometry_id": geometry["geometry_id"], "revision": geometry["revision"]},
            "config_id": "config-offline-v1"}


@pytest.fixture
def successful_probe_stub(monkeypatch):
    # Isolate trust/identity logic. These tests are not numerical/isolation evidence.
    calls = []
    def stub(resolved, *, motion_disabled):
        calls.append(resolved)
        return NumericalValidation(True, (), None, (), None)
    monkeypatch.setattr(receipts, "probe_program", stub)
    return calls


def test_issue_verify_bind_and_imported_receipt_is_untrusted(offline_catalog, successful_probe_stub):
    authority, registry, program, geometry = authority_for(offline_catalog)
    report = run(authority, program, geometry)
    assert report.receipt.status == "accepted"
    assert report.unit_review == review_for(program, geometry)
    assert len(successful_probe_stub) == 1
    reference = refs(program, geometry)
    assert authority.verify_receipt(report.receipt.receipt_id, **reference) == report.receipt
    binding = authority.create_binding("binding-host", receipt_id=report.receipt.receipt_id,
                                       geometry_ref=reference["geometry_ref"], config_id="config-offline-v1")
    assert binding.normalization_scale == .01 and binding.weight == 1
    assert authority.validate_binding(binding.binding_id, geometry_ref=reference["geometry_ref"],
                                       config_id="config-offline-v1") == binding
    fresh = HostProgramValidator(registry, unit_reviews=(review_for(program, geometry),), binding_policies=())
    with pytest.raises(AcceptanceError, match="untrusted_receipt"):
        fresh.verify_receipt(report.receipt.receipt_id, **reference)
    forged = to_dict(report.receipt)
    forged["receipt_id"] = "imported-accepted"
    registry.register_record("ProgramValidationReceipt", forged, session_id=registry.session_id)
    with pytest.raises(AcceptanceError, match="untrusted_receipt"):
        authority.verify_receipt(forged["receipt_id"], **reference)


@pytest.mark.parametrize("field,value", [("function_api_version", "other"), ("output_unit", "cm"),
                                       ("parameters", [{"name": "t", "value": 1, "unit": "cm"}]),
                                       ("object_id", None)])
def test_structural_failures_do_not_fabricate_receipts(offline_catalog, field, value, successful_probe_stub):
    authority, registry, program, geometry = authority_for(offline_catalog)
    program[field] = value
    with pytest.raises(ValidationError):
        run(authority, program, geometry)
    assert not successful_probe_stub


@pytest.mark.parametrize("mutation,reason", [
    ("newline", "source_hash_mismatch"), ("object", "missing_reference"),
    ("feature", "missing_reference"), ("frame", "incompatible_reference"),
    ("parameter", "missing_unit_review"), ("declaration", "missing_unit_review"),
])
def test_semantic_failures_receive_rejected_receipt(offline_catalog, mutation, reason, successful_probe_stub):
    authority, registry, program, geometry = authority_for(offline_catalog)
    if mutation == "newline": program["source_code"] = program["source_code"].rstrip("\n")
    if mutation == "object": program["object_id"] = "foreign-object"
    if mutation == "feature": program["keypoint_bindings"] = ["foreign-feature"]
    if mutation == "frame": geometry["frame_id"] = "other-frame"
    if mutation == "parameter": program["parameters"][0]["value"] = .123
    if mutation == "declaration": program["description"] += " changed"
    report = run(authority, program, geometry)
    assert report.receipt.status == "rejected"
    assert any(reason in diagnostic for diagnostic in report.receipt.diagnostics)
    assert report.receipt.source_sha256 == sha256(program["source_code"].encode()).hexdigest()
    assert not successful_probe_stub
    with pytest.raises(AcceptanceError, match="untrusted_receipt"):
        authority.verify_receipt(report.receipt.receipt_id, **refs(program, geometry))


@pytest.mark.parametrize("mutation", ["parameters", "binding_order", "output_unit", "source"])
def test_full_artifact_identity_even_with_same_source_hash(offline_catalog, mutation, successful_probe_stub):
    program, geometry = inputs(offline_catalog)
    program["keypoint_bindings"] = [kp["keypoint_id"] for kp in geometry["keypoints"]]
    authority, registry, program, geometry = authority_for(offline_catalog, program=program)
    first = run(authority, program, geometry)
    assert first.receipt.status == "accepted"
    changed = deepcopy(program)
    if mutation == "parameters": changed["parameters"][0]["value"] = .1
    if mutation == "binding_order": changed["keypoint_bindings"].reverse()
    if mutation == "output_unit": changed["output_unit"] = "rad"
    if mutation == "source":
        changed["source_code"] += "\n"
        changed["source_sha256"] = sha256(changed["source_code"].encode()).hexdigest()
    report = run(authority, changed, geometry)
    assert report.receipt.status == "rejected"
    assert "immutable_conflict" in report.receipt.diagnostics[0]
    assert len(successful_probe_stub) == 1


def test_no_review_wrong_units_policy_and_missing_settings(offline_catalog, successful_probe_stub):
    for options, reason in [({"reviews": ()}, "missing_unit_review"), ({"unit": "rad"}, "unit_mismatch"),
                           ({"policy": replace(NUMERIC_OFFLINE_V1, rotation_tolerance=.1)}, "policy_mismatch")]:
        authority, registry, program, geometry = authority_for(offline_catalog, **options)
        report = run(authority, program, geometry)
        assert report.receipt.status == "rejected" and reason in report.receipt.diagnostics[0]
    program, geometry = inputs(offline_catalog)
    review = replace(review_for(program, geometry), parameter_units=("rad",))
    authority, registry, program, geometry = authority_for(offline_catalog, reviews=(review,))
    assert "unit_mismatch" in run(authority, program, geometry).receipt.diagnostics[0]
    authority = HostProgramValidator(registry, unit_reviews=(review_for(program, geometry),), binding_policies=())
    assert "missing_normalization" in run(authority, program, geometry).receipt.diagnostics[0]
    assert not successful_probe_stub


def test_coordinate_revision_reuse_and_storage_permutation(offline_catalog, successful_probe_stub):
    authority, registry, program, geometry = authority_for(offline_catalog)
    report = run(authority, program, geometry)
    updated = deepcopy(geometry)
    updated["geometry_id"] += "-new"
    updated["revision"] += 1
    updated["keypoints"][0]["position_xyz"][0] += .01
    for name in ("objects", "axes", "keypoints"): updated[name].reverse()
    registry.register_record("SceneGeometry", updated, session_id=registry.session_id)
    assert authority.verify_receipt(report.receipt.receipt_id, **refs(program, updated)) == report.receipt
    assert feature_definition_digest(updated) == feature_definition_digest(geometry)
    assert len(successful_probe_stub) == 1  # no re-probing or source regeneration


@pytest.mark.parametrize("change", ["scene", "feature_set", "directedness"])
def test_changed_scene_or_feature_definitions_cannot_reuse(offline_catalog, change, successful_probe_stub):
    program, geometry = inputs(offline_catalog)
    geometry["axes"] = [{"axis_id": "axis-test", "object_id": program["object_id"],
        "origin_keypoint_id": program["keypoint_bindings"][0],
        "unit_direction_xyz": [1, 0, 0], "directed": False}]
    authority, registry, program, geometry = authority_for(offline_catalog, geometry=geometry)
    report = run(authority, program, geometry)
    updated = deepcopy(geometry)
    updated["geometry_id"] += "-new"
    updated["feature_set_id"] += "-new"
    if change == "scene":
        updated["scene_id"] += "-new"
        observation = deepcopy(next(m["payload"] for m in offline_catalog["messages"] if m["message_type"] == "SceneObservation"))
        observation["observation_id"] += "-new"
        observation["scene_id"] = updated["scene_id"]
        updated["observation_id"] = observation["observation_id"]
        registry.register_record("SceneObservation", observation, session_id=registry.session_id)
    if change == "directedness": updated["axes"][0]["directed"] = not updated["axes"][0]["directed"]
    registry.register_record("SceneGeometry", updated, session_id=registry.session_id)
    with pytest.raises(AcceptanceError, match="feature_mismatch"):
        authority.verify_receipt(report.receipt.receipt_id, **refs(program, updated))


def test_external_binding_settings_are_not_host_policy(offline_catalog, successful_probe_stub):
    authority, registry, program, geometry = authority_for(offline_catalog)
    report = run(authority, program, geometry)
    registry.register_record("ProgramExecutionBinding", {
        "binding_id": "forged-binding", "program_id": program["program_id"], "config_id": "config-offline-v1",
        "normalization_scale": .1, "weight": 1., "validation_receipt_id": report.receipt.receipt_id,
    }, session_id=registry.session_id)
    with pytest.raises(AcceptanceError, match="normalization_mismatch"):
        authority.validate_binding("forged-binding", geometry_ref=refs(program, geometry)["geometry_ref"],
                                   config_id="config-offline-v1")


def test_numerical_failure_cannot_issue_accepted_receipt(offline_catalog, monkeypatch):
    monkeypatch.setattr(receipts, "probe_program", lambda *a, **kw: NumericalValidation(
        False, ("probe/timeout",), None, (), None))
    authority, registry, program, geometry = authority_for(offline_catalog)
    report = run(authority, program, geometry)
    assert report.receipt.status == "rejected" and report.receipt.diagnostics == ("probe/timeout",)


@pytest.mark.parametrize("change", ["hash", "program_id", "geometry_revision", "config", "policy"])
def test_receipt_reference_mismatches(offline_catalog, change, successful_probe_stub):
    authority, registry, program, geometry = authority_for(offline_catalog)
    report = run(authority, program, geometry)
    reference = refs(program, geometry)
    if change == "hash": reference["program_ref"]["source_sha256"] = "0" * 64
    if change == "program_id":
        other = deepcopy(program)
        other["program_id"] += "-other"
        registry.register_record("ConstraintProgram", other, session_id=registry.session_id)
        reference["program_ref"]["program_id"] = other["program_id"]
    if change == "geometry_revision": reference["geometry_ref"]["revision"] += 1
    if change == "config": reference["config_id"] = "missing-config"
    if change == "policy":
        config = to_dict(registry.get_record("SessionConfig", "config-offline-v1", session_id=registry.session_id))
        config["config_id"] += "-other"
        config["validation_policy_id"] += "-other"
        registry.register_record("SessionConfig", config, session_id=registry.session_id)
        reference["config_id"] = config["config_id"]
    with pytest.raises((AcceptanceError, RegistryError)):
        authority.verify_receipt(report.receipt.receipt_id, **reference)


def test_geometry_definition_change_under_existing_id_is_rejected(offline_catalog, successful_probe_stub):
    authority, registry, program, geometry = authority_for(offline_catalog)
    run(authority, program, geometry)
    geometry["geometry_id"] += "-changed"
    geometry["keypoints"][0]["object_id"] = geometry["objects"][1]["object_id"]
    report = run(authority, program, geometry)
    assert report.receipt.status == "rejected"
    assert "feature_definition_mismatch" in report.receipt.diagnostics[0]


def test_host_metadata_is_validated_and_copied(offline_catalog):
    authority, registry, program, geometry = authority_for(offline_catalog)
    review = review_for(program, geometry)
    for changes in ({"reviewer": ""}, {"program_digest": "bad"}, {"output_unit": "cm"},
                    {"parameter_units": ["m"]}, {"rationale": ""}):
        with pytest.raises((TypeError, ValueError)):
            replace(review, **changes)
    with pytest.raises(ValueError, match="duplicate review"):
        HostProgramValidator(registry, unit_reviews=(review, review), binding_policies=())
    settings = HostBindingPolicy("config-offline-v1", program["program_id"], HostNormalization("m", .01))
    with pytest.raises(ValueError, match="duplicate configuration"):
        HostProgramValidator(registry, unit_reviews=(review,), binding_policies=(settings, settings))


@pytest.mark.skipif(sys.platform != "linux", reason="real bounded-worker receipt acceptance")
def test_real_issued_receipt_and_binding(offline_catalog):
    authority, registry, program, geometry = authority_for(offline_catalog)
    report = run(authority, program, geometry)
    assert report.receipt.status == "accepted", report.receipt.diagnostics
    assert len(report.numerical.probes) == 50 and report.numerical.cleanup.reaped
    assert any(p.outcome.residual > 0 for p in report.numerical.probes)
    assert authority.verify_receipt(report.receipt.receipt_id, **refs(program, geometry)) == report.receipt
    assert authority.create_binding("binding-real", receipt_id=report.receipt.receipt_id,
        geometry_ref=refs(program, geometry)["geometry_ref"], config_id="config-offline-v1").program_id == program["program_id"]
