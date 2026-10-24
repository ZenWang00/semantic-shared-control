# Data Model and Message Contracts

**Design Draft v0.1 — normative proposed contracts, with illustrative examples.**

This page defines transport-independent records for the complete computation chain. No HTTP endpoint, ROS message package, executable validator, or optimizer is introduced by this document. JSON is the exchange notation; image and depth bytes are stored separately. Structural checks alone do not establish geometric consistency or safe execution.

## 1. Shared conventions

All fields in the tables are required unless marked optional. A missing optional field means unavailable, not zero. JSON `NaN` and infinity are forbidden. Integers used for counters are nonnegative and limited to the exact interoperable JSON range, at most `9007199254740991`. Timestamps split seconds and nanoseconds rather than using a large floating-point nanosecond count.

Immutable identifiers are opaque nonempty strings. Examples use readable IDs; implementations may use UUIDs. An immutable artifact ID MUST NOT be reused for changed content. For a versioned entity such as a task, the complete `(task_id, revision)` reference is immutable; the task ID alone may span revisions. A reference MUST resolve within the same session unless explicitly described as an immutable external calibration or robot-model resource. Array order carries meaning only where specified; identity is established by IDs, never by labels such as “bottle.”

| Convention | Definition |
| --- | --- |
| Linear units | Metres and metres/second. Aligned depth is in metres. |
| Angular units | Radians and radians/second. Revolute joint positions are radians. |
| Duration | Finite seconds; positive for periods, deadlines, budgets, and limits. |
| Geometry frame | `SessionConfig.control_frame`, fixed throughout an active scene. |
| Pose | `position_xyz[3]` and unit `quaternion_xyzw[4]`; orientation maps coordinates from the named child frame into the parent frame. |
| LIO defaults | Parent `LIO_base_link`, TCP `lio_tcp_link`; TCP X approach, Y closing, Z lateral. These are explicit deployment settings, not universal robot constants. |
| Rotation in function ABI | `R_control_tcp[:, i]` is TCP axis `i` expressed in the control frame. It is derived from the quaternion. |
| Camera coordinates | Rectified optical frame: X right, Y down, Z forward. Pixels are `(u,v)`, origin at the top-left. |
| Numerical tolerances | A versioned host validation policy defines quaternion/unit-axis and sum-to-one tolerances. Producers emit normalized values; receivers do not silently repair arbitrary invalid input. |

### Envelope and common value types

Each published message uses `{"message_type": "TypeName", "meta": MessageMeta, "payload": Payload}`. Nested immutable artifacts do not need a second envelope. The envelope version describes the data contract (`1.0`); the document's Design Draft v0.1 is a separate publication label.

| Type | Fields and constraints |
| --- | --- |
| `MessageMeta` | `schema_version: "1.0"`; `session_id: string`; `message_id: string`; `source_id: string`; `sequence: integer`; `produced_at: TimePoint`. Sequence increases per source within the session. |
| `TimePoint` | `clock_id: string`; `sec: integer`; `nanosec: integer` in `[0, 1000000000)`. Clock ID includes the relevant clock domain/epoch. |
| `Pose` | `position_xyz: number[3]`; `quaternion_xyzw: number[4]`. No redundant matrix or Euler representation. |
| `StampedTransform` | `parent_frame: string`; `child_frame: string`; `sampled_at: TimePoint`; `pose: Pose`. `p_parent = R_parent_child p_child + t_parent_child`. |
| `AssetRef` | `asset_id: string`; `media_type: string`; `encoding: string`; `shape: integer[]`. The session asset registry supplies immutable bytes through the selected transport; a path on one machine is not automatically meaningful on another. |
| `ProgramRef` | `program_id: string`; `source_sha256: string` containing exactly 64 lowercase hexadecimal characters. Program ID identifies the complete immutable artifact; the hash covers exact UTF-8 source text only. |
| `GeometryRef` | `geometry_id: string`; `revision: integer >= 1`. Both must match the resolved artifact. |

IDs, timestamps, hashes, and validation receipts are assigned or verified by host code. A VLM proposal is not authoritative for these metadata fields or for measured coordinates.

## 2. Configuration and task

### SessionConfig

Producer: host experiment configuration. Consumers: adapters, snapshot assembler, optimizer, and local command gate. The configuration is immutable; an update creates a new `config_id` and triggers controlled bundle activation.

| Field | Type | Meaning |
| --- | --- | --- |
| `config_id` | string | Immutable configuration identity. |
| `control_frame`, `tcp_frame` | string | Explicit fixed parent and controlled tool frame. |
| `robot_model_id` | string | Immutable kinematic/model configuration identity. |
| `validation_policy_id` | string | Host-owned numeric and schema validation policy. |
| `motion_constraint_set_id` | string | Host-owned collision/workspace/joint/motion constraint set; not VLM-generated. |
| `control_period_s` | number > 0 | Requested outer-loop period, not measured performance. |
| `max_xy_speed_mps` | number > 0 | Scale for requested planar velocity `v_user_xy = max_xy_speed_mps * u_xy`. |
| `max_linear_speed_mps` | number > 0 | Host bound on full TCP translation. |
| `max_angular_speed_radps` | number > 0 | Host bound on TCP rotation. |
| `joystick_timeout_s`, `robot_state_timeout_s` | number > 0 | Maximum accepted source age, with local reception watchdogs as an additional check. |
| `solve_budget_s`, `max_command_age_s` | number > 0 | Solve budget and maximum snapshot-to-command-acceptance age, measured by the robot's local monotonic clock. |
| `history_window_s` | number > 0 | Maximum time span of intent history. |
| `max_capture_skew_s` | number >= 0 | Accepted difference between aligned RGB/depth source stamps within the same clock domain. |

Configuration values require experimental validation. In particular, choosing a short period does not prove that computation or the robot meets it. Host step limits must account for both input magnitude and `dt_s`; the policy must disallow nonzero assistance motion when the latest valid input is neutral or disabled.

### TaskContext

Producer: operator/task interface. Consumers: VLM service and bundle manager.

`task_id: string`, `revision: integer >= 1`, `instruction: nonempty string`. Editing the instruction increments the revision under the same task ID. Starting an unrelated task may create a new ID. A semantic context is usable only for its exact task ID and revision.

## 3. Observation and grounded geometry

### SceneObservation

Producer: robot-side capture adapter. Consumers: perception/VLM preparation and provenance store.

| Field | Type | Meaning |
| --- | --- | --- |
| `observation_id`, `scene_id` | string | Capture identity and static scene episode. |
| `rgb_stamp`, `depth_stamp` | TimePoint | Original source timestamps, not upload times. |
| `camera_frame` | string | Optical frame associated with the aligned images. |
| `rgb_ref`, `depth_ref` | AssetRef | RGB `uint8_rgb`, shape `[H,W,3]`; depth `float32_m`, shape `[H,W]`. |
| `K` | number[3][3] | Intrinsics for the actual supplied image resolution; positive focal lengths. |
| `projection_model` | `"pinhole_rectified"` | Distortion correction and depth alignment are preprocessing requirements. |
| `calibration_id` | string | Immutable camera calibration/alignment identity. |
| `T_control_camera` | StampedTransform | Camera-to-control transform evaluated at `rgb_stamp`; frames must match the configured control and camera frames. |

For valid depth `z`, backprojection is `x=(u-cx)z/fx`, `y=(v-cy)z/fy`. Invalid/nonpositive depth cannot generate a valid keypoint. Depth files may encode invalid pixels, but resulting geometry MUST contain finite valid points. Capture sync and TF availability must be checked before accepting an observation. The source of `K` and the calibration validity must be retained; the upload arrival time cannot repair missing capture-time TF.

### SceneGeometry

Producer: perception. Consumers: VLM prompting, program validation, and snapshot assembly.

Required fields: `geometry_id`, `scene_id`, `feature_set_id` (strings); `revision` (integer >= 1); `observation_id` (string); `frame_id` (control frame); `mode: "static"`; `objects: Object[]`; `keypoints: Keypoint[]`; `axes: Axis[]`.

| Nested type | Fields | Meaning |
| --- | --- | --- |
| `Object` | `object_id: string`, `label: string` | Stable identity within the scene; label is explanatory only. |
| `Keypoint` | `keypoint_id: string`, `object_id: string`, `position_xyz: number[3]` | Grounded point in the control frame. Its intended physical meaning is preserved by the feature-set definition. |
| `Axis` | `axis_id: string`, `object_id: string`, `origin_keypoint_id: string`, `unit_direction_xyz: number[3]`, `directed: boolean` | Line origin and normalized direction. If not directed, reversing the direction leaves the geometric meaning unchanged. |

Object IDs and feature IDs must be unique within their namespaces. Ownership references must exist; an axis origin belongs to its object. Empty or degenerate features do not count as valid geometry. A TCP approach target must be grounded as an appropriate approach point; a raw surface point is not automatically a valid TCP target.

`feature_set_id` changes when feature membership, identity, ownership, physical meaning, or axis directedness changes. `revision` changes when geometry values are updated under a preserved definition, and is scoped to `(scene_id, feature_set_id)`. A new revision also gets a new immutable `geometry_id`. The initial static prototype does not accept moving-object tracking updates: a moved scene is invalidated and reinitialized. This separation reserves an unambiguous extension path without implementing dynamics now.

## 4. Semantics and generated programs

### SemanticContext

Producer: host wrapper around a VLM response. Consumers: bundle manager and weight update.

| Field | Type | Meaning |
| --- | --- | --- |
| `semantic_id` | string | Immutable accepted semantic result. |
| `task_id`, `task_revision` | string, integer >= 1 | Exact task dependency. |
| `scene_id`, `feature_set_id` | string | Object/feature identity dependencies. |
| `observation_id` | string | Observation used for generation; preserves provenance rather than pretending the result is a new observation. |
| `object_relevance` | array of `{object_id: string, task_relevance: number}` | Scores in `[0,1]`, not calibrated probabilities and not required to sum to one. |
| `program_refs` | ProgramRef[] | Exact immutable generated artifacts. |

The candidate set is the nonempty set of objects in `object_relevance`; IDs are unique and resolve in geometry. Each assisted candidate has at least one compatible program. `program_refs` must resolve to artifacts whose object and feature bindings exist. Generated source alone is not an active semantic bundle: compatible host validation receipts and execution bindings are also required.

`ConstraintProgram`, `Parameter`, `ProgramExecutionBinding`, and `ProgramValidationReceipt` are specified in [Generated Constraint Program Contract](https://github.com/ZenWang00/semantic-shared-control/wiki/Generated-Constraint-Program-Contract). The binding supplies host normalization and weights. Every change to the immutable program, including its parameters or binding order, requires a new program ID and validation; a source hash alone does not identify those non-code fields.

## 5. Live inputs

### JoystickSample

Producer: calibrated device adapter. Consumers: local gate, snapshot assembly, and intent history.

`sampled_at: TimePoint`; `mapping_id: string`; `u_xy: number[2]` with Euclidean norm <= 1; `enabled: boolean`.

The envelope sequence is the input sequence. `mapping_id` identifies immutable calibration, selected physical axes, sign convention, normalization, and dead-zone processing. Raw SpaceMouse axes are not interchangeable with this normalized vector. In the algorithm contract, positive components request positive control-frame X/Y motion. `enabled` is a local authorization/control state; it must not be inferred from a VLM response.

Current input controls current motion. History provides target-preference evidence and MUST NOT keep the robot moving after current input becomes zero or disabled. Device sampling time is preserved; downstream republishing does not make an old sample new.

### RobotState

Producer: measured-state adapter. Consumers: snapshot assembler, host constraints, and local command gate.

| Field | Type | Meaning |
| --- | --- | --- |
| `sampled_at` | TimePoint | Time corresponding to the measurements. |
| `frame_id`, `tcp_frame` | string | Pose parent and controlled child; match configuration. |
| `tcp_pose` | Pose | Measured TCP or FK derived from the accompanying measured joints. |
| `joint_names` | string[] | Unique revolute-joint names in the model. |
| `joint_positions_rad` | number[] | Measured values, same length/order as names. |
| `joint_velocities_radps` | number[], optional | Same length/order when available; absence is not zero velocity. |
| `state_source` | `"measured_feedback"` or `"fk_from_measured_joints"` | Provenance; commanded setpoints are excluded. |

The adapter must ensure pose and joints describe the same measurement instant or a documented synchronized estimate. New metadata on an old commanded pose is not measured feedback. This first contract is for the inherited revolute arm; adding prismatic joints requires an explicit unit-aware extension.

## 6. Snapshot, fusion output, and candidate command

### ControlSnapshot

Producer: robot-side snapshot assembler. Consumers: weight updater and optimization worker. A snapshot is immutable after construction and uses one coherent bundle.

| Field | Type | Meaning |
| --- | --- | --- |
| `snapshot_id` | string | Unique computation input identity. |
| `snapshot_seq`, `control_epoch` | integer | Robot-issued cycle order and cancellation generation. |
| `config_id` | string | Exact configuration. |
| `task_ref` | `{task_id, revision}` | Exact task dependency. |
| `dt_s` | number > 0 | Intended candidate motion horizon, not network latency. |
| `joystick_ref`, `robot_state_ref` | string | Immutable message IDs for current input and measured state. |
| `joystick_history_refs` | string[] | Ordered current-session sample IDs, bounded by configured history span; empty is valid initially. |
| `geometry_ref` | GeometryRef | Exact scene geometry values. |
| `semantic_id` | string | Exact semantics. |
| `program_refs` | ProgramRef[] | Exact code versions used by this computation. |
| `execution_binding_refs` | string[] | Host execution binding IDs, including normalization/weights/validation receipts. |
| `previous_applied_command_id` | string or null | Last command actually accepted/applied by the local adapter; not merely the most recent proposed result. |

References are resolved to immutable values before computation. The function evaluator performs no network/file lookup. The transport may inline referenced records or pre-transfer/cache them; the semantic identity is unchanged. The robot keeps a local registry associating snapshot ID/sequence with monotonic dispatch time and deadline; a remote worker cannot extend this deadline.

The snapshot fixes scene and task dependencies; no new VLM output can be substituted mid-solve. Input age must be checked independently of the age of the snapshot itself.

### ObjectWeights

Producer: fusion/intent estimator. Consumer: optimizer.

`weights_id: string`; `snapshot_id: string`; `estimator_config_id: string`; `weights: [{object_id: string, weight: number}]`; `assistance_strength: number`.

All weights are nonnegative, sum to one within the validation policy tolerance, and cover exactly the semantic candidate set. `assistance_strength` is in `[0,1]` and is independent of relative support. These weights are normalized preference/support values, not automatically calibrated probabilities. An empty or invalid candidate set prevents this result from being used; it must not be repaired into uniform assistance. The estimator algorithm is outside this documentation revision, but its configuration must be recorded for replay.

### ControlResult

Producer: optimizer worker, or robot-side stop coordinator for a local hold. Consumer: local command gate. This record is a proposal, not evidence that a controller accepted it.

| Field | Type | Meaning |
| --- | --- | --- |
| `result_id` | string | Immutable candidate result. |
| `snapshot_id` | string or null | Causal snapshot; required for move. A local hold may have no snapshot. |
| `weights_id` | string or null | Required for move; may be null on an early failure/local hold. |
| `control_epoch`, `command_seq` | integer | Cancellation generation and robot-issued proposal order. Worker results echo the order assigned with dispatch; local holds use a new robot-issued sequence. |
| `source_joystick_seq`, `source_robot_state_seq` | integer or null | Input sequences used; required for move, unavailable inputs may be null for hold. |
| `status` | `"move"` or `"hold"` | Tagged union controlling target-field presence. |
| `evaluation_valid` | boolean | Numerical evaluation/input compatibility status. |
| `solver_status` | `"solved"`, `"infeasible"`, `"timeout"`, `"error"`, or `"not_run"` | Computational status, separate from relation satisfaction. |
| `constraints_satisfied` | boolean or null | Whether all generated soft residuals satisfy host evaluation tolerances; null when not evaluated. Not a hard-safety certification. |
| `solve_time_s` | number >= 0 | Measured duration; zero when not run. |
| `constraint_evaluations` | ConstraintEvaluation[] | Empty when evaluation was not performed. |
| `next_tcp_pose`, `horizon_s` | Pose, number > 0; move only | Next TCP pose in configured frames; horizon must equal snapshot `dt_s`. |
| `reason` | string; hold only | Required diagnostic reason such as `joystick_released`, `stale_state`, or `invalid_geometry`. |

`ConstraintEvaluation` contains `program_id: string`, `raw_g: number`, `normalization_scale: number > 0`, and `penalty: number >= 0`, where `penalty = max(0, raw_g / normalization_scale)^2` before host weight, object weight, and assistance strength are applied.

A move requires valid evaluation and a solved result. It still requires local host-constraint checks, ordering, deadline, current input enable, and measurement freshness. `constraints_satisfied=false` is allowed for intermediate approach steps because these generated relations are soft. A failed hard host check rejects the candidate regardless of solver success.

Local application bookkeeping records a separate `CommandAcceptanceRecord`: `result_id`, `command_accepted` (boolean), `reason`, `checked_at` (robot-local TimePoint), and `applied_command_id` (string or null). This is a proposed log record, not a claim of measured physical tracking. Only accepted commands may populate a later snapshot's `previous_applied_command_id`.

A hold never carries `next_tcp_pose` or `horizon_s`. The local adapter cancels the previous target and uses current valid measured feedback for hold behavior. With stale feedback, it invokes its predefined controller stop procedure instead. It cannot execute an old remote pose as a substitute for current feedback.

## 7. Lifecycle and compatibility rules

| Identity | Changes when | Consequence |
| --- | --- | --- |
| `session_id` | Runtime restart, clock epoch reset, or new experiment session | Reject all earlier-session messages/results. |
| `task_id/revision` | Task instruction changes | Old semantics cannot activate. Valid geometry may be reused for a new semantic request. |
| `scene_id` | Static scene is reset, moved, or reinitialized | Invalidate old geometry/semantic bindings and pending motion. |
| `feature_set_id` | Feature identities or definitions change | Rebind/regenerate and revalidate dependent programs. |
| `geometry_id/revision` | Geometry values change | Snapshot uses the exact version. Dynamic updates remain unimplemented in v0.1. |
| `program_id` | Any artifact content changes | Revalidation; source hash alone is insufficient for parameter/binding changes. |
| `config_id` | Limits, frames, periods, or host configuration change | Pause and activate a compatible new bundle. |
| `control_epoch` | Release/disable cancellation, invalidation, or fault transition | Reject pending earlier-epoch moves even if otherwise unexpired. |

Clock comparison is valid only within the same declared domain/epoch. Source freshness, local reception liveness, and snapshot turnaround are distinct checks. Receipt or production time MUST NOT reset the source observation age. Static geometry has validity tied to scene assumptions rather than a short joystick-style TTL.

Unknown major schema versions cannot activate. Unknown fields are not silently interpreted as permission to move; extensions require a documented schema revision and compatible readers. The first implementation must distinguish structural errors, unresolved references, invalid semantic content, and runtime failures in diagnostics.

The [runtime page](https://github.com/ZenWang00/semantic-shared-control/wiki/Runtime-Lifecycle-and-Timing) contains initialization, normal control, and cancellation sequence diagrams.

## 8. Coherent illustrative exchange

The following fixture is a complete example catalog, not a live API response or executable configuration. All limits, timestamps, poses, scores, outcomes, asset IDs, model IDs, and validation-policy IDs are illustrative. Asset bytes and hardware models are not shipped. Metadata timestamps from different clock domains intentionally have unrelated numeric values.

The two objects share a distance-function source but use different immutable program IDs and keypoint bindings. This catalog includes complete program artifacts conforming to the generated-program contract, as well as their references. The bottle relation remains unsatisfied during approach, while the numerical result is valid. The next target moves only a small distance from measured pose; it does not jump to the object.

```json
{
  "fixture_kind": "illustrative_contract_catalog",
  "constraint_programs": [
    {
      "program_id": "program-bottle-distance-v1",
      "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2",
      "function_api_version": "geometry-residual-v1",
      "object_id": "object-bottle",
      "description": "Approach the grounded bottle TCP target within the declared distance tolerance.",
      "entrypoint": "evaluate",
      "source_code": "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n    distance = norm(p_tcp - keypoints[0])\n    return distance - params[0]\n",
      "keypoint_bindings": [
        "kp-bottle-approach"
      ],
      "axis_bindings": [],
      "parameters": [
        {
          "name": "distance_tolerance",
          "value": 0.02,
          "unit": "m"
        }
      ],
      "output_unit": "m"
    },
    {
      "program_id": "program-cup-distance-v1",
      "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2",
      "function_api_version": "geometry-residual-v1",
      "object_id": "object-cup",
      "description": "Approach the grounded cup TCP target within the declared distance tolerance.",
      "entrypoint": "evaluate",
      "source_code": "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n    distance = norm(p_tcp - keypoints[0])\n    return distance - params[0]\n",
      "keypoint_bindings": [
        "kp-cup-approach"
      ],
      "axis_bindings": [],
      "parameters": [
        {
          "name": "distance_tolerance",
          "value": 0.02,
          "unit": "m"
        }
      ],
      "output_unit": "m"
    }
  ],
  "validation_receipts": [
    {
      "receipt_id": "receipt-bottle-v1",
      "program_id": "program-bottle-distance-v1",
      "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2",
      "function_api_version": "geometry-residual-v1",
      "scene_id": "scene-demo-001",
      "feature_set_id": "features-demo-001",
      "validation_geometry_id": "geometry-demo-001",
      "validation_geometry_revision": 1,
      "validator_policy_version": "numeric-policy-demo-v1",
      "status": "accepted",
      "diagnostics": []
    },
    {
      "receipt_id": "receipt-cup-v1",
      "program_id": "program-cup-distance-v1",
      "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2",
      "function_api_version": "geometry-residual-v1",
      "scene_id": "scene-demo-001",
      "feature_set_id": "features-demo-001",
      "validation_geometry_id": "geometry-demo-001",
      "validation_geometry_revision": 1,
      "validator_policy_version": "numeric-policy-demo-v1",
      "status": "accepted",
      "diagnostics": []
    }
  ],
  "execution_bindings": [
    {
      "binding_id": "binding-bottle-v1",
      "program_id": "program-bottle-distance-v1",
      "config_id": "config-demo-001",
      "normalization_scale": 0.1,
      "weight": 1.0,
      "validation_receipt_id": "receipt-bottle-v1"
    },
    {
      "binding_id": "binding-cup-v1",
      "program_id": "program-cup-distance-v1",
      "config_id": "config-demo-001",
      "normalization_scale": 0.1,
      "weight": 1.0,
      "validation_receipt_id": "receipt-cup-v1"
    }
  ],
  "messages": [
    {
      "message_type": "SessionConfig",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-config-001",
        "source_id": "robot-demo",
        "sequence": 1,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 0
        }
      },
      "payload": {
        "config_id": "config-demo-001",
        "control_frame": "LIO_base_link",
        "tcp_frame": "lio_tcp_link",
        "robot_model_id": "lio-model-demo",
        "validation_policy_id": "numeric-policy-demo-v1",
        "motion_constraint_set_id": "host-limits-demo-v1",
        "control_period_s": 0.02,
        "max_xy_speed_mps": 0.1,
        "max_linear_speed_mps": 0.1,
        "max_angular_speed_radps": 0.2,
        "joystick_timeout_s": 0.2,
        "robot_state_timeout_s": 0.1,
        "solve_budget_s": 0.01,
        "max_command_age_s": 0.02,
        "history_window_s": 0.5,
        "max_capture_skew_s": 0.01
      }
    },
    {
      "message_type": "TaskContext",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-task-001",
        "source_id": "robot-demo",
        "sequence": 2,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 0
        }
      },
      "payload": {
        "task_id": "task-demo-001",
        "revision": 1,
        "instruction": "Help me approach the bottle or cup while I guide the arm."
      }
    },
    {
      "message_type": "SceneObservation",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-observation-001",
        "source_id": "robot-demo",
        "sequence": 0,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 0
        }
      },
      "payload": {
        "observation_id": "observation-demo-001",
        "scene_id": "scene-demo-001",
        "rgb_stamp": {
          "clock_id": "robot-clock-demo",
          "sec": 10,
          "nanosec": 0
        },
        "depth_stamp": {
          "clock_id": "robot-clock-demo",
          "sec": 10,
          "nanosec": 1000000
        },
        "camera_frame": "camera_color_optical_frame",
        "rgb_ref": {
          "asset_id": "rgb-demo-001",
          "media_type": "image/png",
          "encoding": "uint8_rgb",
          "shape": [
            480,
            640,
            3
          ]
        },
        "depth_ref": {
          "asset_id": "depth-demo-001",
          "media_type": "application/octet-stream",
          "encoding": "float32_m",
          "shape": [
            480,
            640
          ]
        },
        "K": [
          [
            600,
            0,
            319.5
          ],
          [
            0,
            600,
            239.5
          ],
          [
            0,
            0,
            1
          ]
        ],
        "projection_model": "pinhole_rectified",
        "calibration_id": "calibration-demo-001",
        "T_control_camera": {
          "parent_frame": "LIO_base_link",
          "child_frame": "camera_color_optical_frame",
          "sampled_at": {
            "clock_id": "robot-clock-demo",
            "sec": 10,
            "nanosec": 0
          },
          "pose": {
            "position_xyz": [
              0,
              0,
              0
            ],
            "quaternion_xyzw": [
              0,
              0,
              0,
              1
            ]
          }
        }
      }
    },
    {
      "message_type": "SceneGeometry",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-geometry-001",
        "source_id": "workstation-demo",
        "sequence": 1,
        "produced_at": {
          "clock_id": "workstation-clock-demo",
          "sec": 900,
          "nanosec": 0
        }
      },
      "payload": {
        "geometry_id": "geometry-demo-001",
        "scene_id": "scene-demo-001",
        "feature_set_id": "features-demo-001",
        "revision": 1,
        "observation_id": "observation-demo-001",
        "frame_id": "LIO_base_link",
        "mode": "static",
        "objects": [
          {
            "object_id": "object-bottle",
            "label": "bottle"
          },
          {
            "object_id": "object-cup",
            "label": "cup"
          }
        ],
        "keypoints": [
          {
            "keypoint_id": "kp-bottle-approach",
            "object_id": "object-bottle",
            "position_xyz": [
              0.5,
              0.1,
              0.2
            ]
          },
          {
            "keypoint_id": "kp-bottle-origin",
            "object_id": "object-bottle",
            "position_xyz": [
              0.5,
              0.1,
              0.1
            ]
          },
          {
            "keypoint_id": "kp-cup-approach",
            "object_id": "object-cup",
            "position_xyz": [
              0.5,
              -0.15,
              0.15
            ]
          },
          {
            "keypoint_id": "kp-cup-origin",
            "object_id": "object-cup",
            "position_xyz": [
              0.5,
              -0.15,
              0.1
            ]
          }
        ],
        "axes": [
          {
            "axis_id": "axis-bottle-body",
            "object_id": "object-bottle",
            "origin_keypoint_id": "kp-bottle-origin",
            "unit_direction_xyz": [
              0,
              0,
              1
            ],
            "directed": false
          },
          {
            "axis_id": "axis-cup-body",
            "object_id": "object-cup",
            "origin_keypoint_id": "kp-cup-origin",
            "unit_direction_xyz": [
              0,
              0,
              1
            ],
            "directed": false
          }
        ]
      }
    },
    {
      "message_type": "SemanticContext",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-semantic-001",
        "source_id": "workstation-demo",
        "sequence": 2,
        "produced_at": {
          "clock_id": "workstation-clock-demo",
          "sec": 900,
          "nanosec": 0
        }
      },
      "payload": {
        "semantic_id": "semantic-demo-001",
        "task_id": "task-demo-001",
        "task_revision": 1,
        "scene_id": "scene-demo-001",
        "feature_set_id": "features-demo-001",
        "observation_id": "observation-demo-001",
        "object_relevance": [
          {
            "object_id": "object-bottle",
            "task_relevance": 0.8
          },
          {
            "object_id": "object-cup",
            "task_relevance": 0.6
          }
        ],
        "program_refs": [
          {
            "program_id": "program-bottle-distance-v1",
            "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2"
          },
          {
            "program_id": "program-cup-distance-v1",
            "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2"
          }
        ]
      }
    },
    {
      "message_type": "JoystickSample",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-joystick-001",
        "source_id": "robot-demo",
        "sequence": 3,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 0
        }
      },
      "payload": {
        "sampled_at": {
          "clock_id": "robot-clock-demo",
          "sec": 10,
          "nanosec": 995000000
        },
        "mapping_id": "base-xy-device-map-demo",
        "u_xy": [
          0.4,
          0
        ],
        "enabled": true
      }
    },
    {
      "message_type": "RobotState",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-state-001",
        "source_id": "robot-demo",
        "sequence": 4,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 0
        }
      },
      "payload": {
        "sampled_at": {
          "clock_id": "robot-clock-demo",
          "sec": 10,
          "nanosec": 998000000
        },
        "frame_id": "LIO_base_link",
        "tcp_frame": "lio_tcp_link",
        "tcp_pose": {
          "position_xyz": [
            0.4,
            0,
            0.35
          ],
          "quaternion_xyzw": [
            0,
            0,
            0,
            1
          ]
        },
        "joint_names": [
          "illustrative_joint_1",
          "illustrative_joint_2",
          "illustrative_joint_3",
          "illustrative_joint_4",
          "illustrative_joint_5",
          "illustrative_joint_6"
        ],
        "joint_positions_rad": [
          0,
          0.2,
          -0.4,
          0.1,
          0.3,
          0
        ],
        "state_source": "measured_feedback"
      }
    },
    {
      "message_type": "ControlSnapshot",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-snapshot-001",
        "source_id": "robot-demo",
        "sequence": 5,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 0
        }
      },
      "payload": {
        "snapshot_id": "snapshot-demo-001",
        "snapshot_seq": 1,
        "control_epoch": 1,
        "config_id": "config-demo-001",
        "task_ref": {
          "task_id": "task-demo-001",
          "revision": 1
        },
        "dt_s": 0.02,
        "joystick_ref": "message-joystick-001",
        "robot_state_ref": "message-state-001",
        "joystick_history_refs": [],
        "geometry_ref": {
          "geometry_id": "geometry-demo-001",
          "revision": 1
        },
        "semantic_id": "semantic-demo-001",
        "program_refs": [
          {
            "program_id": "program-bottle-distance-v1",
            "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2"
          },
          {
            "program_id": "program-cup-distance-v1",
            "source_sha256": "a686a0b43a1e5e8dd24b8cab1f8015bb81c89975e4d42fd253dee29bcd40cea2"
          }
        ],
        "execution_binding_refs": [
          "binding-bottle-v1",
          "binding-cup-v1"
        ],
        "previous_applied_command_id": null
      }
    },
    {
      "message_type": "ObjectWeights",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-weights-001",
        "source_id": "robot-demo",
        "sequence": 6,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 0
        }
      },
      "payload": {
        "weights_id": "weights-demo-001",
        "snapshot_id": "snapshot-demo-001",
        "estimator_config_id": "estimator-demo-v1",
        "weights": [
          {
            "object_id": "object-bottle",
            "weight": 0.7
          },
          {
            "object_id": "object-cup",
            "weight": 0.3
          }
        ],
        "assistance_strength": 0.5
      }
    },
    {
      "message_type": "ControlResult",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-result-001",
        "source_id": "robot-demo",
        "sequence": 7,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 6000000
        }
      },
      "payload": {
        "result_id": "result-demo-001",
        "snapshot_id": "snapshot-demo-001",
        "weights_id": "weights-demo-001",
        "control_epoch": 1,
        "command_seq": 1,
        "source_joystick_seq": 3,
        "source_robot_state_seq": 4,
        "status": "move",
        "evaluation_valid": true,
        "solver_status": "solved",
        "constraints_satisfied": false,
        "solve_time_s": 0.006,
        "constraint_evaluations": [
          {
            "program_id": "program-bottle-distance-v1",
            "raw_g": 0.18581593718660372,
            "normalization_scale": 0.1,
            "penalty": 3.452756251253586
          },
          {
            "program_id": "program-cup-distance-v1",
            "raw_g": 0.24896133551125896,
            "normalization_scale": 0.1,
            "penalty": 6.198174657954964
          }
        ],
        "next_tcp_pose": {
          "position_xyz": [
            0.4004,
            0,
            0.3498
          ],
          "quaternion_xyzw": [
            0,
            0,
            0,
            1
          ]
        },
        "horizon_s": 0.02
      }
    },
    {
      "message_type": "JoystickSample",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-joystick-002",
        "source_id": "robot-demo",
        "sequence": 8,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 20000000
        }
      },
      "payload": {
        "sampled_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 20000000
        },
        "mapping_id": "base-xy-device-map-demo",
        "u_xy": [
          0,
          0
        ],
        "enabled": true
      }
    },
    {
      "message_type": "ControlResult",
      "meta": {
        "schema_version": "1.0",
        "session_id": "session-demo-001",
        "message_id": "message-hold-002",
        "source_id": "robot-demo",
        "sequence": 9,
        "produced_at": {
          "clock_id": "robot-clock-demo",
          "sec": 11,
          "nanosec": 20100000
        }
      },
      "payload": {
        "result_id": "result-hold-demo-002",
        "snapshot_id": null,
        "weights_id": null,
        "control_epoch": 2,
        "command_seq": 2,
        "source_joystick_seq": 8,
        "source_robot_state_seq": 4,
        "status": "hold",
        "evaluation_valid": false,
        "solver_status": "not_run",
        "constraints_satisfied": null,
        "solve_time_s": 0,
        "constraint_evaluations": [],
        "reason": "joystick_released"
      }
    }
  ],
  "command_acceptance_records": [
    {
      "result_id": "result-demo-001",
      "command_accepted": false,
      "reason": "earlier_control_epoch_after_local_release",
      "checked_at": {
        "clock_id": "robot-clock-demo",
        "sec": 11,
        "nanosec": 21000000
      },
      "applied_command_id": null
    }
  ]
}
```

The catalog is grouped by conceptual type, not by send order. The final acceptance record illustrates the result arriving after release; it is rejected despite having been numerically valid. Joint names and the measurement/model relationship are synthetic placeholders, not a verified LIO FK sample. Validation receipts in this example describe an illustrative accepted state; no deployed validator or robot validation is claimed.

## 9. Rejection examples

These are complete value-level counterexamples, not transport envelopes. They are syntactically valid JSON but semantically invalid.

```json
{
  "invalid_joystick_payload": {
    "sampled_at": {
      "clock_id": "robot-clock-demo",
      "sec": 10,
      "nanosec": 995000000
    },
    "mapping_id": "base-xy-device-map-demo",
    "u_xy": [
      1,
      1
    ],
    "enabled": true
  },
  "invalid_axis": {
    "axis_id": "axis-invalid",
    "object_id": "object-bottle",
    "origin_keypoint_id": "missing-keypoint",
    "unit_direction_xyz": [
      0,
      0,
      0
    ],
    "directed": false
  },
  "invalid_hold_result": {
    "result_id": "result-hold-demo-002",
    "snapshot_id": null,
    "weights_id": null,
    "control_epoch": 2,
    "command_seq": 2,
    "source_joystick_seq": 8,
    "source_robot_state_seq": 4,
    "status": "hold",
    "evaluation_valid": false,
    "solver_status": "not_run",
    "constraints_satisfied": null,
    "solve_time_s": 0,
    "constraint_evaluations": [],
    "reason": "joystick_released",
    "next_tcp_pose": {
      "position_xyz": [
        0.5,
        0.1,
        0.2
      ],
      "quaternion_xyzw": [
        0,
        0,
        0,
        1
      ]
    }
  }
}
```

The joystick exceeds the unit-disk magnitude, the axis has an unresolved origin and zero direction, and the hold incorrectly carries a remote target. Each must be rejected by the appropriate validation layer. See [Validation and Legacy Integration](https://github.com/ZenWang00/semantic-shared-control/wiki/Validation-and-Legacy-Integration) for the full proposed acceptance matrix.
