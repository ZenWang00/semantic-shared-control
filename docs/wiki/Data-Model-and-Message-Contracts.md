# Data Model and Message Contracts

## Schema 2.0

All messages use `{message_type, meta, payload}`. Metadata pins schema version `2.0`, session/message/source identities, sequence and original production timestamp. Schema 1.0 and its generated-program records are rejected. Units are metres, radians and seconds; control-frame poses contain `position_xyz[3]` and a unit `quaternion_xyzw[4]`. Quaternion signs q and -q represent the same orientation.

`parse_record` and `parse_message` are strict trust boundaries. Direct immutable dataclass construction does not validate data. Unknown fields, booleans used as numbers, missing required values, nonfinite values and invalid dimensions are errors; no silent clipping, normalization or candidate dropping repairs malformed input.

## Preparation records

| Record | Fields and meaning |
| --- | --- |
| TaskContext | task_id, revision, instruction |
| SceneObservation | Observation/scene IDs, RGB/depth references and original stamps, camera intrinsics, calibration ID and capture-time camera-to-control transform |
| SceneGeometry | geometry_id, revision, scene_id, feature_set_id, observation_id, frame_id, static mode; objects, keypoints and axes |
| SemanticContext | semantic_id, task_id/task_revision, scene_id, feature_set_id, observation_id, object_relevance; contains no programs |
| CandidatePoint | candidate_id, object_id, position_xyz; a grounded desired TCP position |
| CandidateSet | candidate_set_id, task_id/task_revision, scene_id, feature_set_id, observation_id, frame_id, 1–16 candidates |
| SessionConfig | Immutable frame/model/policy IDs, requested period and motion/freshness/deadline/history limits |

IDs are immutable and references are exact. Reusing an ID with changed data is rejected. Candidate objects and relevance must resolve to the pinned geometry. Every target uses the same control frame and source episode; a raw surface point needs an explicit gripper-to-TCP offset before becoming a control target. Multiple alternatives may share one object ID but never one candidate ID.

The VLM image proposal parser is a preparation boundary, not a camera adapter. Its normalized `[y,x]` coordinates are distinct from metric `position_xyz`; tests do not claim valid perception or depth reconstruction.

## Live and computed records

| Record | Meaning |
| --- | --- |
| JoystickSample | Original sampled_at, mapping_id, enabled and u_xy inside the unit disk |
| RobotState | Original sampled_at, matching frame/TCP, measured TCP pose and measured joints; source is measured_feedback or fk_from_measured_joints |
| ControlSnapshot | Immutable task, geometry, semantic and candidate_set references; current input/state, bounded history, previous selection reference and accepted-command reference; sequence, epoch and horizon |
| CandidateScore | candidate_id, raw score, three-dimensional distance_m |
| CandidateSelection | selection_id, snapshot_id, estimator_config_id, candidate_set_id, selected_candidate_id, scores and assistance_strength |
| CandidateEvaluation | selected candidate_id, distance_m, raw_g, normalization_scale and penalty |
| ControlResult | Snapshot/selection references and input sequences; move/hold, solver/evaluation diagnostics, candidate evaluation, and a pose/horizon only for a valid move |
| CommandAcceptanceRecord | Independent local acceptance, reason, checked_at and applied-command identity |

The previous selection is replayable evidence from the same context/epoch; it never revives motion after release. The complete snapshot resolves without reading mutable devices or a clock. Source freshness and final motion permission remain runtime decisions.

A successful solver proposes a pose only. A move's quaternion is equivalent to the pinned measured quaternion; the gate also checks current feedback feasibility. `constraints_satisfied` refers to the fixed soft distance tolerance and is not motion permission. A positive distance residual during approach is normal. A hold cannot carry a target/horizon, and a failed solve cannot emit its last iterate.

Current joystick input controls motion. Nonzero historical input cannot override current neutral/disabled input. Valid candidate data and an archived accepted decision do not authorize a new command.
