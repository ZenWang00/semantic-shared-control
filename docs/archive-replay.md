# Immutable run archives and offline replay

P1-15 / [issue #17](https://github.com/ZenWang00/semantic-shared-control/issues/17)
adds archive format `ssc-run-v1` in `ssc-core==0.9.0`.
It preserves schema 1.0 and geometry-residual-v1. The server owns this tool;
it never imports the robot repository, runs an archived factory, starts an
optimizer, restores receipt authority, or submits a command.

## Read, inspect and replay

Install the shared wheel and its pinned NumPy/SciPy dependencies as described in
[Shared package](shared-package.md). Inspection and integrity validation work on
Python 3.12 on supported development platforms. Numerical replay requires Linux:
the existing evaluator enforces numeric-offline-v1 process budgets and refuses
unbounded fallback on macOS or Windows.

```sh
python -I -m ssc_core.replay tests/fixtures/shared_control/control_run_v1.json --inspect
python -I -m ssc_core.replay tests/fixtures/shared_control/control_run_v1.json > replay.json
python -I -m ssc_core.replay run.json --sha256 EXTERNALLY_RETAINED_FILE_SHA256
```

Exit 0 means structural inspection or numerical comparison succeeded; exit 1
means numerical execution/comparison failed; exit 2 means unreadable, corrupt,
unsupported or incomplete evidence. Errors are JSON with a stable code and
diagnostic field/reference, for example `missing_reference`,
`source_hash_mismatch`, `checksum_mismatch`, `unsupported_version` or
`incomplete_record`. A run without evaluation events explicitly reports numerical
evidence unavailable; successful inspection does not claim residual reproduction.

The report contains exact evaluator inputs, recorded and replayed residuals and
penalties, atol/rtol (both 1e-9), preparation/evaluation/retirement outcomes, the
recorded/current environments and their differences. It also retains proposals,
acceptance decisions, controller requests and supplied observations separately.
A solved-but-rejected result is still replayed. No observation is invented for a
hold or accepted target. Original acquisition, production, reception, dispatch,
arrival and acceptance timestamps remain unchanged.

## Format and completeness

The file is UTF-8-compatible JSON with exactly `archive_version`,
`sha256` and `payload`. Payload has these five fields:

| Field | Required content |
| --- | --- |
| manifest | Run/session IDs; schema/ABI; profile, evaluator policy, solver, installation and scenario IDs; exact 40-character server/ROS2 revisions, package versions and wheel SHA-256 values; Python/OS/architecture/package environment; explicit evidence scope; comparison tolerances |
| records | Standalone registered artifacts as `{record_type, data}`, including configurations, snapshots, weights and local results |
| messages | Original schema-1.0 envelopes, including all current/history joystick and measured-state samples; envelope IDs, sequences and timestamps remain exact |
| resources | `{resource_id, media_type, sha256, base64}` with complete bytes for profile, installation, scenario, model, host constraints, calibration, input mapping, estimator/solver/policy definitions, observation assets, and additional evidence |
| events | Ordered `{event_id, kind, data}` entries; storage order is preserved and never replaced by a timestamp sort |

JSON is strict: duplicate
keys, nonfinite numbers and unknown fields in defined archive/event structures
are errors. The file size limit is 64 MiB, including inline resource bytes.

The payload digest uses SHA-256 over sorted-key JSON, ASCII escapes, no
whitespace, no nonfinite numbers. Resource digests cover exact decoded bytes.
A program's existing source SHA-256 covers exact UTF-8 source bytes, including
whitespace, line endings and Unicode. Registry validation also checks complete
artifact identity, bindings, receipt provenance and geometry revisions.
The writer rejects changed content under an existing immutable ID; record
registration order does not determine resolution. Event order remains meaningful.

Checksums establish integrity relative to a trusted checksum, **not authorship**.
An attacker who can replace both the archive and its checksum can rewrite history.
Keep the file SHA-256 returned by `write` in an independent delivery record.
Readers do not turn historical accepted receipts into a host authority. All
source is prepared anew in the restricted evaluator during replay.

Every snapshot must resolve its full typed dependency graph and original
dispatch event. Every referenced resource is present as bytes; no network fetch,
path lookup or current-configuration substitution occurs. Known evaluator,
solver and intent configuration resources must exactly match their installed
versioned definitions. Other resources are retained as opaque data; a checksum
does not validate a calibration's physical meaning or a robot model.

Every proposed move needs a recorded evaluation pose and complete diagnostics
for its snapshot's programs. The pose must match its proposed target and each
normalization scale its execution binding. A hold with residual diagnostics
must explicitly record the pose at which those diagnostics were computed;
a timeout/local hold without diagnostics has no evaluation event. An accepted
command referenced by a later snapshot must have an explicit earlier acceptance
record in the same local clock domain. Results that were rejected remain
archivable: the reader does not reinterpret gate decisions as current authority.

## Event data

| kind | data |
| --- | --- |
| dispatch | `snapshot_id, command_seq, created_at, dispatched_at, expires_at, solve_deadline, inputs`; each input is `{message_id, received_at, source_age_ns}` covering current joystick/state and history exactly |
| evaluation | `result_id, pose`; pose uses existing Pose schema; the reader resolves ordered keypoints, axes and parameters from the snapshot |
| result_arrival | `result_id, received_at` |
| acceptance | Existing CommandAcceptanceRecord, stored separately from the result |
| controller_request | `at, result_id, detail`; result_id may be null for independent local actions |
| controller_observation | `at, result_id, detail`; supplied acknowledgment/feedback only, never inferred from a request |
| lifecycle | `at, result_id, detail`; explicit runtime-specific lifecycle evidence |

Times use existing TimePoint values. Dispatch input ages are integer nanoseconds
at snapshot creation, checked against original sampling times. Reception must
follow sampling and precede creation in the same domain. Dispatch times must
satisfy creation <= dispatch < solve deadline <= expiry. This checks evidence
coherence, not live freshness or command eligibility. Runtime-specific `detail`
objects preserve JSON data without loading or executing code. Missing optional
arrivals/acceptances/observations mean unavailable, not accepted or stopped.

## Writer and consumer handoff

```python
from ssc_core.archive import ArchiveBuilder, RunArchive

builder = ArchiveBuilder(manifest)  # all manifest fields above required
builder.add_message(original_message)
builder.add_record("ControlSnapshot", original_snapshot)
builder.add_resource(resource_id, exact_bytes, media_type="application/json")
builder.add_event(event_id, "dispatch", original_dispatch_data)
# Add the remaining graph, resources and events before sealing.
archive = builder.seal()
digest = archive.write("new-run.json")  # atomic create; refuses any existing path
loaded = RunArchive.read("new-run.json", expected_sha256=digest)
resolved = loaded.registry().resolve_snapshot(snapshot_id)
```

The builder is serialized by its caller and owns detached copies. Sealed archives
store immutable bytes; returned dictionaries and registries are fresh copies.
Publication fsyncs a temporary file and atomically links it at a new destination;
it never replaces an existing file or symlink. This is a completed-run archive,
not a streaming crash-recovery log. One archive represents one session. Session
or clock reset starts another archive; an external experiment index may group them.

[ROS2 issue #6](https://github.com/ZenWang00/Grasp-Pose-Generation/issues/6) should
serialize its FrozenSnapshot/DispatchRecord/TimedInput evidence into the table
above, along with results, gate decisions, lifecycle records and actual plant
observations. Preserve all earlier validation geometry and accepted-command
dependencies. For every resource ID supply complete immutable bytes, including
intent/solver configuration dataclasses, validator/evaluator limits and exact
installation manifest. Keep state/controller observations distinct from requests.
The runtime can call this shared API; the archive implementation has no reverse
dependency. Full runner and accepted version-pair compatibility remain #6 work.

## Fixture and verification

`docs/reference/archive_fixture.py` builds a three-snapshot fixture containing
an accepted analytic move, a solved but rejected move, a timeout hold and a local
hold. It executes real host validation/probes and intent fusion on Linux.
Proposals/acceptance decisions and the single observation are explicitly scripted;
they are not full-loop, optimizer, gate or plant evidence. One-pixel RGB/depth
bytes are synthetic provenance placeholders under dedicated fixture IDs.
The referenced ROS2 revision/wheel is the accepted consumer baseline only,
not a claim it ran while generating this fixture.

```sh
python docs/reference/archive_fixture.py \
  --source-revision FULL_TESTED_SERVER_COMMIT \
  --wheel-sha256 INSTALLED_SERVER_WHEEL_SHA256 \
  --output new-archive-fixture.json
python -I -m ssc_core.replay new-archive-fixture.json > archive-replay-evidence.json
```

CI generates a new fixture from the exact checked-out revision and installed wheel,
runs the full shared-core suite and fresh-process replay, and retains the archive,
checksum, report, wheel and dependency manifest. The committed fixture enables
inspection and structural tests on non-Linux platforms. Tests cover full
round-trip, record-storage permutation, detached reads, non-overwriting publication,
identity reuse, missing dependencies/resources, corrupt bytes/source/binding/receipt,
unknown versions, timing and diagnostic incompleteness, numerical mismatch and
an import guard against ROS2/runtime access. Numerical tests run only on Linux;
a non-Linux test verifies explicit refusal rather than an unbounded fallback.
