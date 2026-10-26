"""Analytical and fresh-process replay evidence from the installed shared wheel."""

import argparse
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import platform
import runpy
import subprocess
import sys

from ssc_core import NUMERIC_OFFLINE_V1, estimate_intent, get_intent_config, to_dict


HELPER = Path(__file__).with_name("intent_fixtures.py")


def calculate():
    fixtures = runpy.run_path(str(HELPER))
    cases = []
    for name, arguments, expected, alpha, disposition in fixtures["CASES"]:
        value = fixtures["fixture"](**arguments)
        config = get_intent_config("intent-linear-alpha0-v1" if arguments.get("alpha_zero") else "intent-linear-v1")
        result = estimate_intent(value, config, weights_id=f"weights-{name}", policy=NUMERIC_OFFLINE_V1)
        actual = tuple(item.weight for item in result.weights.weights) if result.weights else ()
        ok = (result.disposition == disposition and len(actual) == len(expected) and
              all(abs(a-b) <= 1e-12 for a, b in zip(actual, expected)) and
              result.weights.assistance_strength == alpha)
        cases.append({"name": name, "inputs": asdict(value), "configuration": asdict(config),
                      "expected_weights": expected, "expected_alpha": alpha, "expected_disposition": disposition,
                      "actual": asdict(result), "ok": ok})
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--wheel-sha256")
    parser.add_argument("--replay-child", action="store_true")
    args = parser.parse_args()
    cases = calculate()
    if args.replay_child:
        print(json.dumps(cases, sort_keys=True, allow_nan=False))
        return
    if not all((args.output, args.source_revision, args.wheel_sha256)):
        parser.error("--output, --source-revision and --wheel-sha256 are required")
    child = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--replay-child"],
                           check=True, capture_output=True, text=True)
    replay = json.loads(child.stdout)
    original = json.loads(json.dumps(cases, allow_nan=False))
    report = {"source_revision": args.source_revision, "wheel_sha256": args.wheel_sha256,
              "package_version": importlib.metadata.version("ssc-core"),
              "environment": {"platform": platform.platform(), "python": sys.version},
              "profile_id": "offline-reference-v1", "policy": asdict(NUMERIC_OFFLINE_V1),
              "weight_absolute_tolerance": 1e-12, "cases": cases,
              "fresh_process_replay_equal": replay == original,
              "evidence_level": "synthetic deterministic component calculation; compatibility-only receipts; no activation, motion or hardware",
              "ok": all(case["ok"] for case in cases) and replay == original}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in ("ok", "fresh_process_replay_equal", "package_version")}))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
