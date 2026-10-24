# Validation and Legacy Integration

**Semantic Shared Control — Architecture and Data Contracts · Design Draft v0.1**

This page defines the evidence required before implementing and connecting the proposed contracts to a robot. It also records the parts of the inherited VLA project that can support that work. The runtime scenarios below are an acceptance specification, **not completed software, simulation, or robot tests**. This documentation release adds no controller, protocol adapter, optimizer, or generated-program executor.

Read the [data contracts](https://github.com/ZenWang00/semantic-shared-control/wiki/Data-Model-and-Message-Contracts), [generated-program contract](https://github.com/ZenWang00/semantic-shared-control/wiki/Generated-Constraint-Program-Contract), and [runtime lifecycle](https://github.com/ZenWang00/semantic-shared-control/wiki/Runtime-Lifecycle-and-Timing) for the normative design. Return to [Home](https://github.com/ZenWang00/semantic-shared-control/wiki).

## 1. Validation levels and release evidence

Validation must distinguish a readable message from a valid decision and a physically verified behavior.

| Level | What it establishes | What it cannot establish |
|---|---|---|
| Documentation consistency | English prose, parseable JSON examples, consistent names and units, correct references, and readable diagrams | An implemented validator or working robot integration |
| Structural validation | Required fields, types, dimensions, numeric domains, and the mutually exclusive `move`/`hold` shapes | That referenced objects exist, a frame is correct, or an input is fresh |
| Semantic and replay validation | Referential integrity, compatible versions, coordinate conventions, immutable snapshots, deterministic function evaluation, and command acceptance rules under simulated timing | Physical tracking performance, network bounds, or a safe operating envelope |
| Integration and hardware validation | Behavior of the actual sensors, transport, robot model, controller, and stop interface under measured conditions | A general real-time guarantee or a safety certification |

The first level is appropriate for this release. The remaining levels require future implementation and recorded results. A JSON parser alone does not test the structural contract; for example, it accepts a three-element quaternion. Likewise, a structurally correct `RobotState` cannot prove that its producer used measured feedback.

For each future test run, record the software revisions, configuration ID, robot model and calibration IDs, input artifacts, expected outcome, actual outcome, and diagnostic logs. A hardware result must also identify the machine, controller configuration, and test conditions. A configured deadline is a requirement to be tested, not evidence that the deadline is met.

## 2. Proposed acceptance scenarios

The following matrix is a minimum implementation acceptance suite. All scenarios are **proposed and not executed in this documentation release**. Rejection means that invalid data cannot activate a scene or produce an accepted motion command; it does not require every component to implement the same error transport.

### Message structure and numeric meaning

| ID | Scenario | Expected outcome |
|---|---|---|
| S01 | Load the complete bottle-and-cup example from the data-model page. | Every example parses; required fields and dimensions match the documented contract. |
| S02 | Remove a required reference, supply a string for a numeric field, or use an unsupported schema version. | Reject at the corresponding structural or version boundary; no implicit replacement with zero or the current version. |
| S03 | Supply a two-element position, a three-element quaternion, mismatched joint-name/position lengths, or an incorrectly shaped camera matrix. | Reject before constructing numeric inputs for the optimizer. |
| S04 | Supply a zero quaternion, a zero-length axis, or a non-unit direction outside the contract's validation tolerance. | Reject the invalid geometric representation; do not silently fabricate an orientation or direction. |
| S05 | Supply a non-finite numeric value, including one produced by an internal calculation. | Reject before serialization or before the value enters the next computation; non-standard JSON `NaN`/`Infinity` is not accepted. |
| S06 | Supply a joystick vector outside the unit disk or an assistance strength outside `[0,1]`. | Reject the contract violation. Device calibration and dead-zone processing belong to the input adapter. |
| S07 | Supply negative object weights, weights that do not sum to one within the documented tolerance, or no valid candidate. | Reject invalid weights; no-candidate operation enters `hold` rather than manufacturing uniform weights. |
| S08 | Supply `status="move"` without its target/horizon, or `status="hold"` with a target pose. | Reject the invalid result branch. A valid `hold` carries a reason and no motion target. |

### Identity, frames, provenance, and time

| ID | Scenario | Expected outcome |
|---|---|---|
| P01 | Reference a missing object/keypoint/axis, or bind a program to an incompatible feature set. | Reject the bundle before activation; array position alone cannot establish feature identity. |
| P02 | Change the task revision while a VLM request is running, then deliver the old answer. | The old answer remains inactive. The active semantics must match the current task and scene. |
| P03 | Reinitialize a moved scene, then receive geometry or semantics for the previous `scene_id`. | Reject the old result even if its arrival time is recent. |
| P04 | Mix geometry, robot state, and a target expressed in different frames. | Reject the mismatch unless a verified adapter performs the explicit transform before contract construction. Renaming a frame is not a transform. |
| P05 | Capture with an arm-mounted camera, move the arm, then complete perception. | World geometry uses the transform associated with capture, not the camera's pose at inference completion. |
| P06 | Give the workstation and robot clocks a large offset while preserving each local clock's progression. | No cross-machine timestamp subtraction is used for freshness. Robot-local elapsed time associated with the snapshot still expires the result correctly. |
| P07 | Receive new geometry or configuration while one solve is running. | The in-flight solve retains its resolved input versions; only a later snapshot may use the new values. Incompatible results cannot pass the local acceptance gate. |
| P08 | Substitute a commanded pose, IK output, or command-topic `JointState` for measured robot state. | Provenance validation or the adapter test fails; a correctly shaped command is not accepted as measured feedback. |
| P09 | Replay an older source sequence or a message from an earlier session. | It cannot replace newer active state or revive an old control session. |
| P10 | Make the joystick or measured state exceed its configured age limit. | Stop producing accepted motion commands and enter the appropriate local hold/stop path. A missing sample is not a zero-valued sample. |

### Generated programs and optimization

| ID | Scenario | Expected outcome |
|---|---|---|
| G01 | Evaluate a validated distance relation with a finite positive residual during approach. | Evaluation succeeds; the soft penalty is positive. An unmet relation is not itself a runtime failure or a reason to reject every intermediate step. |
| G02 | Evaluate the same relation at or inside its tolerance. | `g <= 0` produces zero hinge penalty; the raw residual remains available in the diagnostic record. |
| G03 | Set the host normalization scale to zero or combine a scale with the wrong unit. | Reject the configuration before evaluating the penalty. |
| G04 | Submit generated source with imports, I/O, dynamic execution, loops, recursion, or input mutation. | Reject it before activation according to the restricted-language contract. AST acceptance alone is not claimed to be a complete sandbox. |
| G05 | Change source bytes after validation, reuse a report for another API version, or change the feature bindings. | The artifact cannot activate under the old validation report. |
| G06 | Trigger an out-of-bounds access, domain error, non-scalar return, exception, or non-finite result. | Invalidate the computation; do not substitute a zero residual or an apparently successful motion result. |
| G07 | Exceed the configured evaluation/solve budget, or fail to produce an acceptable feasible step. | Return or locally enforce `hold`; a last valid target is not renewed merely because computation is incomplete. |
| G08 | Replay an archived immutable snapshot with its exact configuration, program artifacts, and bindings. | Reproduce the function inputs and residual evaluations within the documented numerical tolerance; record environment differences. This does not promise identical optimizer trajectories across solvers or hardware. |

### Local command acceptance and controller integration

| ID | Scenario | Expected outcome |
|---|---|---|
| C01 | Return a fresh, valid short-horizon target for the active session, snapshot, configuration, and epoch. | The local gate accepts it only after the documented validity and robot-constraint checks. Solver success alone is insufficient. |
| C02 | Release the joystick while a solve is in flight, then deliver its `move` result. | Local hold/stop starts without waiting for the workstation; the incremented `control_epoch` causes the late result to be rejected. |
| C03 | Return a move after its originating snapshot has expired. | Reject it. A new result-generation or receive timestamp cannot extend the original validity interval. |
| C04 | Deliver duplicate or out-of-order results. | A previously accepted command is not applied again; an older result cannot overwrite a newer accepted result. |
| C05 | Interrupt the workstation or the network while a target is active. | The robot-local watchdog prevents indefinite reuse of the target and invokes the configured hold/stop behavior. |
| C06 | Request hold with usable measured feedback, then repeat with missing or invalid feedback. | The first path uses the controller's defined hold behavior; the second invokes the configured lower-level stop path instead of inventing a measured pose. |
| C07 | Resume input after ordinary joystick release, then repeat after scene invalidation. | The first may reuse an otherwise valid scene; the second remains paused until reinitialization and validation complete. |
| C08 | Submit a generated relation that would favor a pose outside host-defined motion or robot constraints. | Host constraints retain authority; generated code cannot change their limits or disable acceptance checks. |

These outcomes describe software behavior to implement. In hardware tests, observe both the issued command and the measured motion: successful message rejection does not prove that a previously queued controller target has been canceled.

## 3. Legacy evidence and reuse boundaries

The implementation baseline is [vla-grasp-server at `2390315`](https://github.com/ZenWang00/vla-grasp-server/tree/2390315278ad6f734f28a345483f3c144793a5a0). Robot-side references are pinned to [Grasp-Pose-Generation at `00a42b3`](https://github.com/ZenWang00/Grasp-Pose-Generation/tree/00a42b32ef6d82863d298ccd876d0ee9523afa5d). These references establish source behavior at those revisions, not the configuration currently running in the laboratory.

| Existing component or behavior | Evidence | Intended reuse and boundary |
|---|---|---|
| RGB-D loading, VLM grounding, segmentation, and camera-frame geometry | [Server README](https://github.com/ZenWang00/vla-grasp-server/blob/2390315278ad6f734f28a345483f3c144793a5a0/README.md), [geometry utilities](https://github.com/ZenWang00/vla-grasp-server/blob/2390315278ad6f734f28a345483f3c144793a5a0/vg_pipeline/geometry.py) | Reuse suitable perception functions and artifacts behind a new adapter. Existing detection/grasp outputs do not automatically provide the new stable feature IDs or semantic programs. |
| Discrete server-to-ROS grasp and IK round trip | [Legacy IK payload](https://github.com/ZenWang00/vla-grasp-server/blob/2390315278ad6f734f28a345483f3c144793a5a0/doc/ros2_ik_payload.md) | Reference for asynchronous jobs and diagnostics. `GraspDict` and `mode="execute"` remain legacy interfaces; they are not aliases for `ControlResult`. |
| `GET /poll_publish` timer at 0.5 seconds | [ROS grasp client](https://github.com/ZenWang00/Grasp-Pose-Generation/blob/00a42b32ef6d82863d298ccd876d0ee9523afa5d/src/grasp_pose_client/grasp_pose_client/grasp_pose_client_node.py#L281) | Useful for understanding the old task flow. This polling period is not the robot control frequency and is not adopted as the new continuous-control period. |
| RGB/depth conversion, TF access, Pinocchio helpers, and logging | [ROS grasp client](https://github.com/ZenWang00/Grasp-Pose-Generation/blob/00a42b32ef6d82863d298ccd876d0ee9523afa5d/src/grasp_pose_client/grasp_pose_client/grasp_pose_client_node.py) | Candidates for reuse after adapter tests. Their presence does not establish the new freshness, cancellation, or snapshot semantics. |

### Coordinate and timing adapter requirements

The legacy grasp convention is X=closing, Y=lateral, Z=approach. The LIO TCP convention is X=approach, Y=closing, Z=lateral. The existing `GRASP_TO_TCP_AXES` remap documents that distinction in the [client source](https://github.com/ZenWang00/Grasp-Pose-Generation/blob/00a42b32ef6d82863d298ccd876d0ee9523afa5d/src/grasp_pose_client/grasp_pose_client/grasp_pose_client_node.py#L49). New `tcp_pose` values already describe `lio_tcp_link`; an adapter must not apply that legacy remap twice. Test the three basis directions as well as positions, because coincident origins can hide an orientation error.

`LIO_base_link` and `LIO_robot_base_link` are distinct names with distinct transforms in the reference implementation. The [teleoperation executor](https://github.com/ZenWang00/Grasp-Pose-Generation/blob/00a42b32ef6d82863d298ccd876d0ee9523afa5d/src/panda-ik/cpp_src/main_teleop.cpp#L119) transforms `/commanded_pose` into `LIO_robot_base_link`. The proposed control frame defaults to `LIO_base_link`; a future executor adapter must transform to its actual model root. Do not copy a numerical offset from a code comment as a replacement for the installed robot model and calibrated TF tree.

The existing client has more than one transform path: `_handle_request_grasp` obtains `T_base_camera` at the color-image stamp, while polling/IK paths use a latest transform through their helper. The new observation contract consistently requires the capture-associated transform. With an arm-mounted camera, a delayed result transformed using the latest camera pose can displace a static object in the control frame. This requirement is a new adapter obligation; it is not a claim that every old path already implements it.

### Measured state and command units

The [LIO IK interface](https://github.com/ZenWang00/Grasp-Pose-Generation/blob/00a42b32ef6d82863d298ccd876d0ee9523afa5d/src/lio_specific_pkg_ros2/lio_specific_pkg_ros2/ik_interface_lio_ros2.py#L113) converts arm radians to **degrees** when publishing its robot command on `ik_interface/joint_states_lio`, despite using the `JointState` message type. The same file subscribes to `/lio_joint_states` as robot feedback. The [grasp client](https://github.com/ZenWang00/Grasp-Pose-Generation/blob/00a42b32ef6d82863d298ccd876d0ee9523afa5d/src/grasp_pose_client/grasp_pose_client/grasp_pose_client_node.py) also distinguishes the real-joint subscription from its generic joint-state/IK-seed input.

The new `RobotState.joint_positions_rad` must come from a verified measured-state source, with joint names and units checked. Unit conversion belongs at the adapter boundary. Neither a topic containing the words `joint_states` nor a plausible-looking pose establishes measurement provenance. A future integration test must compare independently computed FK from measured joints with the selected TCP transform and explain any discrepancy; it must not validate a commanded pose against another representation of that same command.

### Protocol compatibility

The new versioned protocol is independent of `GraspDict`. Legacy grasp scores, gripper widths, `pose_4x4`, and task polling envelopes keep their original meanings. New messages carry explicit session, scene, feature, configuration, and control identities. Any future conversion must be explicit and tested; a legacy grasp target cannot be relabeled as a fresh short-horizon control command.

This release preserves inherited runtime code and behavior. It neither adds a compatibility shim nor changes the ROS launch graph. Contact-GraspNet remains an optional inherited capability, not a mandatory dependency of the proposed shared-control optimizer. Gripper opening/closing, attached-object motion, moving objects, and mobile-base operation are outside the v1 contracts.

## 4. Measurements and decisions still required before robot integration

The architectural placement and data meanings are defined in this draft. The following deployment values require experiment or controller-specific verification; sample numbers elsewhere in the documentation are illustrative only.

| Item | Required evidence before enabling motion |
|---|---|
| Control period and solve budget | End-to-end latency distribution, solver duration, deadline misses, and behavior under CPU/GPU contention |
| Input, state, and command age limits | Measured sampling/transport delay and the maximum acceptable stale-data interval for the selected task/controller |
| Host motion limits and robot constraints | Verified model, joint/workspace limits, controller behavior, and the permitted experiment envelope |
| Hold, cancel, and lower-level stop behavior | Which installed interface cancels queued targets, whether it acknowledges completion, and measured stopping/tracking behavior |
| Calibration and capture synchronization | Camera intrinsics, RGB/depth alignment, image timing, capture-associated TF availability, and spatial error measurements |
| Numerical tolerances and objective scales | Unit-aware normalization, sensitivity to noisy features, residual tests, and recorded configuration versions |
| Generated-program execution limits | Enforced resource bounds and tested isolation for the chosen restricted executor; AST validation alone is insufficient evidence |
| Joystick mapping and assistance behavior | Verified device axes/dead zone, responsiveness to reversal/release, and task-specific assistance tuning |

There is no demonstrated 50 Hz performance, bounded network delay, certified stop time, or physical safety guarantee in this release. The local acceptance gate and watchdog are design requirements, not a substitute for the robot controller's operating procedures and protections.

## 5. Documentation release checklist

The publication review should establish all of the following and record what was actually checked:

- All prose, diagram labels, field explanations, and navigation are in English; the draft status is visible.
- Every JSON block intended as a valid example parses as strict JSON. Invalid examples identify their expected rejection reason and are not counted as valid fixtures.
- Field tables, JSON examples, formulas, and diagrams use the same names, units, versions, and result-branch semantics.
- The bottle-and-cup IDs resolve consistently through observation, geometry, semantic bindings, snapshot, weights, and result examples.
- Wiki navigation and pinned legacy links resolve; Mermaid diagrams render on the published Wiki without clipped participants or unsupported syntax.
- The repository diff contains documentation and project-description changes only; inherited runtime source is unchanged.
- The Wiki publication records the project documentation commit from which its pages were synchronized.
- The final delivery distinguishes completed documentation checks from the future acceptance scenarios above.

Related pages: [System architecture](https://github.com/ZenWang00/semantic-shared-control/wiki/System-Architecture-and-Scope) · [Data model](https://github.com/ZenWang00/semantic-shared-control/wiki/Data-Model-and-Message-Contracts) · [Generated programs](https://github.com/ZenWang00/semantic-shared-control/wiki/Generated-Constraint-Program-Contract) · [Runtime lifecycle](https://github.com/ZenWang00/semantic-shared-control/wiki/Runtime-Lifecycle-and-Timing).
