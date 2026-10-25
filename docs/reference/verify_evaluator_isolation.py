"""Reproducible offline evaluator probes and fault-boundary measurements.

Run with the installed wheel and the repository's test dependency (pytest).
The trusted fault harness is shared with tests, never exposed to generated code.
"""
import argparse
from dataclasses import asdict
from hashlib import sha256
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import runpy
import statistics
import sys
import tempfile
import threading
import time

import pytest
from ssc_core import SupervisedEvaluator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--wheel-sha256", required=True)
    args = parser.parse_args()
    helpers = runpy.run_path(str(Path(__file__).resolve().parents[2] / "tests/shared_control/test_supervision.py"))
    identity = helpers["IDENTITY"]
    positions = [[.4, 0., .3]]
    rotations = [identity]
    for axis in range(3):
        for sign in (1, -1):
            p = positions[0].copy()
            p[axis] += sign * .01
            positions.append(p)
            c, s = math.cos(.1), sign * math.sin(.1)
            matrices = [
                [[1, 0, 0], [0, c, -s], [0, s, c]],
                [[c, 0, s], [0, 1, 0], [-s, 0, c]],
                [[c, -s, 0], [s, c, 0], [0, 0, 1]],
            ]
            rotations.append(matrices[axis])
    report = {"source_revision": args.source_revision, "wheel_sha256": args.wheel_sha256,
              "package_version": importlib.metadata.version("ssc-core"),
              "environment": {"platform": platform.platform(), "python": sys.version,
                              "os_release": Path('/etc/os-release').read_text(),
                              "numpy": importlib.metadata.version("numpy"),
                              "scipy": importlib.metadata.version("scipy"),
                              "pytest": pytest.__version__},
              "probes": [], "faults": [], "preparations": [], "ok": True}
    for mode in ("distance", "axis"):
        artifact = helpers["artifact"]("    return norm(p_tcp - keypoints[0]) - params[0]\n" if mode == "distance"
            else "    return acos(clip(dot(R_control_tcp[:, 0], axis_dirs[0]), -1, 1)) - params[0]\n")
        artifact["program_id"] = f"isolation-probe-{mode}"
        artifact["keypoint_bindings"] = ["target"] if mode == "distance" else []
        artifact["axis_bindings"] = ["axis"] if mode == "axis" else []
        artifact["parameters"] = [{"name": "tolerance", "value": 0., "unit": "m" if mode == "distance" else "rad"}]
        artifact["output_unit"] = "m" if mode == "distance" else "rad"
        worker = SupervisedEvaluator()
        try:
            prep = worker.prepare(artifact, motion_disabled=True).result(6)
            report["preparations"].append(asdict(prep))
            assert prep.ok, prep
            for position in positions:
                for rotation in rotations:
                    target = [.42, 0., .3]
                    axis = [math.cos(.1), math.sin(.1), 0.]
                    outcome = worker.submit(position, rotation, [target] if mode == "distance" else [],
                                            [axis] if mode == "axis" else [], [0.]).result(1)
                    expected = (math.dist(position, target) if mode == "distance" else
                                math.acos(min(1., max(-1., sum(rotation[k][0] * axis[k] for k in range(3))))))
                    valid = outcome.ok and math.isclose(outcome.residual, expected, abs_tol=1e-9, rel_tol=1e-9)
                    report["probes"].append({"mode": mode, "position": position, "rotation": rotation,
                                             "expected": expected, "matches": valid, "outcome": asdict(outcome)})
                    report["ok"] &= valid
        finally:
            assert worker.close().result(1).reaped
    with tempfile.TemporaryDirectory() as directory:
        for mode, expected in (("stall", "timeout"), ("late", "timeout"), ("memory", "memory_limit"),
                               ("crash", "worker_crash"), ("exception", "worker_exception"),
                               ("partial_reply", "timeout"), ("wrong_id", "malformed_response")):
            worker = SupervisedEvaluator()
            patch = pytest.MonkeyPatch()
            beats, stop = [], threading.Event()
            def heartbeat():
                while not stop.wait(.001):
                    beats.append(time.monotonic_ns())
            thread = threading.Thread(target=heartbeat)
            try:
                helpers["inject"](worker, Path(directory), mode, patch)
                prep = worker.prepare(helpers["artifact"](), motion_disabled=True).result(6)
                assert prep.ok, prep
                fd_before = len(list(Path('/proc/self/fd').iterdir()))
                thread.start()
                start = time.monotonic_ns()
                future = worker.submit(*helpers["INPUTS"])
                returned = time.monotonic_ns()
                outcome = future.result(1)
                stop.set()
                thread.join(1)
                fd_after = len(list(Path('/proc/self/fd').iterdir()))
                valid = outcome.code == expected and outcome.residual is None and outcome.reaped
                report["ok"] &= valid
                report["faults"].append({"mode": mode, "expected_code": expected, "matches": valid,
                    "submit_return_s": (returned - start) / 1e9, "heartbeats": len(beats),
                    "max_heartbeat_gap_s": max([(b-a)/1e9 for a,b in zip(beats, beats[1:])] or [0]),
                    "fd_before": fd_before, "fd_after": fd_after, "outcome": asdict(outcome)})
            finally:
                stop.set()
                if thread.ident is not None:
                    thread.join(1)
                assert worker.close().result(1).reaped
                patch.undo()
    durations = [(item["outcome"]["finished_ns"] - item["outcome"]["started_ns"]) / 1e9 for item in report["probes"]]
    ordered = sorted(durations)
    report["normal_latency_s"] = {"count": len(durations), "min": min(durations),
        "median": statistics.median(durations), "p95": ordered[math.ceil(.95 * len(ordered))-1],
        "max": max(durations)}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": report["ok"], "normal_latency_s": report["normal_latency_s"],
                      "faults": len(report["faults"]), "output": str(args.output)}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
