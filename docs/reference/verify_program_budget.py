"""Full-count Linux acceptance: 20 solves per fixture/count, no discarded samples."""
from collections import Counter
from dataclasses import asdict
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import runpy
import statistics
import sys

from ssc_core import HostCheckerSpec, SupervisedOptimizer, get_solver_config

F = runpy.run_path(str(Path(__file__).with_name("optimizer_fixtures.py")))
CONFIG = get_solver_config("solver-slsqp-batch-v2")
CHECKER = HostCheckerSpec("ssc_core._test_doubles:WorkspaceSphereDouble")


def scenario(fixture, count):
    if fixture == "infeasible-start":
        kwargs = F["infeasible_start"]()
        objects = [(F["BOTTLE"], F["KP_BOTTLE"])] * count
    else:
        kwargs = dict(current=(-1., 0.), history=(((1., 0.), True),), previous={"kind": "move"})
        objects = [(F["BOTTLE"], F["KP_BOTTLE"]) if i % 2 == 0 else (F["CUP"], F["KP_CUP"])
                   for i in range(count)]
        if count == 1:
            kwargs["candidates"] = (F["BOTTLE"],)
    counts = Counter(obj for obj, _ in objects)
    # Replicate relations with distinct identities but preserve each object's
    # total penalty weight. Increasing count must not make the infeasible
    # problem easier by changing tolerance or the objective's relative scale.
    kwargs["programs"] = tuple(F["distance_program"](f"budget-{fixture}-{count}-{i}", obj, kp,
                                                   weight=1. / counts[obj])
                               for i, (obj, kp) in enumerate(objects))
    return F["scenario"](**kwargs)


def measure(fixture, count, samples):
    case = scenario(fixture, count)
    solver = SupervisedOptimizer(CHECKER, solver_config=CONFIG)
    results = []
    try:
        for index in range(samples):
            if not solver.ready:
                prep = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(6)
                if not prep.ok:
                    results.append({"sample": index, "preparation_error": prep.code, "ok": False})
                    continue
            out = solver.solve(case["resolved"], case["weights"], intent_config=case["intent_config"],
                               previous_applied=case["previous"], command_seq=index).result(3)
            expected = "infeasible" if fixture == "infeasible-start" else "solved"
            stats = json.loads(out.evaluator_json)["stats"] if out.evaluator_json else None
            ok = out.result.solver_status == expected and out.elapsed_s < CONFIG.solve_budget_s
            if fixture == "infeasible-start":
                ok = ok and out.result.status == "hold" and out.result.next_tcp_pose is None
            else:
                ok = ok and out.result.status == "move"
            results.append({"sample": index, "code": out.code, "status": out.result.solver_status,
                            "elapsed_ms": out.elapsed_s * 1000, "margin_ms": (CONFIG.solve_budget_s - out.elapsed_s) * 1000,
                            "evaluator_stats": stats, "ok": ok})
    finally:
        solver.close().result(3)
    elapsed = sorted(r["elapsed_ms"] for r in results if "elapsed_ms" in r)
    row = {"fixture": fixture, "programs": count, "samples": results,
           "ok": len(results) == samples and all(r["ok"] for r in results),
           "timeouts": sum(r.get("status") == "timeout" for r in results),
           "median_ms": statistics.median(elapsed) if elapsed else None,
           "p95_ms": elapsed[max(0, (95 * len(elapsed) + 99) // 100 - 1)] if elapsed else None,
           "max_ms": max(elapsed) if elapsed else None}
    print(json.dumps({k: v for k, v in row.items() if k != "samples"}), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--wheel-sha256", required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("acceptance requires Linux isolation")
    rows = [measure(fixture, count, 20) for fixture in ("reversal", "infeasible-start")
            for count in range(1, CONFIG.max_programs + 1)]
    report = {"profile_id": "offline-reference-batch-v2", "solver_config": asdict(CONFIG),
              "source_revision": args.source_revision, "wheel_sha256": args.wheel_sha256,
              "environment": {"platform": platform.platform(), "machine": platform.machine(),
                              "python": sys.version, "package": importlib.metadata.version("ssc-core")},
              "fixture_method": "replicated original relations with normalized binding weights; no tolerance changes",
              "scope": "software timing on the stated CI host, not a real-time guarantee or hardware evidence",
              "rows": rows, "ok": all(row["ok"] for row in rows)}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
