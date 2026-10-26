"""Offline restricted residual replay: python -m ssc_core.replay ARCHIVE.

No optimizer, runtime, controller or archived factory is instantiated. Archived
receipts remain historical evidence, not locally issued acceptance authority.
"""

import argparse
from dataclasses import asdict
import json
import math
import sys

from .archive import ArchiveError, RunArchive, environment, inspect_archive
from .penalties import HostNormalization, residual_diagnostic
from .supervision import SupervisedEvaluator


def replay_archive(archive: RunArchive):
    # Lazy numerical dependency: importing ssc_core remains standard-library only.
    from scipy.spatial.transform import Rotation

    registry = archive.registry()
    manifest = archive.manifest
    current = environment()
    report = {"archive_sha256": archive.sha256, "run_id": manifest["run_id"],
              "evidence_scope": "offline residual replay; no command execution or receipt authority",
              "recorded_environment": manifest["environment"], "replay_environment": current,
              "environment_differences": {key: {"recorded": manifest["environment"][key], "replay": value}
                                          for key, value in current.items() if value != manifest["environment"][key]},
              "tolerances": manifest["replay_tolerances"], "evaluations": [], "ok": True}
    for event in archive.events:
        if event["kind"] != "evaluation":
            continue
        data = event["data"]
        result = registry.get_record("ControlResult", data["result_id"], session_id=registry.session_id)
        resolved = registry.resolve_snapshot(result.snapshot_id)
        rotation = Rotation.from_quat(data["pose"]["quaternion_xyzw"]).as_matrix().tolist()
        diagnostics = {item.program_id: item for item in result.constraint_evaluations}
        for program, binding in zip(resolved.bundle.programs, resolved.bundle.execution_bindings):
            expected = diagnostics[program.program.program_id]
            inputs = {"p_tcp": data["pose"]["position_xyz"], "R_control_tcp": rotation,
                      "keypoints": [list(v) for v in program.keypoints],
                      "axis_dirs": [list(v) for v in program.axis_dirs], "params": list(program.params)}
            item = {"event_id": event["event_id"], "snapshot_id": result.snapshot_id,
                    "result_id": result.result_id, "program_id": program.program.program_id,
                    "source_sha256": program.program.source_sha256, "inputs": inputs,
                    "expected": asdict(expected), "actual": None, "ok": False}
            worker = SupervisedEvaluator()
            try:
                preparation = worker.prepare(program.program, motion_disabled=True).result()
                item["preparation"] = asdict(preparation)
                if preparation.ok:
                    outcome = worker.submit(**inputs).result()
                    item["evaluation"] = asdict(outcome)
                    if outcome.ok:
                        try:
                            actual = residual_diagnostic(outcome.residual, output_unit=program.program.output_unit,
                                normalization=HostNormalization(program.program.output_unit,
                                                                binding.normalization_scale, binding.weight))
                            item["actual"] = asdict(actual)
                            item["ok"] = all(math.isclose(actual_value, recorded_value,
                                abs_tol=manifest["replay_tolerances"]["atol"],
                                rel_tol=manifest["replay_tolerances"]["rtol"])
                                for actual_value, recorded_value in ((actual.raw_residual, expected.raw_g),
                                                                    (actual.penalty, expected.penalty)))
                            if not item["ok"]:
                                item["diagnostic"] = "residual_mismatch: archived raw residual or penalty differs"
                        except ValueError as exc:
                            item["diagnostic"] = "invalid_penalty: " + str(exc)
                else:
                    item["diagnostic"] = "preparation_failed: " + preparation.diagnostic
            finally:
                cleanup = worker.close().result()
                item["cleanup"] = asdict(cleanup)
                item["ok"] = item["ok"] and cleanup.ok and cleanup.reaped is True
            report["evaluations"].append(item)
            report["ok"] = report["ok"] and item["ok"]
    report["evaluation_count"] = len(report["evaluations"])
    report["numerical_evidence"] = "compared" if report["evaluations"] else "unavailable (no recorded evaluations)"
    # Preserve observations as supplied; an empty list is not a stopping claim.
    inspection = inspect_archive(archive)
    for key in ("proposed_results", "acceptance_decisions", "controller_requests", "controller_observations", "events"):
        report[key] = inspection[key]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--sha256", help="externally retained archive file SHA-256")
    parser.add_argument("--inspect", action="store_true", help="validate and inspect without numerical execution")
    args = parser.parse_args(argv)
    try:
        archive = RunArchive.read(args.archive, expected_sha256=args.sha256)
        report = inspect_archive(archive) if args.inspect else replay_archive(archive)
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        return 0 if report.get("ok", True) else 1
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"ok": False, "code": getattr(exc, "code", "invalid_archive"),
                          "diagnostic": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
