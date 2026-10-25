"""Private stdlib-only evaluator process. The host never unpickles its replies."""
import resource
import sys
import time

from .supervision import EVALUATOR_LIMITS, _decode, _encode


def _limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    bound = EVALUATOR_LIMITS.address_space_bytes
    resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
    actual = {"address_space": list(resource.getrlimit(resource.RLIMIT_AS)),
              "core": list(resource.getrlimit(resource.RLIMIT_CORE))}
    if actual != {"address_space": [bound, bound], "core": [0, 0]}:
        raise RuntimeError("resource limits not applied")
    return actual


def _evaluate(compiled, inputs):
    # Private seam for fault injection, never selected by request/source data.
    return compiled.evaluate(*inputs)


def main():
    limits = _limits()  # fail preparation on any unsupported/unenforceable setting
    from .interpreter import InterpreterError, compile_program
    from .policy import NUMERIC_OFFLINE_V1
    compiled = None
    generation = None
    while True:
        line = sys.stdin.buffer.readline(EVALUATOR_LIMITS.payload_bytes + 2)
        if not line:
            return
        if len(line) > EVALUATOR_LIMITS.payload_bytes + 1 or not line.endswith(b"\n"):
            return
        request = _decode(line)
        started = time.monotonic_ns()
        reply = {"request_id": request["request_id"], "generation": request["generation"],
                 "ok": False, "code": "worker_exception", "stage": "worker",
                 "diagnostic": "", "residual": None, "worker_elapsed_s": 0., "limits": limits}
        try:
            if request["operation"] == "prepare" and compiled is None:
                compiled = compile_program(request["program"], policy=NUMERIC_OFFLINE_V1)
                generation = request["generation"]
            elif request["operation"] == "evaluate" and compiled is not None and generation == request["generation"]:
                reply["residual"] = _evaluate(compiled, request["inputs"])
            else:
                raise ValueError("invalid worker lifecycle")
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
            # Let the supervisor consume failure and own teardown/reaping. Waiting
            # avoids racing a process-exit check against a buffered failure reply.
            sys.stdin.buffer.read(1)
            return


if __name__ == "__main__":
    main()
