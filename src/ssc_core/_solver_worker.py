"""Private solver process: SLSQP plus separately supervised residual evaluation.

The host launches ``python -I -m ssc_core._solver_worker`` in a new process group.
This process limits its own address space to 1 GiB, then imports NumPy/SciPy.
Generated programs never execute here: each candidate residual comes from a
separately spawned 256 MiB / 10 ms child: one per program in v1, one complete
candidate batch in v2. Preparation happens while motion is disabled.
The host checker is created from
a trusted ``module:attribute`` factory named in the preparation request.
"""
import importlib
import resource
import sys
import time

from .supervision import _decode, _encode

ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
PAYLOAD_BYTES = 1024 * 1024
SOLVER_CONFIG_ID = "solver-slsqp-v1"
_RETAINED = {"ready", "solved", "infeasible"}


def _limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    actual = {"address_space": list(resource.getrlimit(resource.RLIMIT_AS)),
              "core": list(resource.getrlimit(resource.RLIMIT_CORE))}
    if actual != {"address_space": [ADDRESS_SPACE_BYTES] * 2, "core": [0, 0]}:
        raise RuntimeError("resource limits not applied")
    return actual


def _new_evaluator():
    # Private seam for trusted fault-injection tests; never selected by request data.
    from .supervision import SupervisedEvaluator
    return SupervisedEvaluator()


def _solve(problem, evaluate, host_margins, deadline_ns):
    # Private seam for trusted fault-injection tests.
    from ._solver_math import solve_problem
    return solve_problem(problem, evaluate, host_margins, deadline_ns=deadline_ns)


def _load_checker(spec):
    module_name, _, attribute = spec.partition(":")
    factory = getattr(importlib.import_module(module_name), attribute)
    checker = factory()
    if not callable(checker):
        raise TypeError("checker factory did not return a callable")
    return checker


class _Worker:
    def __init__(self, limits):
        self.limits = limits
        self.generation = None
        self.evaluators = {}
        self.checker = None
        self.environment = {}
        self.batch = None

    def close_evaluators(self):
        for evaluator in dict.fromkeys(self.evaluators.values()):
            try:
                evaluator.close().result(1)
            except Exception:
                pass  # The host's process-group teardown remains authoritative.

    def prepare(self, request):
        if self.generation is not None:
            raise ValueError("invalid worker lifecycle")
        if request["solver_config_id"] not in (SOLVER_CONFIG_ID, "solver-slsqp-batch-v2"):
            raise ValueError("unsupported solver configuration")
        deadline = request["deadline_ns"]
        import numpy
        import scipy
        importlib.import_module(f"{__package__}._solver_math")  # fail preparation on import problems
        self.environment = {"numpy": numpy.__version__, "scipy": scipy.__version__}
        self.checker = _load_checker(request["checker"])
        evaluators = {}
        try:
            programs = request["programs"]
            if request["solver_config_id"] == "solver-slsqp-batch-v2":
                from ._batch_evaluator import BatchEvaluator
                self.batch = BatchEvaluator()
                evaluators = {p["program_id"]: self.batch for p in programs}
                remaining = max(0., (deadline - time.monotonic_ns()) / 1e9)
                outcome = self.batch.prepare(programs, motion_disabled=True).result(remaining + 1)
                if not outcome.ok:
                    raise _Failed("error", f"evaluator_{outcome.code}", outcome.diagnostic)
                if time.monotonic_ns() >= deadline:
                    raise _Failed("timeout", "preparation_deadline", "batch preparation exceeded its budget")
            for program in (() if self.batch is not None else programs):
                evaluator = _new_evaluator()
                evaluators[program["program_id"]] = evaluator
                remaining = max(0., (deadline - time.monotonic_ns()) / 1e9)
                outcome = evaluator.prepare(program, motion_disabled=True).result(remaining + 1)
                if not outcome.ok:
                    raise _Failed("error", f"evaluator_{outcome.code}",
                                  f"{program['program_id']}: {outcome.diagnostic}"[:400])
                if time.monotonic_ns() >= deadline:
                    raise _Failed("timeout", "preparation_deadline", "evaluator preparation exceeded its budget")
        except BaseException:
            self.evaluators = evaluators
            raise
        self.evaluators = evaluators
        self.generation = request["generation"]
        pids = {key: value._process.pid for key, value in evaluators.items() if value._process}
        return {"status": "ready", "code": "ready", "evaluator_pids": pids,
                "environment": self.environment}

    def solve(self, request):
        from ._solver_math import SolveFailure
        from .host_constraints import HostConstraintResult, MeasuredStateView
        from .policy import NUMERIC_OFFLINE_V1
        from .records import Pose, SessionConfig
        from .validation import parse_record

        if self.generation != request["generation"]:
            raise ValueError("invalid worker lifecycle")
        problem, deadline = request["problem"], request["deadline_ns"]
        if {term["program_id"] for term in problem["terms"]} != set(self.evaluators):
            raise ValueError("problem programs differ from prepared evaluators")
        state = problem["measured_state"]
        view = MeasuredStateView(state["frame_id"], state["tcp_frame"],
                                 parse_record(Pose, state["tcp_pose"], policy=NUMERIC_OFFLINE_V1),
                                 tuple(state["joint_names"]), tuple(state["joint_positions_rad"]))
        config = parse_record(SessionConfig, problem["configuration"], policy=NUMERIC_OFFLINE_V1)
        u_xy, dt = tuple(problem["u_xy"]), problem["dt_s"]
        stats = {"calls": 0, "max_elapsed_s": 0., "total_elapsed_s": 0.}
        first_batch = True

        def evaluate_batch(terms, p_tcp, rotation):
            nonlocal first_batch
            outcome = self.batch.submit_batch(p_tcp, rotation, terms if first_batch else None,
                deadline_ns=deadline, _inline=True, _scope=request["request_id"]).result(1)
            stats["calls"] += len(terms)
            stats["batch_calls"] = stats.get("batch_calls", 0) + 1
            stats["max_elapsed_s"] = max(stats["max_elapsed_s"], outcome.elapsed_s)
            stats["total_elapsed_s"] += outcome.elapsed_s
            if outcome.ok:
                first_batch = False
                return list(outcome.residual)
            status = "timeout" if outcome.code in ("timeout", "memory_limit", "cancelled") else "error"
            raise SolveFailure(status, f"evaluator_{outcome.code}", outcome.diagnostic[:300],
                               evaluation_valid=False, evaluator={"code": outcome.code,
                               "stage": outcome.stage, "diagnostic": outcome.diagnostic[:200],
                               "elapsed_s": outcome.elapsed_s, "scope": "complete candidate batch"})

        def evaluate(term, p_tcp, rotation):
            outcome = self.evaluators[term["program_id"]].submit(
                p_tcp, rotation, term["keypoints"], term["axis_dirs"], term["params"],
                deadline_ns=deadline).result(1)
            stats["calls"] += 1
            stats["max_elapsed_s"] = max(stats["max_elapsed_s"], outcome.elapsed_s)
            stats["total_elapsed_s"] += outcome.elapsed_s
            if outcome.ok:
                return outcome.residual
            evaluator = {"program_id": term["program_id"], "code": outcome.code,
                         "stage": outcome.stage, "diagnostic": outcome.diagnostic[:200],
                         "elapsed_s": outcome.elapsed_s}
            status = "timeout" if outcome.code in ("timeout", "memory_limit", "cancelled") else "error"
            raise SolveFailure(status, f"evaluator_{outcome.code}", f"{term['program_id']}: {outcome.diagnostic}"[:300],
                               evaluation_valid=False, evaluator=evaluator)

        def host_margins(position, quaternion):
            try:
                pose = parse_record(Pose, {"position_xyz": position, "quaternion_xyzw": quaternion},
                                    policy=NUMERIC_OFFLINE_V1)
                result = self.checker(candidate_pose=pose, measured_state=view, u_xy=u_xy,
                                      dt_s=dt, configuration=config)
                if type(result) is not HostConstraintResult:
                    raise TypeError("checker must return HostConstraintResult")
            except SolveFailure:
                raise
            except Exception as exc:
                raise SolveFailure("error", "host_checker", type(exc).__name__) from None
            return [m.name for m in result.margins], [float(m.value) for m in result.margins]

        try:
            if self.batch is None:
                report = _solve(problem, evaluate, host_margins, deadline)
            else:
                from ._solver_math import solve_problem
                report = solve_problem(problem, evaluate, host_margins, deadline_ns=deadline,
                                       evaluate_batch=evaluate_batch)
            return {"status": "solved", "code": "solved", "evaluation_valid": True, "report": report,
                    "evaluator_stats": stats}
        except SolveFailure as failure:
            return {"status": failure.status, "code": failure.code, "diagnostic": failure.diagnostic,
                    "evaluation_valid": failure.evaluation_valid, "evaluator": failure.evaluator,
                    "report": getattr(failure, "report", None), "evaluator_stats": stats}


class _Failed(Exception):
    def __init__(self, status, code, diagnostic):
        super().__init__(diagnostic)
        self.status, self.code, self.diagnostic = status, code, diagnostic


def main():
    limits = _limits()  # fail preparation if the bound cannot be enforced
    worker = _Worker(limits)
    try:
        while True:
            line = sys.stdin.buffer.readline(PAYLOAD_BYTES + 2)
            if not line or len(line) > PAYLOAD_BYTES + 1 or not line.endswith(b"\n"):
                return
            request = _decode(line)
            started = time.monotonic_ns()
            reply = {"request_id": request["request_id"], "generation": request["generation"],
                     "operation": request["operation"], "status": "error", "code": "worker_exception",
                     "diagnostic": "", "evaluation_valid": False, "report": None, "evaluator": None,
                     "evaluator_stats": None, "evaluator_pids": None, "environment": None}
            try:
                if request["operation"] == "prepare":
                    reply.update(worker.prepare(request))
                elif request["operation"] == "solve":
                    reply.update(worker.solve(request))
                else:
                    raise ValueError("unsupported operation")
            except _Failed as exc:
                reply.update(status=exc.status, code=exc.code, diagnostic=exc.diagnostic)
            except MemoryError:
                reply.update(status="timeout", code="memory_limit", diagnostic="allocation refused under RLIMIT_AS")
            except Exception as exc:
                reply.update(status="error", code="worker_exception", diagnostic=type(exc).__name__)
            reply["worker_elapsed_s"] = (time.monotonic_ns() - started) / 1e9
            reply["limits"] = limits
            sys.stdout.buffer.write(_encode(reply))
            sys.stdout.buffer.flush()
            if reply["status"] not in _RETAINED:
                # The host consumes the classified failure and owns teardown.
                worker.close_evaluators()
                sys.stdin.buffer.read(1)
                return
    finally:
        worker.close_evaluators()


if __name__ == "__main__":
    main()
