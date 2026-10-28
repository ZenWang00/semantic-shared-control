"""Record Linux solver evidence for data-only candidate targets.

This uses synthetic inputs and a host-checker double, never a VLM or robot.
The exact problem, selection, solver settings, output and provenance are saved.
"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import runpy
import sys

from ssc_core import HostCheckerSpec, SupervisedOptimizer, to_dict
from ssc_core.archive import environment

F = runpy.run_path(str(Path(__file__).with_name("optimizer_fixtures.py")))


def verify_case(name, inputs, expected_z):
    case = F["scenario"](**inputs)
    solver = SupervisedOptimizer(HostCheckerSpec("ssc_core._test_doubles:WorkspaceSphereDouble"))
    try:
        preparation = solver.prepare(case["resolved"].bundle, motion_disabled=True).result(10)
        assert preparation.ok, preparation
        outcome = solver.solve(case["resolved"], case["selection"], intent_config=case["intent_config"],
                               command_seq=1, previous_applied=case["previous"]).result(5)
        result = outcome.result
        assert result.status == "move", outcome
        before = case["resolved"].robot_state.payload.tcp_pose
        after = result.next_tcp_pose
        delta = [b-a for a,b in zip(before.position_xyz, after.position_xyz)]
        assert after.quaternion_xyzw == before.quaternion_xyzw
        assert abs(delta[2]) < 1e-7 if expected_z == 0 else expected_z * delta[2] > 1e-3
        assert delta[0] > 0 and math.dist(before.position_xyz, after.position_xyz) <= .01000001
        assert 0 < result.solve_time_s < .15
        assert result.candidate_evaluation.candidate_id == case["selection"].selected_candidate_id
        return {"name": name, "input": inputs, "selection": to_dict(case["selection"]),
                "candidates": to_dict(case["resolved"].bundle.candidate_set),
                "solver_config": asdict(solver.solver_config), "problem": outcome.request.problem(),
                "result": to_dict(result), "delta_xyz": delta, "report": json.loads(outcome.report_json)}
    finally:
        assert solver.close().result(3).reaped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Acceptance evidence requires Linux / Python 3.12")
    cases = []
    for count in (1, 2, 16):
        points = [F["point"]((.41, 0, .31+i*.0001), f"candidate-{i:02}") for i in range(count)]
        cases.append(verify_case(f"candidate-count-{count}", {"candidate_points": points}, 1))
    for height, sign in ((.31, 1), (.3, 0), (.29, -1)):
        cases.append(verify_case(f"height-{height}", {"candidate_points": [F["point"]((.41, 0, height))],
                                                     "quaternion": (.6, 0, 0, .8)}, sign))
    cases.append(verify_case("alpha0-after-z-and-angular-motion", {"alpha_zero": True,
                              "previous": {"velocity": (0, 0, .04), "angular": (0, 0, .3)}}, 0))
    report = {"scope": "Synthetic supervised solver evidence; no perception, consumer or robot execution",
              "source_revision": args.source_revision, "wheel_sha256": args.wheel_sha256,
              "environment": environment(), "cases": cases}
    Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"output": args.output, "cases": len(cases), "status": "passed"}))


if __name__ == "__main__":
    main()
