"""Private solver-slsqp-batch-v2 supervisor: 10 ms for a complete candidate.

The scalar public evaluator is unchanged. Reuse its admission, deadline,
bounded IPC, cancellation and retirement machinery; only the trusted worker
module and successful result shape differ. Never selected by generated data.
"""
import math

from .supervision import SupervisedEvaluator, _Failure


class BatchEvaluator(SupervisedEvaluator):
    _worker_module = "ssc_core._batch_worker"

    def prepare(self, programs, *, motion_disabled):
        return super().prepare({"programs": programs}, motion_disabled=motion_disabled)

    def _operate(self, future, operation, payload, request_id, started, deadline):
        # Only an admitted preparation may replace the expected reply shape.
        if operation == "prepare":
            programs = payload.get("programs") if type(payload) is dict else None
            self._count = len(programs) if type(programs) is list else 0
        return super()._operate(future, operation, payload, request_id, started, deadline)

    def submit_batch(self, position, rotation, terms, *, deadline_ns, _inline=False):
        bindings = [{key: term[key] for key in ("program_id", "keypoints", "axis_dirs", "params")}
                    for term in terms]
        return self._start("evaluate", (position, rotation, bindings), deadline_ns=deadline_ns, _inline=_inline)

    def _normalize_residual(self, residual):
        if (type(residual) is not list or len(residual) != self._count
                or not all(type(g) in (int, float) and math.isfinite(g) for g in residual)):
            raise _Failure("malformed_response", "batch requires one finite scalar per prepared program")
        return tuple(residual)
