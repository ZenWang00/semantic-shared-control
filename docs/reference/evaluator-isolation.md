# Evaluator isolation acceptance evidence

This is offline Linux process/resource evidence for issue #8, not a control-rate,
physical stopping, hardware-safety or general hostile-code sandbox claim.
The host uses the unchanged `numeric-offline-v1` policy. Receipt issuance,
solver supervision and the robot-side watchdog/gate remain separate work.

## Reproduction and provenance

- Implementation/test revision: `dd028966c96fa52f7a477f9c55d81fc3937c1550`.
- Installed wheel: `ssc_core-0.5.0-py3-none-any.whl`, SHA-256
  `c5f73746d39b5c88e194b9d8dae06508379bcdca8863c8b3de48cf51f3ee50fd`.
- The wheel was built from `git archive` of that revision with the pyproject's
  pinned setuptools 75.8.0 and wheel 0.45.1, then installed with `--no-deps` into
  a new Ubuntu Python venv using the VM's existing reference dependencies.
- Ubuntu 24.04.4 LTS, aarch64, Linux 6.17.0-35-generic, Python 3.12.3,
  NumPy 1.26.4, SciPy 1.11.4, pytest 7.4.4; existing local VMware CPU VM.
- `python -m pytest source/tests/shared_control -q --import-mode=importlib
  --junitxml=full-tests.xml`: **916 passed, 1 skipped in 8.12 s**.
  The skip is the unsupported/non-Linux development check; all Linux enforcement
  tests ran. `python -m pip check`: no broken requirements.
- [Machine-readable measurements](evaluator-isolation.json) retain all 98 probe
  inputs/oracles/outcomes and seven injected fault outcomes, with request,
  generation, PID, policy, limits, real timestamps, environment and cleanup.
  The source revision points to the implementation; this report is a later
  documentation-only addition.

From that revision's checkout and installed wheel:

```sh
python -B docs/reference/verify_evaluator_isolation.py \
  --source-revision dd028966c96fa52f7a477f9c55d81fc3937c1550 \
  --wheel-sha256 c5f73746d39b5c88e194b9d8dae06508379bcdca8863c8b3de48cf51f3ee50fd \
  --output evaluator-evidence.json
```

CI builds/installs a fresh wheel and runs the same regression and benchmark,
retaining its wheel, JUnit, dependencies, checksum and JSON as workflow artifacts.
Its x86 Linux measurements supplement, rather than replace, the approved VM.

## Observed results

The profile's seven translation poses crossed with seven orientations produce
49 probes for each of the distance and directed-axis relations. All **98**
finite scalars matched independent mathematical oracles within `atol=rtol=1e-9`.
End-to-end host admission-to-completion latency (including serialization/IPC,
excluding preparation) was minimum **0.0914 ms**, median **0.1624 ms**,
95th percentile **0.5540 ms**, maximum **1.2326 ms**. The configured 10 ms budget
was enforced unchanged; this sample does not establish a worst-case bound.

| Injected boundary fault | Observed reason | Completion incl. cleanup | Cleanup |
| --- | --- | --- | --- |
| Stall, ignoring SIGTERM | timeout | 111.50 ms | 101.24 ms |
| Delayed reply, ignoring SIGTERM | timeout | 74.10 ms | 63.85 ms |
| 512 MiB allocation under 256 MiB RLIMIT_AS | memory_limit | 1.33 ms | 1.10 ms |
| Process exit 17 | worker_crash | 0.67 ms | 0.016 ms |
| Unexpected exception | worker_exception | 1.32 ms | 1.12 ms |
| Partial JSON reply | timeout | 11.22 ms | 1.11 ms |
| Old request identity | malformed_response | 1.33 ms | 1.10 ms |

All failures had no scalar and completed reaping. Both per-worker pipe handles
were closed in each fault measurement (host descriptor count 6 to 4). A separate
1 ms heartbeat recorded **92 ticks** during the stalled request, with maximum
observed gap **1.278 ms**; `submit` returned in **0.145 ms**. The delayed worker's
reply remained rejected, and tests verify a freshly prepared worker succeeds.
The 100 ms SIGTERM grace precedes SIGKILL; observed cleanup includes scheduling
and kill/reap overhead. A bounded additional 100 ms reap wait detects cleanup
failure without granting extra evaluation time. No indefinite wait is used.

## Acceptance coverage

Installed-wheel tests inspect `/proc/PID/limits` and `/proc/PID/environ`, exercise
a real allocation refusal, and establish that the evaluator PID differs from
the host. The worker reports exact equal soft/hard address-space limits and zero
core limits before readiness; a malformed limit report fails closed. RLIMIT_AS
limits virtual address space, not resident memory.

The suite covers source/AST/assignment and fixed-shape rejection, invalid
rotation/Boolean/nonfinite inputs, domain/division failures, oversized IPC,
original deadline expiry, crash, exception, wrong request/generation, Boolean
scalar, bad limit report, malformed/oversized/duplicate/partial replies, stalled
preparation, disabled-motion preparation, busy rejection, explicit recovery and
close/cancellation. Twenty successive worker crashes leave no child or pipe
handle behind. Normal source cannot select fault injection; the trusted test
harness replaces a private process seam.

Four deterministic host-state tests additionally cover close at the completion
boundary, a deadline expiring after response validation, callback responsiveness
during retirement and supervisor-thread allocation failure. These tests isolate
race/order semantics; their controlled clock is not used as wall-time enforcement
evidence. The separate real Linux tests and measurements above provide that.

Successful compilation remains a grammar/shape check, not a validation receipt.
The immutable outcome API carries policy/timing/failure evidence for #9 and #14;
those modules still resolve context, issue receipts and enforce their own
original deadlines. No ROS2 consumer version-pair or complete-loop acceptance
is claimed by this component delivery.
