# Shared validation, resolution and residual-evaluation package

`ssc-core==0.4.0` implements scene/artifact and live/control record validation,
immutable session registries, deterministic reference resolution and a restricted
`geometry-residual-v1` numerical interpreter for schema `1.0`, using an explicit
host policy. The parsing, registry and schema APIs remain source compatible with
0.3.0. The inherited grasp service is separate. Resolution checks typed-record
compatibility and the interpreter evaluates accepted source; process isolation,
receipt issuance, an active bundle, live control loop, command gate and robot
integration remain separate responsibilities.

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
.venv-acceptance/bin/python -m pip install 'dist/ssc_core-0.4.0-py3-none-any.whl[test]'
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

The parsing API is unchanged in 0.4.0. Supported message payloads are
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
The resolver additionally checks the parent frame and capture skew against
SessionConfig. No image bytes, calibration, actual capture synchronization or
capture-time transform availability are verified.

Programs permit empty feature bindings and parameters. Local uniqueness, units,
finite scalar domains, UTF-8 source size and binding counts are checked. Parameter
values may be zero or negative unless a later program-specific semantic rule
prohibits them. Normalization scale is positive in the referenced program's
implicit output unit; structural checks cannot prove dimensional compatibility.
Relevance scores do not need to sum to one. Empty program references can parse;
bundle resolution requires compatible program coverage for every candidate.

Parsing checks SHA-256 for lowercase 64-digit format only. Registry registration
also recomputes the exact UTF-8 source hash and rejects immutable-ID reuse with
changed content. Resolution checks typed reference existence and
task/scene/frame/policy compatibility. The interpreter below checks source
syntax, shapes and numerical evaluation; numerical probes, execution isolation
and receipt trust remain later validation responsibilities. Parsing or
resolving an `accepted` receipt neither issues it nor establishes host trust.

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
Weight resolution checks exact coverage of the semantic candidate set.

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

The registry supplies the cross-record compatibility and program coverage checks
described below. Snapshot program-count limits, equality of result horizon and
snapshot `dt_s`, input freshness, command ordering and control authority remain
later-layer checks. Structural acceptance or successful resolution does not
authorize motion or establish the complete offline profile's runtime requirements.

## Session registry and resolution

`SessionRegistry(session_id, *, policy)` owns one in-memory session. Callers
serialize access; there is no persistence, network lookup or cross-registry
fallback. Dependencies may arrive in any order. Registration checks each record
and identity; resolution fails until every required typed reference is present
and compatible. It does not create, activate or dispatch snapshots.

| Method | Input and result |
| --- | --- |
| `register_record(record_type, data, *, session_id)` | A supported class or type name, and a JSON-shaped dict or matching record; returns a detached validated record. |
| `register_message(data)` | A JSON-shaped envelope or Message; checks its session and atomically registers the message ID and identifiable payload; returns a detached Message. |
| `get_record(record_type, identity, *, session_id)` | Exact typed lookup; TaskContext requires a TaskRef or `{task_id, revision}` dict, while other supported records use their artifact ID. |
| `get_message(message_id, *, session_id)` | Exact envelope lookup, including live inputs whose identity is their message ID. |
| `resolve_program(program_ref, *, geometry_ref, config_id)` | Resolves a ProgramRef and exact GeometryRef to a ResolvedProgram, without requiring a receipt. Useful before validation probes. |
| `resolve_bundle(*, config_id, task_ref, geometry_ref, semantic_id, program_refs, execution_binding_refs)` | Resolves the complete compatible typed bundle and accepted-status receipts. Reference sequences accept lists or tuples. |
| `resolve_snapshot(snapshot_id)` | Resolves a registered snapshot, its bundle, current input/state messages and referenced joystick history. |
| `resolve_weights(weights_id)` | Resolves registered weights and their causal snapshot, enforcing exact candidate coverage. |

Standalone registration supports SessionConfig, TaskContext, SceneObservation,
SceneGeometry, SemanticContext, ConstraintProgram, ProgramExecutionBinding,
ProgramValidationReceipt, ControlSnapshot, ObjectWeights and ControlResult.
JoystickSample and RobotState use envelopes because their immutable references
are message IDs. Nested values and Parameter have no standalone artifact identity.
The explicit session argument on standalone registration/lookup must equal the
registry session; envelopes retain their own checked session IDs. Resolver calls
use only the registry's session.

Every input, stored value and public return is detached through the parsing
boundary, including inputs already constructed as dataclasses. Ordinary writes
are prevented by frozen records and tuple fields. Registering identical content
again is idempotent. Changed content under the same identity is rejected, using
complete canonical JSON-shaped values: object-key order is ignored, array
order, numeric types and signed zero are preserved, so `1` and `1.0` differ.
Identity comparison does not decimal-encode unbounded TimePoint seconds or
change global Python integer conversion limits.
TaskContext identities include both task ID and revision. Geometry IDs cannot be
reused for a different revision; exact GeometryRef resolution checks both fields.
A matching source hash never substitutes for the full program content check.

For each `(scene_id, feature_set_id)`, the registry also retains the observable
feature-definition signature: object membership, keypoint IDs and ownership,
and axis IDs, ownership, origin keypoint and directedness. These must remain
unchanged across registered geometry versions. Changed coordinates or frame
require both a new geometry ID and a new revision within the scene/feature set.
An existing revision pins those values independently of scene storage order;
changed definitions require a new feature-set ID. Labels do not establish
physical feature meaning, which still requires producer validation.

For each `(config_id, program_id)`, execution normalization scale and weight are
fixed. Changing either requires a new binding ID and configuration ID. A new
receipt reference with unchanged settings may use the existing configuration.

The host supplies the validation policy; wire IDs cannot select it. Bundle and
program resolution require the configuration's policy ID to agree with that
policy. Geometry and observation scenes, capture skew and configured frames are
checked. Semantic task ID/revision, scene and feature-set dependencies must agree;
its source observation must resolve within the same scene. Semantic provenance
may refer to an earlier observation of that static scene.

The selected program references exactly cover the semantic programs. Every
candidate, including one with zero relevance, has at least one program, and every
selected program belongs to a candidate. Feature IDs must exist; programs may
bind features from other objects. Each program has exactly one execution binding
for the selected configuration. Its receipt must have accepted status and match
the program ID, source hash, ABI, scene, feature set and host policy. The receipt's
exact validation geometry also resolves; it may be an earlier geometry version
with the same scene and feature definitions. These checks establish consistency,
not the origin or trustworthiness of a receipt.

Resolved values are frozen containers with the following fields:

| Value | Fields and ordering |
| --- | --- |
| `ResolvedProgram` | `program`, `geometry`, `candidate`, `keypoints`, `axis_dirs`, `params`; numeric-source tuples follow the program's binding and parameter order. |
| `ResolvedBundle` | `session_id`, `configuration`, `task`, `geometry`, `semantic`, `candidates`, `programs`, `execution_bindings`, `receipts`; candidates follow semantic relevance order, programs follow requested reference order, bindings and receipts follow programs. |
| `ResolvedSnapshot` | `snapshot`, `bundle`, `joystick`, `robot_state`, `joystick_history`; live inputs retain their Message envelopes and history retains reference order. |
| `ResolvedWeights` | `weights`, `snapshot`, `candidate_weights`; candidate weights follow the resolved semantic candidate order. |

Resolution uses only the standard library and preserves source numeric values.
It does not create NumPy arrays or run generated code. Empty numeric-source
bindings are empty tuples. The interpreter accepts these tuples directly as the
ABI shapes `(K, 3)`, `(A, 3)` and `(P,)`, where an empty feature binding is a
`(0, 3)` array, and copies them to float64 values on each evaluation.

RobotState control/TCP frames must match configuration. History entries must be
JoystickSample messages with the current input's mapping ID and clock domain.
History ordering, count, age/window bounds, source freshness and liveness remain
assembler/runtime checks. `previous_applied_command_id` remains opaque here:
accepted-command provenance and result checks belong to the adapter/solver/gate.
There is no applied-command lookup or inference of motion authority.

Resources without a supported record schema remain opaque dependencies: asset
bytes, calibration and robot-model resources, motion-constraint sets, joystick
mappings and estimator configurations. Their existence or contents are not
verified. The supplied ValidationPolicy is checked by ID but is not loaded from
a registry resource. No external-resource exception or another-session fallback
is implemented. Missing typed records fail even when another registry contains
an identically named record.

Structural failures retain ValidationError. RegistryError also exposes `path`,
`code` and `message`, distinguishing missing references, session mismatches,
immutable conflicts, source-hash mismatches, feature-definition changes,
geometry revision conflicts, binding/configuration conflicts and incompatible
references or coverage. Neither failure class repairs inputs.

From the repository root, this example loads the synthetic catalog and resolves
the bottle program's numerical inputs without inventing validation receipts:

```python
import json
from pathlib import Path

from ssc_core import GeometryRef, NUMERIC_OFFLINE_V1, SessionRegistry, TaskRef

catalog = json.loads(
    Path("tests/fixtures/shared_control/bottle_cup_offline_v1.json").read_text()
)
session = catalog["messages"][0]["meta"]["session_id"]
registry = SessionRegistry(session, policy=NUMERIC_OFFLINE_V1)
for message in catalog["messages"]:
    registry.register_message(message)
for key, record_type in (
    ("constraint_programs", "ConstraintProgram"),
    ("execution_bindings", "ProgramExecutionBinding"),
    ("validation_receipts", "ProgramValidationReceipt"),
):
    for data in catalog[key]:
        registry.register_record(record_type, data, session_id=session)

task = registry.get_record(
    "TaskContext", TaskRef("task-bottle-cup-offline-v1", 1), session_id=session,
)
semantic = registry.get_record(
    "SemanticContext", "semantic-bottle-cup-offline-v1", session_id=session,
)
geometry_ref = GeometryRef("geometry-bottle-cup-offline-v1", 1)
program = registry.resolve_program(
    semantic.program_refs[0], geometry_ref=geometry_ref,
    config_id="config-offline-v1",
)
assert program.keypoints == ((0.41, 0, 0.3),)
assert program.axis_dirs == () and program.params == (0,)
```

The catalog deliberately has no issued validation receipts. Once a separate
trusted validator has registered compatible receipts for its bindings, the
corresponding bundle call is:

```python
bundle = registry.resolve_bundle(
    config_id="config-offline-v1",
    task_ref=TaskRef(task.task_id, task.revision),
    geometry_ref=geometry_ref,
    semantic_id=semantic.semantic_id,
    program_refs=semantic.program_refs,
    execution_binding_refs=[item["binding_id"] for item in catalog["execution_bindings"]],
)
```

With the unchanged fixture this call raises RegistryError with
`code="missing_reference"` for an unissued receipt. Registering a structurally
valid accepted-status receipt alone would not establish trusted validation.

## Restricted residual interpreter

`compile_program(program, *, policy)` accepts a ConstraintProgram record or
mapping and returns an immutable `CompiledProgram`, or raises
`InterpreterError`. It re-parses the artifact under the explicit policy,
recomputes the exact UTF-8 source hash, rejects oversized source before parsing
and bounds AST nodes, depth and assignments before lowering. The accepted body is
lowered to a small immutable expression tree; Python `eval`/`exec` are never used
and generated source can reach only its five inputs, its locals and the host
primitives.

Continuing the registry example above, the resolved bottle program evaluates at
the fixture's initial TCP pose:

```python
from ssc_core import compile_program

compiled = compile_program(program.program, policy=NUMERIC_OFFLINE_V1)
identity = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
g = compiled.evaluate((0.4, 0, 0.3), identity, program.keypoints, program.axis_dirs, program.params)
assert abs(g - 0.01) <= 1e-9  # positive during approach: a valid, unsatisfied relation
```

`evaluate` takes lists or tuples of `int`/`float` with shapes `(3,)`, `(3, 3)`,
`(K, 3)`, `(A, 3)` and `(P,)`, where K, A and P are the artifact's binding and
parameter counts; `ResolvedProgram` supplies the ordered feature and parameter
tuples. Convert NumPy arrays with `tolist()`; array scalar types, Booleans and
strings are rejected. Inputs are copied to float64 values on each call and are
never written. The rotation must satisfy `R^T R = I` and `det R = 1` within the
policy's rotation tolerance, and every axis row must have unit norm within the
unit-norm tolerance, including unused bindings. Nothing is normalized or
repaired. The result is one finite Python `float`; positive, zero and negative
residuals are all successful evaluations. Relation satisfaction, penalties and
normalization remain the caller's computation.

The accepted language is exactly the Generated Constraint Program Contract
section 7 grammar: one undecorated `def evaluate(p_tcp, R_control_tcp,
keypoints, axis_dirs, params)` with no defaults or annotations, then single-target
assignments to local names and one final `return`. Locals may be reassigned;
inputs, primitive names and `evaluate` cannot be bound. Expressions are finite
`int`/`float` literals, names, `+ - * /`, unary `+ -`, direct positional calls to
`norm`, `dot`, `cross`, `sqrt`, `sin`, `cos`, `acos`, `abs`, `minimum`,
`maximum` and `clip`, and indexing with literal nonnegative integers or full
`:` slices on any array-valued expression. Array arithmetic requires equal
shapes or a scalar operand; there is no other broadcasting. Every shape, index
bound and primitive signature is checked statically, and the return value must
be a scalar.

`numeric-offline-v1` sets 8192 UTF-8 source bytes, 512 AST nodes, AST depth 32,
64 assignments, 256 elements per array and rotation/unit-norm tolerance 1e-8.
AST nodes and depth are counted over `ast.iter_child_nodes` from the module root
under Python 3.12, including expression-context and operator nodes, with the
module at depth 1. These are language limits; they do not bound memory or time.

`InterpreterError` carries `stage`, `path`, `code` and `message` and is
pickle-safe. Compile paths name an artifact field or a `$.source_code:LINE:COL`
location; input paths name the element, such as `$.axis_dirs[1]`.

| Stage | Codes |
| --- | --- |
| `compile` | `invalid_artifact`, `source_limit`, `source_hash_mismatch`, `syntax_error`, `ast_limit`, `function_structure`, `signature`, `forbidden_syntax`, `forbidden_call`, `undefined_name`, `input_rebinding`, `input_mutation`, `reserved_name`, `invalid_literal`, `arity`, `shape_mismatch`, `invalid_index`, `index_out_of_bounds`, `array_limit`, `non_scalar_return` |
| `input` | `input_type`, `input_shape`, `non_finite_input`, `invalid_rotation`, `non_unit_axis` |
| `evaluation` | `division_by_zero`, `domain_error`, `non_finite` |

Division by zero (including by `-0.0`), `sqrt` of a negative value, `acos`
outside [-1, 1], `clip` with `lo > hi` and any nonfinite intermediate fail,
even in an unused local. A failure never becomes a zero residual; callers
invalidate the evaluation. Compilation does not issue a validation receipt,
run numerical probes or activate a program. Acceptance is a grammar check, not
a sandbox: the evaluator must still run in the supervised worker with the
profile's memory and wall-time budgets.

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

Registry checks additionally cover defensive copying, immutable-content conflicts,
exact task/geometry references, cross-session rejection, source integrity,
feature definitions, ordered bindings and compatible bundle/snapshot/weight
resolution. They do not exercise a generated-code validator or a live controller.

Interpreter checks cover the Wiki distance and undirected-axis examples, the
catalog's recorded `raw_g` values, the profile's directed-axis and division
variants, every primitive and indexing form, table-driven rejected syntax,
signatures, limits and artifacts, invalid inputs, numerical failures, sign
invariance, determinism, input immutability and the absence of `eval`/`exec`.
These are language and numerical checks only. They do not establish process
isolation, memory or time bounds, receipt trust or physical safety.

PR evidence records the tested commit, wheel hash, exact environment and package
versions, commands, test result and independent final-diff review. Component
checks do not establish two-repository compatibility, worker isolation, complete
loop behavior, control performance or physical safety. Subsequent cards consume
this parsing API and fixtures without importing the inherited pipeline.
