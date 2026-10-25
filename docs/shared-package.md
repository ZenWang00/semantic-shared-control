# Shared structural-validation package

`ssc-core==0.1.0` implements the scene/artifact portion of schema `1.0`, using
explicit policy `numeric-offline-v1`. It is the first shared-control executable
slice; the inherited grasp service is separate. The package does not implement
an active bundle, live control, program execution, a command gate, or a robot.

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
.venv-acceptance/bin/python -m pip install 'dist/ssc_core-0.1.0-py3-none-any.whl[test]'
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

Supported payloads are SessionConfig, TaskContext, SceneObservation,
SceneGeometry, SemanticContext, ConstraintProgram, ProgramExecutionBinding and
ProgramValidationReceipt and Parameter. All common/nested value types are
available through `parse_record`. Live/control payloads are deferred to #5.

Parsing is the validation boundary. Dataclass constructors are typed value
containers, not a replacement for parsing untrusted or internally calculated
data. Re-enter through the parsers after calculations or external input. Missing
data is never supplied implicitly. This slice's types have no optional fields;
later live optional fields must retain absence, rather than acquire zero values.

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

## Pure host-constraint interface

`ssc_core.host_constraints.HostConstraintChecker` is a callable protocol taking
keyword arguments `candidate_pose`, `measured_state`, `u_xy`, `dt_s` and
`configuration`. The input configuration is a parsed immutable SessionConfig;
it identifies the exact model and host constraint set. Consumer implementations
own any additional immutable model constants. They must not read devices,
clocks, network state or mutable globals.

`MeasuredStateView` holds a parsed pose, frame names and tuple-valued joint data.
It is a projection of already validated measured data, not the future RobotState
validator or evidence of measurement provenance. `ConstraintMargin` contains a
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

Tests cover the scene/artifact portion of S01–S05, profile-limit boundaries,
record-local provenance consistency, immutable output, round trips and callback
reports. The original bottle/cup catalog remains unchanged; its live records
are retained for #5. A separate synthetic offline fixture uses fresh identities
and is not a runtime-approved active bundle. See the fixture README for origin.

PR evidence records the tested commit, wheel hash, exact environment and package
versions, commands, test result and independent final-diff review. Component
checks do not establish two-repository compatibility, worker isolation, complete
loop behavior, control performance or physical safety. Subsequent cards consume
this parsing API and fixtures without importing the inherited pipeline.
