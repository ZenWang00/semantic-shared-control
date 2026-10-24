# Generated Constraint Program Contract

**Status: Design Draft v0.1 — proposed interfaces; no generated-code runtime is implemented in this documentation change.**

This page specifies how a VLM proposes numerical relations between a candidate tool pose and observed scene features. The output is a versioned program artifact, not a motion command. A host validator must accept the artifact and bind it to a scene before an optimizer can evaluate it.

Read [Data Model and Message Contracts](https://github.com/ZenWang00/semantic-shared-control/wiki/Data-Model-and-Message-Contracts) for common metadata, frame conventions, and the enclosing semantic context. Read [Runtime Lifecycle and Timing](https://github.com/ZenWang00/semantic-shared-control/wiki/Runtime-Lifecycle-and-Timing) for activation and rejection of late results.

## 1. Numerical meaning and scope

A program evaluates one scalar relation:

$$
g(p, R, K, A, \theta) \in \mathbb{R}, \qquad g \leq 0 \text{ means that the relation is satisfied.}
$$

The nonpositive satisfaction convention follows the [official ReKep prompt](https://github.com/huangwl18/ReKep/blob/main/vlm_query/prompt_template.txt#L41). This project defines its own interface: it explicitly supplies the candidate TCP orientation, uses stable feature IDs, and treats generated relations as soft assistance terms in continuous shared control.

The VLM may compose new formulas from the restricted numerical language. It is not limited to selecting a predefined list of relations. Each artifact returns one scalar; multiple relations require multiple artifacts.

The first version covers a static scene before an object is grasped. Scene keypoints and axes remain fixed in the control frame while the optimizer changes the candidate TCP pose. The interface does not model grasp attachment, object transport, object deformation, or a sequence of manipulation stages.

For the LIO adapter, the TCP X axis is the approach direction, Y is the closing direction, and Z is the lateral direction. These are columns 0, 1, and 2 of `R_control_tcp`. A relation must not assume that the TCP Z axis is the approach direction.

## 2. Function interface

`function_api_version = "geometry-residual-v1"` fixes the following positional interface:

```python
def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):
    # Restricted straight-line numerical expressions.
    return residual
```

| Argument or return | Type and shape | Meaning |
|---|---|---|
| `p_tcp` | Floating-point array `(3,)` | Candidate TCP origin, in meters in `control_frame`. |
| `R_control_tcp` | Floating-point array `(3, 3)` | Rotation mapping a vector in `tcp_frame` into `control_frame`; columns are the TCP axes in the control frame. |
| `keypoints` | Floating-point array `(K, 3)` | Fixed observed positions in meters, ordered by `keypoint_bindings`. |
| `axis_dirs` | Floating-point array `(A, 3)` | Fixed observed unit directions, ordered by `axis_bindings`; all expressed in `control_frame`. |
| `params` | Floating-point array `(P,)` | Parameter values in the order of `parameters`. Units are declared by the artifact. |
| Return value | One finite floating-point scalar | Signed residual `g`, in `output_unit`. A vector, Boolean, `NaN`, or infinity is invalid. |

All arrays are read-only. Empty feature or parameter bindings are permitted and produce arrays with the corresponding zero first dimension. A program may leave arguments unused. It must not access state, time, randomness, external models, or previous evaluations.

The host obtains `R_control_tcp` from the candidate quaternion using the common pose convention. It checks that the result is a valid rotation before evaluation. The function does not normalize, reinterpret, or repair frames. The scene and candidate pose must already share `control_frame`.

Bindings use stable feature IDs; array indices are local positions within those bindings. For example, `keypoints[0]` means the first ID in this artifact's `keypoint_bindings`, not the first keypoint in the scene's storage order. Axis origins, if needed in a formula, are supplied explicitly through their origin keypoint IDs in `keypoint_bindings`.

The `directed` property belongs to the scene axis definition. A formula for an undirected axis must be invariant under sign reversal of that axis. A formula for a directed axis may distinguish the two directions. This is checked with validation probes; it is not inferred from array position.

## 3. Program artifact

`ConstraintProgram` is an immutable content record referenced from `SemanticContext`. When transmitted as a standalone message, it uses the common metadata defined in the [data model](https://github.com/ZenWang00/semantic-shared-control/wiki/Data-Model-and-Message-Contracts); metadata is not part of the function arguments.

| Field | Type | Required | Contract |
|---|---|---|---|
| `program_id` | String | Yes | Unique immutable artifact ID. Any source, binding, parameter, or declaration change requires a new ID. |
| `source_sha256` | String | Yes | Lowercase SHA-256 hex digest of the exact UTF-8 bytes of `source_code`, including its final newline if present. It does not hash the other fields. |
| `function_api_version` | String | Yes | Exactly `geometry-residual-v1` for this version. |
| `object_id` | String | Yes | Candidate object whose assistance cost receives this relation. It must exist in the bound scene. |
| `description` | String | Yes | Human-readable intended geometric relationship, including the role of each tolerance. |
| `entrypoint` | String | Yes | Exactly `evaluate`. |
| `source_code` | String | Yes | The proposed restricted Python function. |
| `keypoint_bindings` | Array of strings | Yes | Ordered, unique keypoint IDs. Empty if no keypoints are needed. |
| `axis_bindings` | Array of strings | Yes | Ordered, unique axis IDs. Empty if no axes are needed. |
| `parameters` | Array of `Parameter` | Yes | Ordered numeric parameters. Empty if none are needed. |
| `output_unit` | Enum | Yes | `m`, `rad`, or `dimensionless`. |

`Parameter` has three required fields: unique `name: string`, finite `value: number`, and `unit: "m" | "rad" | "dimensionless"`. Angles use radians. A name explains the purpose of an entry; execution uses the array order, not dictionary lookup.

The VLM supplies a proposed program. The host owns the immutable artifact registry and rejects a changed body under an existing `program_id`. The host recomputes `source_sha256`; a matching source hash alone is insufficient to establish that bindings and parameter values are unchanged. The complete immutable artifact must also match the referenced ID.

## 4. Host-owned execution binding and validation receipt

The generator does not choose its own validation result, penalty normalization, or execution budget. Two host-owned records complete the contract.

### ProgramExecutionBinding

| Field | Type | Contract |
|---|---|---|
| `binding_id` | String | Immutable identifier for this execution configuration. |
| `program_id` | String | Exact immutable artifact to execute. |
| `config_id` | String | Session configuration version that owns these settings. |
| `normalization_scale` | Positive finite number | Residual scale `s`, expressed in the program's `output_unit`. |
| `weight` | Nonnegative finite number | Dimensionless coefficient `lambda` for this relation. |
| `validation_receipt_id` | String | Host acceptance record for this artifact and feature set. |

An execution binding is resolved and frozen with the control snapshot. A change to a scale or coefficient creates a new binding and configuration version; an in-flight solve keeps its original values.

### ProgramValidationReceipt

| Field | Type | Contract |
|---|---|---|
| `receipt_id` | String | Immutable host validation record ID. |
| `program_id` | String | Immutable artifact checked by the validator. |
| `source_sha256` | String | Recomputed source hash. |
| `function_api_version` | String | Checked function ABI. |
| `scene_id` | String | Scene identity in which the bindings were resolved. |
| `feature_set_id` | String | Feature identities and definitions used for validation. |
| `validation_geometry_id` | String | Geometry record used for numerical validation probes; provenance rather than a requirement to regenerate source on every coordinate update. |
| `validation_geometry_revision` | Integer >= 1 | Exact coordinate revision used for those probes. |
| `validator_policy_version` | String | Exact language rules and validation policy used. |
| `status` | Enum | `accepted` or `rejected`. |
| `diagnostics` | Array of strings | Empty or explanatory messages; a rejection requires at least one explanation. |

A receipt applies only to the stated artifact, hash, ABI, scene, feature set, and policy. It does not replace the semantic context's task version checks. A new scene, changed feature identity or definition, altered program, or changed validation policy requires revalidation. The snapshot pins the specific geometry revision separately; coordinate updates under the same feature definitions do not by themselves require new generated source. Runtime checks of shapes, finite values, rotations, and axes still apply to every evaluation; validation is not a proof over every possible optimizer candidate. The static-scene v1 lifecycle still requires reinitialization when an object is physically moved.

## 5. Soft assistance cost

The host converts a valid residual to a nonnegative normalized penalty:

$$
c_j = \left[\max\left(0, \frac{g_j}{s_j}\right)\right]^2, \qquad s_j > 0.
$$

An illustrative grouping of the generated terms is:

$$
J_{\mathrm{semantic}} = \alpha \sum_i w_i \sum_{j \in \mathcal{P}_i} \lambda_j c_j,
$$

where `w_i` is a candidate object's weight, `alpha` is the separate assistance strength, and `lambda_j` comes from the execution binding. This equation defines how these terms are scaled; it is not the complete objective or a specification of the optimizer algorithm.

A positive `g` during approach is expected: the candidate has not yet reached the target relation. A valid evaluation may therefore yield a positive cost and still contribute to an accepted movement step. The following outcomes must remain distinct:

| Outcome | Meaning |
|---|---|
| Program validation | The artifact passed the host's declared structural and numerical checks. |
| Function evaluation | This particular set of arguments produced a finite scalar of the required type. |
| Relation satisfaction | The resulting scalar is at or below zero. |
| Solver result | Optimization returned a usable candidate within its budget and independent constraints. |
| Command acceptance | The robot-side gate accepted the result's session, epoch, ordering, age, and current control conditions. |

Generated terms are soft assistance only. They cannot weaken joint limits, workspace limits, collision constraints, motion limits, or local stop behavior. Those checks belong to independently implemented robot constraints and control gates. Neither a negative residual nor a successful solver result certifies physical safety.

## 6. Worked examples

These examples illustrate proposed artifacts and numerical behavior. They are not robot calibration values, validated grasp targets, or tested motion parameters. The JSON blocks contain complete program artifacts; enclosing common metadata is omitted because these are referenced records rather than standalone messages.

### 6.1 Approach a bottle TCP target

The keypoint `kp-bottle-approach` is defined by the scene as a desired TCP location. It is not merely a point on the bottle surface. The offset needed to account for the gripper geometry must be included when that scene feature is defined.

```python
def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):
    distance = norm(p_tcp - keypoints[0])
    return distance - params[0]
```

```json
{
  "program_id": "program-bottle-distance-v1",
  "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2",
  "function_api_version": "geometry-residual-v1",
  "object_id": "object-bottle",
  "description": "Approach the grounded bottle TCP target within the declared distance tolerance.",
  "entrypoint": "evaluate",
  "source_code": "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n    distance = norm(p_tcp - keypoints[0])\n    return distance - params[0]\n",
  "keypoint_bindings": ["kp-bottle-approach"],
  "axis_bindings": [],
  "parameters": [
    {"name": "distance_tolerance", "value": 0.02, "unit": "m"}
  ],
  "output_unit": "m"
}
```

If the TCP is 0.10 m from the target, `g = 0.08 m`. With an illustrative `normalization_scale = 0.10 m`, the unweighted penalty is `0.64`. At a distance of 0.01 m, `g = -0.01 m` and the penalty is zero. Both evaluations are valid.

### 6.2 Align a TCP axis with an undirected scene axis

Here `axis-bottle-body` is an undirected unit axis. The example asks that the TCP approach axis be parallel or antiparallel to it within an angular tolerance. This is a formula demonstration, not a claim that this is the appropriate grasp orientation for every bottle.

```python
def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):
    approach = R_control_tcp[:, 0]
    alignment = dot(approach, axis_dirs[0])
    threshold = cos(params[0])
    return threshold * threshold - alignment * alignment
```

```json
{
  "program_id": "program-bottle-axis-v1",
  "source_sha256": "cabc49150a7f0776d6225017970b2d43d28a94010d303ceda03cdd81acee1026",
  "function_api_version": "geometry-residual-v1",
  "object_id": "object-bottle",
  "description": "Align the TCP X axis to the undirected bottle body axis within 0.1745329252 rad, allowing either sign.",
  "entrypoint": "evaluate",
  "source_code": "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n    approach = R_control_tcp[:, 0]\n    alignment = dot(approach, axis_dirs[0])\n    threshold = cos(params[0])\n    return threshold * threshold - alignment * alignment\n",
  "keypoint_bindings": [],
  "axis_bindings": ["axis-bottle-body"],
  "parameters": [
    {"name": "angle_tolerance", "value": 0.1745329252, "unit": "rad"}
  ],
  "output_unit": "dimensionless"
}
```

The tolerance must lie in `[0, pi / 2]` for this formula's intended meaning. The squared dot product makes the residual invariant to axis sign. A perfect parallel or antiparallel alignment gives approximately `-0.03015`; a perpendicular alignment gives approximately `0.96985`. Normalization must therefore use a dimensionless scale, not an angle in radians. For a directed relation, sign invariance would be inappropriate and the formula must preserve direction.

## 7. Restricted language and execution requirements

The proposed implementation parses the source, validates its abstract syntax tree, and lowers accepted expressions to a controlled numerical evaluator. It must not send VLM source directly to unrestricted Python `exec` or `eval`. This documentation change implements neither that evaluator nor its isolation.

For `geometry-residual-v1`, the permitted language is deliberately small:

| Allowed element | Restriction |
|---|---|
| One function definition | Name and five positional arguments exactly match the ABI; no defaults, annotations, decorators, nested functions, or extra arguments. |
| Local assignments and one final return | Straight-line body only. Assignment targets are local names; input names cannot be rebound or mutated. |
| Finite numeric literals and local names | No strings, containers, object construction, module references, or implicit external globals in the function body. |
| Arithmetic `+`, `-`, `*`, `/` and unary `+`/`-` | Scalar or fixed-shape array operations only; incompatible shapes and nonfinite intermediate results are errors. |
| Fixed array indexing | Literal nonnegative integer indices and full-axis slices; dimensions and bounds checked before execution. No data-dependent indices or slice steps. |
| Numerical calls | Only direct calls to host primitives `norm`, `dot`, `cross`, `sqrt`, `sin`, `cos`, `acos`, `abs`, `minimum`, `maximum`, and `clip`. |

The host primitives have fixed signatures: `norm(v)` maps a three-vector to a scalar; `dot(a, b)` and `cross(a, b)` accept three-vectors; `sqrt`, `sin`, `cos`, `acos`, and `abs` accept and return scalars; `minimum(a, b)` and `maximum(a, b)` accept two scalars; `clip(x, lo, hi)` accepts three scalars with `lo <= hi`. The host does not expose NumPy, Python builtins, attribute access, or arbitrary callables to generated programs.

Imports, loops, comprehensions, conditional statements or expressions, recursion, exception handling, dynamic lookup, file and network access, mutation, and user-defined helpers are forbidden. Division by zero and mathematical domain errors are failures, not automatic repair opportunities. Explicit clipping in an accepted formula may handle roundoff, but the host must not silently change a relation's formula or units.

Language checks and isolation serve different purposes. AST acceptance alone is not a sandbox or a proof of numerical correctness. The later implementation must enforce limits on source size, expression count, intermediate array shape, memory, and evaluation time; execute outside the robot's command callback; and isolate failures from the control process. Concrete budgets are host configuration, versioned with the validator policy and benchmarked before use. No performance claim is made in this draft.

The host validator also checks the intended units and binding definitions against the scene. Numerical probes and dimensional review can reveal errors, but neither certifies that a generated relation expresses the user's intent. Inspection of the formula and its geometry remains part of the research validation process.

## 8. Failure behavior and acceptance scenarios

An artifact rejected during initialization never enters the active semantic context. The system stays paused until it has a complete accepted context. During a solve, an exception, invalid shape, nonfinite intermediate or output, or execution-budget violation invalidates the current snapshot's result and produces `hold`; it must not yield a partial motion command. The local command gate remains responsible for timely stopping even if the evaluator stalls.

The required checks are documented here for later implementation; they are not claims of tests already implemented in the inherited server.

| Scenario | Required result |
|---|---|
| Source text differs from the declared hash | Reject the artifact before activation. |
| A known `program_id` arrives with changed parameters or bindings | Reject immutable-ID reuse; require a new artifact and validation. |
| Unknown ABI, missing scene feature, wrong object ID, or wrong frame | Reject initialization or context activation. |
| Reordered scene storage with stable feature IDs | Resolve the same ordered bindings and obtain the same numerical result. |
| Invalid rotation or zero-length scene axis | Reject input; do not repair it inside generated code. |
| Out-of-bounds constant index or prohibited syntax | Reject at validation. |
| Nonfinite result, divide-by-zero, domain error, or budget violation | Invalidate the solve and request `hold`. |
| Positive finite residual during approach | Record the penalty and allow normal optimization; do not mistake it for an execution fault. |
| Undirected axis sign reversed | Residual remains unchanged within the declared numerical comparison tolerance. |
| Task or scene changes while generation is running | Reject stale activation through the enclosing semantic context checks. |
| Program changes while a snapshot is solving | Keep the snapshot's immutable program and binding; apply new accepted context only to a later snapshot. |
| Identical snapshot, immutable programs, execution bindings, and evaluator version replayed | Reproduce function evaluations within the recorded numerical tolerance; bit-for-bit solver trajectory reproducibility is not promised. |

For diagnostics, each `constraint_evaluations` entry in a `ControlResult` records the program identity, raw `g`, normalization scale, and resulting penalty. Logs additionally preserve the immutable program, execution binding, validation receipt, and geometry/configuration references required for offline replay.

Next: [Runtime Lifecycle and Timing](https://github.com/ZenWang00/semantic-shared-control/wiki/Runtime-Lifecycle-and-Timing).
