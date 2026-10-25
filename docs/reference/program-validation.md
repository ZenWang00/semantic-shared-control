# Host program acceptance evidence — issue #9

`ssc-core==0.6.0` composes host-reviewed unit declarations, immutable reference
resolution, bounded numerical probes, local receipt trust and execution bindings.
The owner approved the host-review mechanism in [issue #9](https://github.com/ZenWang00/semantic-shared-control/issues/9).
This is offline semantic/numerical component evidence, not operator-intent,
complete-loop, controller or physical-safety validation.

## Provenance and reproduction

- Implementation head: `16724a9edaae13525817d10f2174eb0be15111b2`.
- Tested GitHub PR merge: `f110457bf77b16cc0c5593da7bb029dd6ce7b97f`, with parents
  `7d67b958cbe31ad51e0365929010ba5ba619c33f` and the implementation head.
- Both trees: `bcc0d6697d89d91c22b0ce9fb69ab05b24699dcf`.
- [Ubuntu CI run](https://github.com/ZenWang00/semantic-shared-control/actions/runs/40246707891)
  built and installed a clean wheel, ran tests and both evidence harnesses, and
  retained wheel, dependency versions, JUnit, hashes and complete reports.
- Wheel SHA-256: `6fe2d398f2ca48de4fac4fadcc0a59d90f2e81495f0cf244ab675a44d0535470`.
- Ubuntu 24.04 runner, Linux `6.17.0-1022-azure`, x86_64, glibc 2.39;
  CPython 3.12.15, NumPy 1.26.4, SciPy 1.11.4, pytest 7.4.4.
- Full installed-wheel suite: **982 passed, 2 skipped**, 0 errors/failures;
  skips are the two checks for explicit rejection on non-Linux platforms.
  `pip check`: no broken requirements.
- Complementary macOS installed-wheel suite: **945 passed, 39 skipped**;
  it does not establish Linux resource enforcement.

The byte-for-byte [raw program-validation report](program-validation.json)
retains complete program/geometry inputs, host reviews and normalization,
receipts, bindings, named numerical probes, real monotonic worker deadlines,
resource limits, cleanup outcomes and replay/analytical comparisons. Its
`source_revision` identifies the tested merge rather than a later evidence-only
commit. The existing isolation harness also passed in this run.

To reproduce from a checkout with its clean wheel installed:

```sh
python -m pytest tests/shared_control --junitxml=test-results.xml
python -m pip check
python -B docs/reference/verify_program_validation.py \
  --source-revision TESTED_GIT_SHA --wheel-sha256 TESTED_WHEEL_SHA256 \
  --output program-validation-evidence.json
```

## Results

| Fixture/case | Expected and actual result | Retained check |
| --- | --- | --- |
| Distance | accepted | 50 evaluations match analytic distances and replay |
| Directed axis | accepted | 51 evaluations match analytic angles and replay; opposed direction allowed |
| Undirected axis | accepted | 98 evaluations, including 49 individual-axis reversals, match analytic values and replay |
| Noninvariant undirected axis | rejected | `axis_sign_invariance`, 1 versus -1 |
| Division domain failure | rejected | `division_by_zero` in the worker |
| Changed final newline | rejected | `source_hash_mismatch` before worker preparation |
| Wrong normalization unit | rejected | `unit_mismatch` before probing |
| Missing host review | rejected | `missing_unit_review` before probing |
| Worker crash | rejected | `worker_crash`, no scalar, retirement confirmed |
| Worker stall | rejected | `timeout` at the original deadline, retirement confirmed |
| Worker memory exhaustion | rejected | `memory_limit` under RLIMIT_AS, retirement confirmed |

All **11 cases** and **199 analytic/replay comparisons** matched. Each accepted
case also issued an execution binding through the authority. The Wiki distance
examples retained raw `.008` / `-.002`, scale `.01`, and penalties
`.6400000000000001` / `0`, matching `.64` / `0` within `1e-9`.

The pytest suite separately checks source/parameter/binding-order/declaration
immutability; malformed ABI/unit/shape boundaries; foreign references and frame/
policy mismatch; untrusted/imported receipts; changed scene/feature definitions;
coordinate-only receipt reuse; host settings and positive finite penalties.
Trust/identity tests explicitly stub numerical success; the Linux acceptance
checks and this report exercise real bounded workers. Neither replaces the other.

## Limits and consumers

A UnitReview is a trusted host assertion that a specific formula and its feature
meaning were reviewed. It is not generated metadata, automated dimensional
inference or proof of operator intent. Finite probes cannot establish every
future candidate. Runtime checks and static-scene reinitialization remain
mandatory. No numeric policy or resource limit was increased to obtain this run.

Only the issuing authority's in-memory ledger establishes local receipt trust.
Imported records and archived reports cannot restore it after restart; revalidate.
This is not cryptographic attestation or isolation from arbitrary trusted-host
code. The ROS2 activation owner must combine registry compatibility with receipt
and binding verification; the optimizer re-evaluates every candidate, and replay
retains exact artifact/review/configuration provenance. No public wire field,
frame, unit convention or motion authority changed.
