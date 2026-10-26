# Bounded short-horizon optimizer

`ssc-core==0.8.0` implements P1-12 / [issue #14](https://github.com/ZenWang00/semantic-shared-control/issues/14):
the `solver-slsqp-v1` objective, pose parameterization, solver, budgets and
tolerances of [offline-reference-v1 §4–6](wiki/Offline-Reference-Profile.md).
It proposes one target pose or returns a reasoned hold. It grants no motion
authority. The robot-side gate must still check authority, freshness, ordering
and host constraints. Schema 1.0, `geometry-residual-v1`, frames, units and the
profile identities are unchanged.

## Use

P1-17 adds opt-in `solver-slsqp-batch-v2` in `ssc-core==0.10.0`.
Pass `solver_config=get_solver_config("solver-slsqp-batch-v2")` to
`SupervisedOptimizer`. It evaluates a complete candidate in one isolated
256 MiB worker and one supervised IPC call, with **10 ms for the whole batch**,
not per program. The 16-program maximum, 150 ms total budget and all SLSQP
numerics are unchanged. Default lookup still selects the original v1 below.
See the versioned [batch profile](wiki/Offline-Batch-Profile.md) for identities,
failure rules, full-count verification and consumer handoff. Old archives and
scalar receipt/replay evaluation retain their original behavior.

```python
from ssc_core import HostCheckerSpec, SupervisedOptimizer, estimate_intent, get_intent_config

optimizer = SupervisedOptimizer(HostCheckerSpec("ssc_runtime.constraints:SyntheticHostChecker"))
# While motion is disabled (initialization, or PAUSED after a computation failure):
assert optimizer.prepare(resolved.bundle, motion_disabled=True).result(10).ok

config = get_intent_config("intent-linear-v1")
intent = estimate_intent(resolved, config, weights_id=weights_id, policy=NUMERIC_OFFLINE_V1)
if intent.disposition == "active":
    future = optimizer.solve(resolved, intent.weights, intent_config=config,
                             command_seq=dispatch.command_seq,          # host-allocated
                             previous_applied=applied_reference,        # or None
                             deadline_ns=expiry_in_monotonic_ns)        # required for coordinators
    outcome = future.result()   # never in input, release or gate callbacks
    message_payload = outcome.result  # ControlResult: move proposal or hold
```

Coordinators must pass `deadline_ns`: the snapshot's original expiry mapped to
`time.monotonic_ns`. It can only shorten the 150 ms budget. The parameter is
optional for tests and harnesses only; without it, a delayed dispatch could
finish after the snapshot expired (the gate would still reject that result).

`resolved` is the snapshot producer's `ResolvedSnapshot` (`FrozenSnapshot.resolved`).
The worker echoes the dispatch `command_seq`; it never allocates command order.
`previous_applied` is an `AppliedCommandReference(result, acceptance, source)`
built from the adapter's accepted-command record: the exact ControlResult
message, its accepted `CommandAcceptanceRecord` and, for a move, its source
`ResolvedSnapshot`. Neutral or disabled input must use the existing local
hold path; if passed anyway it returns a `not_run` hold.

## Process topology (owner decision, 26 October 2026)

The host (one `SupervisedOptimizer`) supervises one solver process,
`python -I -m ssc_core._solver_worker`, started in its own process group with
BLAS thread counts of one. The worker applies `RLIMIT_AS` = 1 GiB and a zero
core limit, verifies them, then imports NumPy/SciPy. During `prepare`, it
creates one `SupervisedEvaluator` per bound program; each is a separately
spawned 256 MiB evaluator with the 10 ms call budget. Generated source never
runs in the solver or host process. The rejected alternatives were
host-brokered evaluation (two IPC hops) and an in-process interpreter, which
would need a new profile identity. The decision record is the
[#14 claim comment](https://github.com/ZenWang00/semantic-shared-control/issues/14#issuecomment-6417797254).

The host checker is created from trusted configuration, a `HostCheckerSpec`
naming a zero-argument `module:attribute` factory. Both the solver process
(solver constraints) and the host (final recheck) import it from installed
packages. Never build a spec from a message. Server tests use the private
`ssc_core._test_doubles` doubles and never import the robot repository.

## Validation before solving

`build_request` (also used by `solve`) is pure and replayable. It rejects with
a stable code, and `solve` turns every rejection into a `not_run` hold:

- Weights must equal `estimate_intent` for this exact snapshot. The check covers
  IDs, order, the 1e-12 weight tolerance and an exactly equal `assistance_strength`.
  The estimator recheck also validates every pinned snapshot link.
- `dt_s` must equal the session control period, and state frames must match.
- Each of the 1–16 programs needs exactly one binding, an accepted-status receipt
  for its exact hash, and a weighted candidate. They must equal the prepared
  context (`context_mismatch` otherwise). Receipt *trust* remains the activation
  boundary's job.
- Previous reference rules:
  - Null gives zero reference.
  - A hold gives zero reference.
  - A same-session move from an older epoch gives zero reference.
  - A same-epoch move gives `x_prev = v·dt/L` and `r_prev = ω·dt/A`, where `v` and `ω`
    come from its own source state and target over its horizon.
  - These are errors: a supplied reference without an ID, a missing record, an
    unaccepted or ID-mismatched acceptance, another session, a later epoch, and
    a move without its exact source snapshot.

The request keeps identities and canonical sorted-key `problem_json` for archival (#17).

## Objective and solver

With `L = max_linear_speed·dt`, `A = max_angular_speed·dt`, measured `p,R` and
zero initialization of six variables `x,r`:

- `p' = p + L x`; `R' = Exp([A r]×) R`. The rotation is left-multiplied in the
  control frame. The emitted quaternion is normalized xyzw with a nonnegative scalar.
- `J = ‖x_xy − b‖² + 0.1(‖x − x_prev‖² + ‖r − r_prev‖²) + α Σ_i w_i Σ_j λ_j max(0, g_j/s_j)²`,
  with `b = (max_xy_speed·dt/L) u`.
- Every generated relation is evaluated in its evaluator at every candidate.
  With α = 0, they are still evaluated, so a failure still invalidates the solve.
- Inequalities are the profile's three basic margins `m²−‖x‖²`, `m²−‖r‖²` and
  `û·x_xy`, plus all injected checker margins.
- Solver call: SciPy `minimize(method="SLSQP", jac=None, eps=1e-6, ftol=1e-9,
  maxiter=100)` with bounds [−1, 1]⁶. There is no warm start and no parallel
  finite differences.

At a successful point, the worker recomputes from the *emitted* target, the
same way the gate does. It recomputes all basic margins plus the six box margins,
reruns the checker and re-evaluates every program. The host then recomputes the
basic margins (standard library) and calls its own checker instance at that
target. It also re-derives each penalty with `residual_diagnostic`. It never
trusts margins or poses reported by the worker. Every hard margin must be ≥ −1e‑8.

## Outcomes

| Outcome | ControlResult | Solver after |
| --- | --- | --- |
| solved, rechecks pass | `move`, `evaluation_valid=true`, `solver_status=solved`, `constraints_satisfied = all(raw_g ≤ 1e-9)`, final residuals/penalties, `horizon_s == snapshot.dt_s` | ready |
| SLSQP incompatible constraints, or a returned/rechecked hard-infeasible point | `hold`, `infeasible` | ready |
| deadline, iteration limit (status 9), evaluator timeout or memory limit, close | `hold`, `timeout`, `evaluation_valid=false` | retired |
| evaluator domain/division/crash, nonfinite objective, checker exception, other SLSQP failure, worker crash, malformed reply | `hold`, `error` | retired |
| invalid input, inactive input, not prepared, busy, closed | `hold`, `not_run`, `solve_time_s=0` | unchanged |

A hold never has a pose or horizon, never reuses a partial iterate or a
previous target, and has an empty `constraint_evaluations`. A positive finite
residual with a feasible step is a normal `move` with
`constraints_satisfied=false`. Holds record why in
`reason = "<code>: <diagnostic>"`. `OptimizationOutcome` also retains the
worker report: increments, objective terms, final residuals, all margins,
SciPy status/iterations, evaluator call statistics, failing evaluator and
host-recheck margins. After a retirement, call `prepare` again while motion is
disabled.

## Deadlines and teardown

The real monotonic deadline is `min(start + 0.15 s, deadline_ns)`. It applies
to validation, IPC, SLSQP, every evaluator call (`min(10 ms, deadline)`) and
the host recheck. A move decided at or after the deadline becomes a `timeout`
hold; a late reply never extends the original expiry. `solve` returns after
validation only. The supervisor thread owns all pipe waits.

On timeout or a protocol failure, the host closes the pipes and terminates the
whole process group. After the 0.1 s grace it kills the group, then reaps the
leader. A worker that replied with a classified failure gets an orderly EOF
exit first, and closes and reaps its own evaluators. The Future therefore
completes after a bounded teardown of roughly budget plus two grace periods.
That teardown grants no motion time: the coordinator's own deadline and hold
path are authoritative. Evaluators orphaned by a killed or crashed worker are
killed with the group and reaped by the system init process. In a container
where the host process itself runs as PID 1, use an init (`docker run --init`)
or a subreaper so those evaluators do not remain as zombies.

`close()` is final. If a solve or preparation has not committed its outcome,
it withdraws that operation and returns its Future; the cancelled outcome is
delivered after a bounded retirement attempt. The result may therefore be an
`OptimizationOutcome` or a `PreparationOutcome`. Cancellation/deadline checking,
the final status and removal of the active operation commit under one lock.
Errors discovered by the host recheck retire the worker just like worker errors.

Future delivery and callbacks run outside that lock. A `close()` after commit
returns a separate retirement Future, even if delivery of the committed outcome
is still pending; it cannot retroactively change that outcome. The local gate
must still reject expired or withdrawn authority when consuming any proposal.

## Evidence

Run on Linux against the installed wheel:

```sh
python -m pytest tests/shared_control/test_optimization_math.py tests/shared_control/test_optimization.py
python -B docs/reference/verify_optimizer.py --output optimizer-evidence.json \
  --source-revision TESTED_GIT_SHA --wheel-sha256 TESTED_WHEEL_SHA256
```

CI runs both and uploads `optimizer-evidence.json` with the wheel and JUnit.
The harness drives real solver and evaluator processes with the local checker
double:

- **12 profile cases.** Joystick-only/α=0, positive approach, axis alignment,
  reversal with an applied rightward reference, within tolerance, workspace face
  and excluded sphere favoured by a soft relation, infeasible start (twice, see
  below), division error, neutral input and disabled input. Each case checks the
  oracle within 1e‑6 m and 1e‑5 rad, identity echo, and a gate-equivalent margin
  recheck.
- **Infeasible start and the budget.** This fixture needs about 177 evaluator
  calls. That took about 129 ms in CI but more than 150 ms on a slower 2-vCPU host,
  where the result becomes a `timeout` hold instead of `infeasible`. The owner
  decided (26 October 2026) to relax the time limit for this check only:
  `infeasible-host` and `test_infeasible_hold_keeps_solver_ready` run the same
  `solver-slsqp-v1` content with a test-only 1 s total budget
  (`relaxed_solve_budget` in `optimizer_fixtures.py`; the 10 ms per-call evaluator
  budget is unchanged), and the evidence records `solve_budget_s` and
  `budget_kind` per case. `infeasible-host-profile-budget` and
  `test_infeasible_start_under_profile_budget_is_a_hold_without_target` run it at
  the profile's 150 ms and accept `infeasible` or `timeout`, always as a hold
  without target. The profile configuration itself is unchanged.
- **5 boundary faults.** Solver stall ignoring SIGTERM, late reply, evaluator
  stall, solver crash, and a worker that misreports its pose.
- **Fresh-process replay.** Every emitted residual is replayed in a fresh
  isolated process within atol = rtol = 1e‑9. Each request is rebuilt identically.
- **Measurements.** A 100-solve latency sample and a 2/4/8/16-program scaling sample.

| Matrix row | Component evidence |
| --- | --- |
| G01 | Positive finite residual moves; penalty retained; `constraints_satisfied=false` |
| G02 | Relation within tolerance: zero penalty, negative raw residual retained |
| G06 | Division by zero at the measured pose → `error` hold, `evaluation_valid=false`, no target |
| G07 | Deadline, stall, late reply, evaluator stall and infeasible steps → hold without target |
| C08 | Soft targets beyond the workspace face and inside the excluded sphere stay within host margins |

Development measurements (cloud Linux x86_64 sandbox, Python 3.12.3, NumPy 1.26.4,
SciPy 1.11.4; not the reference VM):

- **Latency.** For two programs, end-to-end solve time over 100 solves was
  21 ms minimum, 31 ms median, 51 ms p95 and 122 ms maximum, with 44 evaluator
  calls per solve. One sample hit the 10 ms per-call evaluator budget and became
  a `timeout` hold.
- **Scaling.** Median solve times were 28 ms for 2 programs, 85 ms for 4 and
  83 ms for 8. With **16 programs, every solve timed out at 150 ms.**
- **Teardown.** A stalled solver completed its Future about 255 ms after
  admission (150 ms deadline plus forced teardown).

These are observations, not real-time guarantees.

## Limitations and handoff

- **Program budget (accepted limitation, owner decision 26 October 2026).**
  The profile allows 16 programs per snapshot, but each objective evaluation
  calls every program's evaluator once. Solve cost therefore scales with
  programs × evaluated candidate poses. The original two-program reversal
  fixture reports 44 total evaluator calls, while the one-program infeasible
  start reports about 177 total calls. Measured in CI: 2 programs ≈ 28 ms; 4 ≈ 80 ms;
  8 ≈ 90 ms; every 16-program solve timed out. On a slower, loaded 2-vCPU host
  some 4- and 8-program solves already timed out. Over-budget solves fail closed
  as `timeout` holds and retire the worker. Follow-up
  [#30](https://github.com/ZenWang00/semantic-shared-control/issues/30)
  introduces the opt-in batch v2 topology and full-count measurement. Consumers
  still using v1 should keep to 2–4 programs per snapshot; a library upgrade
  alone does not select the new topology. V2 consumers must select its solver
  and configuration identities and verify their pinned installed version pair.
- **Integration evidence.** This is component software evidence with a local
  checker double. It does not cover the consumer's `SyntheticHostChecker`, the
  real gate, the plant or the coordinator. The installed version pair and the
  complete loop belong to
  [Grasp-Pose-Generation#6](https://github.com/ZenWang00/Grasp-Pose-Generation/issues/6).
- **Archival.** Replay archival (#17) consumes `OptimizationRequest.problem_json`,
  the solver configuration, `report_json`, `evaluator_json` and the ControlResult.
- **Isolation scope.** Process and address-space isolation is not a hostile-code
  sandbox. No physical safety, control rate or research benefit is claimed.
