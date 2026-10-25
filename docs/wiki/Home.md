# Semantic Shared Control — Project Documentation

**Design Draft v0.1 · 24 October 2026 · Documentation only**

The technical documentation specifies a proposed data contract for continuous, joystick-guided robotic assistance. It is intended for supervisors reviewing the research design and engineers implementing the perception, fusion, optimization, and robot interfaces.

The central research question is how task semantics and scene geometry can assist a user's ongoing two-dimensional command while preserving direct influence over each next motion step. A VLM supplies task relevance and generated geometric relation programs. A separate online computation combines those results with current joystick input and measured robot state.

The technical pages are a design specification, not a report of an implemented or experimentally validated shared-control system. In those pages, **MUST** and **MUST NOT** identify requirements of the proposed contract; they do not claim that inherited code already enforces those requirements. **SHOULD** identifies a design recommendation with an explanation required for deviation.

## Working on the project

**Development Policy v1.1** defines the active contribution process, separately from the technical design draft. Read these pages before creating or executing tasks:

| Page | Purpose |
| --- | --- |
| [Development Workflow](https://github.com/ZenWang00/semantic-shared-control/wiki/Development-Workflow) | Follow ownership, tiered AI autonomy, implementation, independent review, acceptance, and handoff. |
| [Issue Standards](https://github.com/ZenWang00/semantic-shared-control/wiki/Issue-Standards) | Define complete task cards sized to one working conversation, with staged decomposition and verifiable outcomes. |
| [Project Board](https://github.com/ZenWang00/semantic-shared-control/wiki/Project-Board) | Use board states, priorities, labels, work-in-progress limits, and completion rules. |
| [Repository Ownership and Cross-Repository Workflow](https://github.com/ZenWang00/semantic-shared-control/wiki/Repository-Ownership-and-Cross-Repository-Workflow) | Place work in the server or ROS2 repository and verify their shared interfaces and version pairs. |

Use the [shared development board](https://github.com/users/ZenWang00/projects/3) for both repositories and the [phase-one Epic](https://github.com/ZenWang00/semantic-shared-control/issues/2) for its current execution cards. Create tasks in the repository that owns their deliverables. The server repository owns shared contracts, algorithms and offline archival/replay; the ROS2 repository owns device adapters, coordination, final motion authority and the complete offline loop runner. Repository ownership does not change the initial deployment locations.

## Technical reading order

| Page | Purpose |
| --- | --- |
| [System Architecture and Scope](https://github.com/ZenWang00/semantic-shared-control/wiki/System-Architecture-and-Scope) | Understand module responsibilities, deployment, and the first-prototype boundary. |
| [Data Model and Message Contracts](https://github.com/ZenWang00/semantic-shared-control/wiki/Data-Model-and-Message-Contracts) | Implement compatible producers and consumers using field definitions and examples. |
| [Generated Constraint Program Contract](https://github.com/ZenWang00/semantic-shared-control/wiki/Generated-Constraint-Program-Contract) | Interpret generated numerical functions, bindings, normalization, and validation. |
| [Runtime Lifecycle and Timing](https://github.com/ZenWang00/semantic-shared-control/wiki/Runtime-Lifecycle-and-Timing) | Follow initialization, control cycles, invalidation, and command acceptance. |
| [Validation and Legacy Integration](https://github.com/ZenWang00/semantic-shared-control/wiki/Validation-and-Legacy-Integration) | Review expected acceptance cases and inherited-interface pitfalls. |
| [Offline Reference Profile](https://github.com/ZenWang00/semantic-shared-control/wiki/Offline-Reference-Profile) | Implement the selected Phase 1 CPU baseline, synthetic model, limits and worked design oracles. |

## First-prototype assumptions

- The robot base and objects remain fixed relative to the control frame during a valid scene episode. A moved object invalidates that episode until reinitialization; automatic change detection is not claimed.
- An initial aligned RGB-D observation provides scene geometry. The camera may be mounted on the robot, so the capture-time camera transform must be preserved.
- The user's normalized XY command refers to the fixed base plane. Its magnitude controls requested translational speed. Assistance may affect height and orientation, subject to host motion constraints.
- The robot state and joystick remain live even when scene geometry is cached.
- The VLM may generate new straight-line numerical formulas through a restricted Python-like function contract. It does not issue robot commands or change host control limits.
- The first task is approach and pose alignment. Gripper actuation, transport of a grasped object, mobile-base motion, and autonomous task stages are out of scope.
- Invalid semantic or geometric information pauses assistance and requires reinitialization. Normal joystick release stops motion without unnecessarily discarding valid scene information.

Dynamic scene tracking is an extension point. Versioned feature identities are included now, but that does not imply a tracker, prediction model, or attached-object model has been implemented.

## Terminology

| Term | Meaning |
| --- | --- |
| TCP | Tool center point whose pose is requested and measured. |
| Control frame | Fixed coordinate frame in which geometry and candidate TCP poses are evaluated. |
| Observation | A captured RGB-D sample, calibration identity, and capture-time transform. |
| Geometry | Objects, keypoints, and axes grounded in an observation. |
| Semantic context | Task relevance scores and references to generated relation programs. |
| Residual | A scalar `g`; `g <= 0` means a generated relation is satisfied. |
| Snapshot | Immutable inputs and exact artifact versions for one computation cycle. |
| Active bundle | A local, atomically activated set of compatible geometry, semantics, programs, validation receipts, and configuration. |
| Control epoch | A cancellation generation used to reject results from before a stop or invalidation. |

## Provenance and document maintenance

The project preserves the Git history of [vla-grasp-server at 2390315](https://github.com/ZenWang00/vla-grasp-server/tree/2390315278ad6f734f28a345483f3c144793a5a0). Robot integration observations refer to [Grasp-Pose-Generation at 00a42b3](https://github.com/ZenWang00/Grasp-Pose-Generation/tree/00a42b32ef6d82863d298ccd876d0ee9523afa5d), the target of its annotated [`v1.0.0` Git tag](https://github.com/ZenWang00/Grasp-Pose-Generation/tree/v1.0.0). These pinned sources distinguish inspected code from later repository changes; they do not establish a validated shared-control version pair.

The single authoritative authoring source for both repositories' shared policy and contracts is [`semantic-shared-control/docs/wiki/`](https://github.com/ZenWang00/semantic-shared-control/tree/main/docs/wiki). The separate Wiki repository publishes the reviewed source files; ROS2 documentation links here. Readers should treat Design Draft v0.1 as a coherent set; updates to shared fields or behavior must update examples and diagrams together. The [cross-repository workflow](https://github.com/ZenWang00/semantic-shared-control/wiki/Repository-Ownership-and-Cross-Repository-Workflow) defines dependency pins and integration evidence.

The Offline Reference Profile selects synthetic solver, timing and numerical settings for Phase 1. Implementations must still verify resource limits and runtime behavior; device calibration and real robot-side stopping require separate validation. Numerical design checks do not establish control performance.
