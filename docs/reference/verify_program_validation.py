"""Retain end-to-end host acceptance/rejection evidence from an installed wheel."""

import argparse
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import runpy
import sys

import pytest

from ssc_core import (
    HostBindingPolicy, HostNormalization, HostProgramValidator, NUMERIC_OFFLINE_V1,
    SessionRegistry, UnitReview, feature_definition_digest, program_content_digest,
    residual_diagnostic,
)
from ssc_core import program_validation


ROOT = Path(__file__).resolve().parents[2]
HEADER = "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n"


def fixture(mode):
    catalog = json.loads((ROOT / "tests/fixtures/shared_control/bottle_cup_offline_v1.json").read_text())
    program = deepcopy(catalog["constraint_programs"][0])
    geometry = deepcopy(next(m["payload"] for m in catalog["messages"] if m["message_type"] == "SceneGeometry"))
    if "axis" in mode:
        directed = mode == "directed-axis"
        geometry["axes"] = [{"axis_id": "axis-evidence", "object_id": program["object_id"],
            "origin_keypoint_id": program["keypoint_bindings"][0],
            "unit_direction_xyz": [1., 0., 0.], "directed": directed}]
        program["axis_bindings"] = ["axis-evidence"]
        program["keypoint_bindings"] = []
        program["parameters"] = []
        if directed:
            expression = "acos(clip(dot(R_control_tcp[:,0], axis_dirs[0]), -1, 1))"
            program["output_unit"] = "rad"
        else:
            product = "dot(R_control_tcp[:,0], axis_dirs[0])"
            expression = f"abs({product})" if mode == "undirected-axis" else product
            program["output_unit"] = "dimensionless"
        program["source_code"] = HEADER + f"    return {expression}\n"
    if mode == "domain-failure":
        program["parameters"] = []
        program["keypoint_bindings"] = []
        program["output_unit"] = "dimensionless"
        program["source_code"] = HEADER + "    return 1 / 0\n"
    program["program_id"] = "program-evidence-" + mode
    program["source_sha256"] = sha256(program["source_code"].encode()).hexdigest()
    return catalog, program, geometry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--wheel-sha256", required=True)
    args = parser.parse_args()
    evidence = {"source_revision": args.source_revision, "wheel_sha256": args.wheel_sha256,
                "package_version": importlib.metadata.version("ssc-core"),
                "policy": asdict(NUMERIC_OFFLINE_V1),
                "environment": {"platform": platform.platform(), "python": sys.version},
                "level": "offline host semantic/numerical component validation; no robot or complete loop",
                "cases": [], "ok": True}
    modes = ("distance", "directed-axis", "undirected-axis", "noninvariant-axis", "domain-failure",
             "changed-newline", "wrong-unit", "missing-review", "crash", "stall", "memory")
    faults = runpy.run_path(str(ROOT / "tests/shared_control/test_supervision.py"))
    for mode in modes:
        catalog, program, geometry = fixture(mode)
        registry = SessionRegistry("session-offline-v1", policy=NUMERIC_OFFLINE_V1)
        for message in catalog["messages"]:
            if message["message_type"] != "SceneGeometry":
                registry.register_message(message)
        # These are fixed public test formulas authored here, never generator
        # metadata. Review rationale explicitly covers dimensions, not intent.
        rationale = ("Metre displacement norm minus metre tolerance" if program["output_unit"] == "m" else
                     "acos of clipped dimensionless unit-vector dot product yields radians" if program["output_unit"] == "rad" else
                     "Dimensionless vector dot/abs or scalar division; domain/sign validity is tested separately")
        review = UnitReview("review-" + mode, program_content_digest(program), geometry["scene_id"],
            geometry["feature_set_id"], feature_definition_digest(geometry), program["output_unit"],
            tuple(p["unit"] for p in program["parameters"]), "repository fixture author and independent PR reviewer", rationale)
        unit = "rad" if mode == "wrong-unit" else program["output_unit"]
        settings = HostNormalization(unit, {"m": .01, "rad": .1, "dimensionless": 1.}[unit])
        authority = HostProgramValidator(registry, unit_reviews=() if mode == "missing-review" else (review,),
            binding_policies=(HostBindingPolicy("config-offline-v1", program["program_id"], settings),))
        if mode == "changed-newline":
            program["source_code"] = program["source_code"].rstrip("\n")
        patch = pytest.MonkeyPatch()
        try:
            if mode in ("crash", "stall", "memory"):
                import tempfile
                with tempfile.TemporaryDirectory() as directory:
                    worker = program_validation.SupervisedEvaluator()
                    faults["inject"](worker, Path(directory), mode, patch)
                    patch.setattr(program_validation, "SupervisedEvaluator", lambda: worker)
                    report = authority.validate(program, geometry=geometry, config_id="config-offline-v1", motion_disabled=True)
            else:
                report = authority.validate(program, geometry=geometry, config_id="config-offline-v1", motion_disabled=True)
            expected = mode in ("distance", "directed-axis", "undirected-axis")
            matches = (report.receipt.status == "accepted") == expected
            case = {"mode": mode, "expected_status": "accepted" if expected else "rejected",
                    "matches": matches, "report": asdict(report)}
            if expected:
                replay = authority.validate(program, geometry=geometry, config_id="config-offline-v1", motion_disabled=True)
                checks = []
                if report.numerical and replay.numerical and replay.receipt.status == "accepted":
                    keypoints = {k["keypoint_id"]: k["position_xyz"] for k in geometry["keypoints"]}
                    for first, second in zip(report.numerical.probes, replay.numerical.probes, strict=True):
                        probe = first.probe
                        oracle = (math.dist(probe.position, keypoints[program["keypoint_bindings"][0]]) if mode == "distance"
                                  else math.acos(min(1., max(-1., probe.rotation[0][0]))) if mode == "directed-axis"
                                  else abs(probe.rotation[0][0]))
                        checks.append({"probe": probe.name, "reversed_axis": first.reversed_axis_id,
                            "expected": oracle, "actual": first.outcome.residual,
                            "matches": math.isclose(first.outcome.residual, oracle, abs_tol=1e-9, rel_tol=1e-9)
                              and math.isclose(first.outcome.residual, second.outcome.residual, abs_tol=1e-9, rel_tol=1e-9)})
                case["replay"] = asdict(replay)
                case["oracles"] = checks
                case["matches"] &= bool(checks) and all(c["matches"] for c in checks)
                if report.receipt.status == "accepted":
                    case["binding"] = asdict(authority.create_binding("binding-" + mode,
                        receipt_id=report.receipt.receipt_id, config_id="config-offline-v1",
                        geometry_ref={"geometry_id": geometry["geometry_id"], "revision": geometry["revision"]}))
            evidence["cases"].append(case)
            evidence["ok"] &= case["matches"]
        finally:
            patch.undo()
    evidence["wiki_penalties"] = [asdict(residual_diagnostic(g, output_unit="m",
        normalization=HostNormalization("m", .01))) for g in (.008, -.002)]
    evidence["ok"] &= all(math.isclose(actual["penalty"], expected, abs_tol=1e-9, rel_tol=1e-9)
                           for actual, expected in zip(evidence["wiki_penalties"], (.64, 0.)))
    args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"ok": evidence["ok"], "cases": len(evidence["cases"]), "output": str(args.output)}))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
