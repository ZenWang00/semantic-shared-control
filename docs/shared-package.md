# Shared structural-validation package

`ssc-core==0.2.0` implements scene/artifact and live/control record validation
for schema `1.0`, using explicit policy `numeric-offline-v1`. The inherited grasp
service is separate. Parsing live/control records does not implement an active
bundle, live control loop, program execution, a command gate, or a robot.

## Install and test

The reference environment is Ubuntu 24.04, Python 3.12, NumPy 1.26.4,
SciPy 1.11.4 and pytest 7.4.4. The distribution supports Python 3.12 for this
reference dependency set. Do not install the inherited `requirements.txt`.

From the repository root:

```sh
python3.12 -m venv .venv-ssc
.venv-ssc/bin/python -m pip install '.[test]'
.venv-ssc/bin/python -m pytest tests/shared_control
```

To test the actual wheel in a second clean environment:

```sh
.venv-ssc/bin/python -m pip wheel --no-deps --wheel-dir dist .
python3.12 -m venv .venv-acceptance
.venv-acceptance/bin/python -m pip install 'dist/ssc_core-0.2.0-py3-none-any.whl[test]'
.venv-acceptance/bin/python -m pytest tests/shared_control
.venv-acceptance/bin/python -m pip check
```

Tests use the installed package with pytest's importlib mode; no `src` path is
injected. Packaging includes only `ssc_core`. Contract code and the package
entrypoint use the standard library and do not import NumPy/SciPy, legacy
services, ROS2, Torch, CUDA, Open3D or online providers. NumPy/SciPy are declared
reference runtime dependencies for the shared library's later numerical modules.

## Parsing API

```python
from ssc_core import NUMERIC_OFFLINE_V1, parse_record, to_dict

pose = parse_record(
    "Pose",
    {"position_xyz": [0.4, 0, 0.3], "quaternion_xyzw": [0, 0, 0, 1]},
    policy=NUMERIC_OFFLINE_V1,
)
assert pose.position_xyz == (0.4, 0, 0.3)
wire_value = to_dict(pose)
```

- `parse_record(type_name_or_class, data, *, policy)` validates a dict into a
  frozen typed record with immutable nested tuples.
- `parse_message(data, *, policy)` validates the envelope and supported payload.
- `loads_message(text, *, policy)` additionally rejects duplicate JSON keys and
  nonstandard/nonfinite numeric tokens. It never runs program source.
- `to_dict(record)` returns detached JSON-compatible containers for serialization;
  original numbers, sequence order, IDs and source text are preserved.
- `ValidationError` exposes `path`, `code`, and `message`, identifying the invalid
  field. Validation fails at the first diagnosed violation.

The parsing API is unchanged in 0.2.0. Supported message payloads are
SessionConfig, TaskContext, SceneObservation, SceneGeometry, SemanticContext,
ConstraintProgram, ProgramExecutionBinding, ProgramValidationReceipt, Parameter,
JoystickSample, RobotState, ControlSnapshot, ObjectWeights and ControlResult.
ConstraintEvaluation is a nested result record; CommandAcceptanceRecord is
separate local bookkeeping parsed through `parse_record`, not a message payload.
The new records also use the exported TaskRef and ObjectWeight value types and
Vector2 tuple alias. All common/nested record types are available through
`parse_record`.

Parsing is the validation boundary. Dataclass constructors are typed value
containers, not a replacement for parsing untrusted or internally calculated
data. Re-enter through the parsers after calculations or external input. Missing
data is never supplied implicitly. RobotState's optional `joint_velocities_radps`
retains its absence when serialized; an explicit JSON null is rejected, and
missing velocity is never replaced with zeros. Required nullable fields retain
their explicit null values instead of disappearing during serialization.

A local hold can preserve unavailable causal inputs without creating a target:

```python
from ssc_core import NUMERIC_OFFLINE_V1, parse_record, to_dict

hold = parse_record(
    "ControlResult",
    {
        "result_id": "hold-1", "snapshot_id": None, "weights_id": None,
        "control_epoch": 1, "command_seq": 1,
        "source_joystick_seq": None, "source_robot_state_seq": None,
        "status": "hold", "evaluation_valid": False, "solver_status": "not_run",
        "constraints_satisfied": None, "solve_time_s": 0,
        "constraint_evaluations": [], "reason": "joystick_released",
    },
    policy=NUMERIC_OFFLINE_V1,
)
assert to_dict(hold)["snapshot_id"] is None
assert "next_tcp_pose" not in to_dict(hold)
```

This creates a validated value only; it does not send or apply a hold command.

## Validation boundaries

Unknown fields, wrong primitive types, bool-as-number, unsupported versions,
invalid dimensions/domains and nonfinite numeric values are rejected. JSON
integer and fractional numbers remain their original numeric values; numeric
strings and floating values in integer fields are not coerced. Counter values
are in the exact interoperable range 0 through 9007199254740991; revisions start
at 1. TimePoint seconds are signed integers with no undocumented magnitude cap;
nanoseconds are in [0, 1000000000). Clock IDs remain explicit.

The explicit host policy controls norm tolerances and local profile limits.
Record policy IDs remain opaque references: they never select a policy or prove
compatibility. The original catalog's `numeric-policy-demo-v1` is not silently
registered as `numeric-offline-v1`.

Geometry requires objects and keypoints, permits empty axes, and checks unique
IDs within each namespace and local ownership. An axis origin must belong to
its object. No per-object feature minimum or program-wide single-object binding
rule is invented. Norm checks do not normalize values. Labels and descriptions
are explanatory strings, not identities or proof of physical feature meaning.

Observation checks cover image encoding, dimensions and aligned size, finite
3x3 intrinsics with positive focal lengths, matching capture clock domains,
transform sampling at the RGB timestamp, and the transform's camera child frame.
Comparing the parent frame or capture skew against SessionConfig requires
cross-record resolution and is deferred. No image bytes, calibration, actual
capture synchronization or capture-time transform availability are verified.

Programs permit empty feature bindings and parameters. Local uniqueness, units,
finite scalar domains, UTF-8 source size and binding counts are checked. Parameter
values may be zero or negative unless a later program-specific semantic rule
prohibits them. Normalization scale is positive in the referenced program's
implicit output unit; structural checks cannot prove dimensional compatibility.
Relevance scores do not need to sum to one. Empty program references can parse;
the later resolver must establish compatible program coverage before activation.

SHA-256 is checked for lowercase 64-digit format only. A well-formed but wrong
hash is intentionally not detected here. Source syntax, source/hash agreement,
immutable-ID reuse, referenced record existence, task/scene/frame/policy
compatibility, receipt trust, and numerical probes remain #6–#9 responsibilities.
Parsing an `accepted` receipt neither issues it nor establishes host trust.

JoystickSample requires a finite two-component vector within the unit disk and
a Boolean enable flag. RobotState checks its pose, unique joint names, matching
position/optional velocity lengths and the declared measured-state source enum.
These checks do not prove measurement provenance, synchronization or model
agreement. ControlSnapshot validates reference shapes, positive `dt_s`, separate
nonnegative `snapshot_seq` and `control_epoch`, and a required nullable previous
applied-command ID. It does not invent a dispatch `command_seq` or resolve IDs.

ObjectWeights requires a nonempty set of unique object IDs, finite nonnegative
weights and assistance strength in [0, 1]. The selected policy bounds the
candidate count and weight-sum error; `numeric-offline-v1` allows at most eight
candidates and absolute sum-to-one error of 1e-12. Weights are not renormalized.
Exact coverage of the semantic candidate set requires the later resolver.

ControlResult uses mutually exclusive move/hold shapes. A move requires nonnull
snapshot/weight references and source sequences, valid evaluation, a solved
status, a Boolean `constraints_satisfied`, a valid target pose and positive
horizon. False satisfaction is allowed because generated relations are soft.
A hold requires a nonblank reason and forbids target/horizon keys, even when
their values are null; causal references and unavailable source sequences may
be null. A `not_run` solver status requires zero solve time. Other solve times
are finite and nonnegative. Parsing does not infer evaluation-array completeness
or satisfaction from an empty array.

ConstraintEvaluation checks finite raw residuals, positive normalization scales
and finite nonnegative penalties. It does not recompute the penalty formula or
verify declared satisfaction; evaluator/replay checks retain that responsibility.
CommandAcceptanceRecord validates its decision flag, reason, timestamp and
required nullable applied-command ID independently of the proposed result.
It does not certify that a command was accepted or physically executed.

Cross-record compatibility, snapshot program coverage/count limits, equality of
result horizon and snapshot `dt_s`, input freshness, command ordering and control
authority remain later-layer checks. Structural acceptance does not authorize
motion or establish the complete offline profile's runtime requirements.

## Pure host-constraint interface

`ssc_core.host_constraints.HostConstraintChecker` is a callable protocol taking
keyword arguments `candidate_pose`, `measured_state`, `u_xy`, `dt_s` and
`configuration`. The input configuration is a parsed immutable SessionConfig;
it identifies the exact model and host constraint set. Consumer implementations
own any additional immutable model constants. They must not read devices,
clocks, network state or mutable globals.

`MeasuredStateView` holds a parsed pose, frame names and tuple-valued joint data.
It is a projection of already validated measured data, separate from RobotState
parsing and not evidence of measurement provenance. `ConstraintMargin` contains a
name, finite dimensionless value and explanation for a violation.
`HostConstraintResult(margins, policy=...)` rejects missing/duplicate/nonfinite
margins and derives both `feasible` and `violation_reasons` using the profile's
`margin >= -1e-8` rule; a separate feasibility flag cannot be supplied.

The robot-owned checker supplies model/workspace/path margins. The future
solver/gate also computes the profile's basic step constraints; authority,
deadlines, ordering and freshness remain outside this callback. Any exception
or invalid report must reject the candidate. Tests use a small local plane
checker; no synthetic controller, gate or real robot implementation is included.

## Evidence and handoff

Tests cover the structural portions of S01–S08, selected profile-limit boundaries,
record-local consistency, immutable output, round trips and callback reports.
The original bottle/cup catalog remains unchanged; its complete scene/artifact
and live/control chain is parsed, including separate acceptance bookkeeping.
A separate synthetic offline fixture uses fresh identities and is not a
runtime-approved active bundle. See the fixture README for origin.

PR evidence records the tested commit, wheel hash, exact environment and package
versions, commands, test result and independent final-diff review. Component
checks do not establish two-repository compatibility, worker isolation, complete
loop behavior, control performance or physical safety. Subsequent cards consume
this parsing API and fixtures without importing the inherited pipeline.
