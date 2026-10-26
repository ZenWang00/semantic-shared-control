# Deterministic intent fusion

`ssc-core==0.7.0` implements [Issue #12](https://github.com/ZenWang00/semantic-shared-control/issues/12)
using the approved [offline-reference-v1 §3](wiki/Offline-Reference-Profile.md).
The formula and schema 1.0 are unchanged. The library consumes a frozen
`ResolvedSnapshot`, never devices, services, a mutable registry or a clock.

```python
from ssc_core import NUMERIC_OFFLINE_V1, estimate_intent, get_intent_config

configuration = get_intent_config("intent-linear-v1")
outcome = estimate_intent(
    resolved_snapshot, configuration,
    weights_id="weights-for-this-snapshot", policy=NUMERIC_OFFLINE_V1,
)
if outcome.disposition == "active":
    weights = outcome.weights  # May feed a later optimizer, never an actuator.
else:
    # The coordinator uses its existing local hold/disabled path.
    diagnostic = outcome.diagnostic
```

The caller assigns the immutable weights ID and registers the returned record
in the same session for subsequent resolution. Reusing an ID for different
content is rejected by the existing `SessionRegistry`.

## Formula and configuration

The mapped approach point and measured TCP define the unit XY target direction
`d`; norm at most `1e-12 m` gives zero direction. Enabled nonzero current input
defines `u_hat`. `h` is the arithmetic mean of **unit directions** of enabled,
nonzero earlier inputs, or zero for no such input. Disabled/neutral history is
ignored in this mean, but still must pass input/reference checks.

For each semantic candidate:

```text
C = (1 + dot(u_hat, d)) / 2
H = (1 + dot(h, d)) / 2
score = 0.25 + relevance + 2*C + 0.5*H
weight = score / sum(all candidate scores)
```

IDs determine association; output is sorted by object ID. The values are
relative support, not calibrated probabilities. `assistance_strength` is
separate: `0.5` for active input under `intent-linear-v1`, `0` under
`intent-linear-alpha0-v1`. Alpha-zero with active input remains `active`, so a
later optimizer can compute the approved joystick-only objective.

`get_intent_config` returns frozen `IntentConfig` definitions for only those two
approved IDs, bound respectively to `config-offline-v1` and
`config-offline-alpha0-v1`. Each contains the formula coefficients, 32-sample /
1-second history limits, direction epsilon, alpha, and the bottle/cup
object-to-approach-keypoint mapping. Candidate subsets are allowed; every selected
candidate must have its mapping and at least one compatible program. Unknown IDs,
altered content behind a supported ID, or mismatched session configuration fail.
New mappings/coefficients require a new approved configuration and implementation;
this API does not support arbitrary tuning or live configuration registration.
Use `dataclasses.asdict(configuration)` to retain full content for replay.

## Outcomes and validation boundary

| Disposition | Weights | Downstream meaning |
| --- | --- | --- |
| `active` | Valid exact candidate coverage; independent alpha | Further computation allowed by this component only. Host authority/freshness checks remain required. |
| `disabled` | Diagnostic support, alpha exactly zero | Current input is disabled or exactly zero; no motion solve. History never rescues it. |
| `error` | `None` | No usable output; route the diagnostic to the existing failure/hold path. |

The implementation reparses consumed records, validates the configuration,
checks snapshot/task/scene/frame/input identities, exact semantic/program/binding
coverage, source hashes, numerical feature bindings, and compatible receipt
metadata. Invalid or empty candidates fail even on disabled input. It checks
finite intermediate geometry/support and parses the final `ObjectWeights` under
the exact host policy (sum tolerance `1e-12`). It neither drops bad candidates nor
substitutes uniform support.

Referenced history must match the snapshot exactly, contain at most 32 unique
earlier messages from the same session/source/mapping/clock, and remain ordered by
acquisition timestamp and strictly increasing source sequence. Equal timestamps
are valid for lower source sequences. Integer nanoseconds avoid timestamp
cancellation. History must lie within one second before the current input; both
endpoints are inclusive. The producer's stronger creation-time window and current
input/state freshness checks remain producer responsibilities because public
`ControlSnapshot` carries no creation time. No timestamp is overwritten.

An accepted-status receipt here establishes metadata compatibility only. The
robot-owned activation boundary must already have established receipt trust,
static-scene validity and motion eligibility. This estimator does not execute
programs, infer accepted-command provenance, establish source measurement trust,
revalidate observation/calibration assets, or replace the gate's fresh checks.
Applied-command history is unused by this formula and remains available to the
future optimizer's smoothing objective.

## Verification and consumer handoff

Run against the installed wheel:

```sh
python -m pytest tests/shared_control/test_fusion.py
python -B docs/reference/verify_intent_fusion.py \
  --source-revision TESTED_GIT_SHA --wheel-sha256 TESTED_WHEEL_SHA256 \
  --output intent-fusion-evidence.json
```

The harness retains complete immutable inputs/configuration, expected and actual
weights/alpha/disposition for 15 analytical cases, the `1e-12` comparison
tolerance, environment, wheel/source identity, and equality of a fresh-process
replay. CI uploads this alongside JUnit, dependency versions and the built wheel.
Fixtures use explicitly synthetic compatibility-only receipts; they do not claim
host validator acceptance or robot activation.

Component tests cover no history, sustained input, complete reversal including
maximally adverse relevance, current neutral/disabled with old directional history,
equal/zero support, single/degenerate candidates, ignored neutral/disabled history,
mean of unit directions, storage reordering, subnormal nonzero input, bounded
history and corrupt records/references/configuration. S05/S06/S07 numeric/domain/
candidate checks and the intent portion of G08 have component evidence; optimizer
and gate behavior are not implied. Existing record tests cover externally supplied
negative/non-normalized ObjectWeights.

The snapshot producer accepted at Grasp-Pose-Generation
`53c64b6a5a9a7317deac6ed0c1e0ceac092ecede` (`ssc-runtime==0.2.0`, pinned
`ssc-core==0.6.0`) exports `FrozenSnapshot.resolved` with this unchanged input
shape. Its packaging pin is not changed by this producer delivery; installing
0.7.0 with that unchanged pinned consumer is not a validated integration pair.
Consumer upgrades and complete-loop version-pair evidence remain integration work.
Shared optimizer #14 consumes the outcome/configuration; archival #17 retains
the inputs, full configuration and exact ObjectWeights. This is deterministic
software evidence only, without intent accuracy, comparative task benefit,
physical-safety or real-time claims.
