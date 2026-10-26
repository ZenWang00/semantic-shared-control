"""Private stdlib-only candidate-batch interpreter, isolated from solver/host.

One failed program discards the entire batch. The external supervisor bounds
serialization, all programs and reply publication together to 10 ms, with the
same 256 MiB address-space and bounded JSON transport as scalar evaluation.
"""
import sys
import time

from ._worker import _limits
from .supervision import EVALUATOR_LIMITS, _decode, _encode


def _compile(payload):
    from .interpreter import compile_program
    from .policy import NUMERIC_OFFLINE_V1
    programs = payload["programs"]
    if type(programs) is not list or not 1 <= len(programs) <= 16:
        raise ValueError("batch requires 1-16 programs")
    compiled = [compile_program(p, policy=NUMERIC_OFFLINE_V1) for p in programs]
    if len({p.program.program_id for p in compiled}) != len(compiled):
        raise ValueError("duplicate batch program identity")
    return compiled


def _evaluate(compiled, inputs):
    from .interpreter import InterpreterError
    position, rotation, bindings = inputs
    if type(bindings) is not list or len(bindings) != len(compiled):
        raise ValueError("incomplete batch bindings")
    values = []
    for program, binding in zip(compiled, bindings):
        if (type(binding) is not dict
                or set(binding) != {"program_id", "keypoints", "axis_dirs", "params"}
                or binding["program_id"] != program.program.program_id):
            raise ValueError("batch program identity/order mismatch")
        try:
            values.append(program.evaluate(position, rotation, binding["keypoints"],
                                           binding["axis_dirs"], binding["params"]))
        except InterpreterError as exc:
            # Keep the responsible program while returning no partial values.
            exc.message = f"{program.program.program_id}: {exc.message}"
            raise
    return values


def main():
    limits = _limits()
    from .interpreter import InterpreterError
    compiled = generation = None
    while True:
        line = sys.stdin.buffer.readline(EVALUATOR_LIMITS.payload_bytes + 2)
        if not line or len(line) > EVALUATOR_LIMITS.payload_bytes + 1 or not line.endswith(b"\n"):
            return
        request = _decode(line)
        started = time.monotonic_ns()
        reply = {"request_id": request["request_id"], "generation": request["generation"],
                 "ok": False, "code": "worker_exception", "stage": "worker", "diagnostic": "",
                 "residual": None, "worker_elapsed_s": 0., "limits": limits}
        try:
            if request["operation"] == "prepare" and compiled is None:
                compiled = _compile(request["program"])
                generation = request["generation"]
            elif request["operation"] == "evaluate" and compiled is not None and generation == request["generation"]:
                reply["residual"] = _evaluate(compiled, request["inputs"])
            else:
                raise ValueError("invalid batch lifecycle")
            reply["ok"], reply["code"] = True, "ok"
        except InterpreterError as exc:
            reply.update(code=exc.code, stage=exc.stage, diagnostic=f"{exc.path}: {exc.message}"[:512])
        except MemoryError:
            reply.update(code="memory_limit", diagnostic="allocation refused under RLIMIT_AS")
        except Exception as exc:
            reply.update(code="worker_exception", diagnostic=type(exc).__name__)
        reply["worker_elapsed_s"] = (time.monotonic_ns() - started) / 1e9
        sys.stdout.buffer.write(_encode(reply))
        sys.stdout.buffer.flush()
        if not reply["ok"]:
            sys.stdin.buffer.read(1)
            return


if __name__ == "__main__":
    main()
