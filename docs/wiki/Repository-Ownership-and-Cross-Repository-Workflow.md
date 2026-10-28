# Repository Ownership and Cross-Repository Workflow

The project uses two code repositories, one authoritative Wiki, and one development Project. This page records the approved source-ownership boundary tracked in [semantic-shared-control #19](https://github.com/ZenWang00/semantic-shared-control/issues/19). It does not change the proposed message contracts, coordinate conventions, deployment locations, or robot-side motion authority.

Read [Development Workflow](https://github.com/ZenWang00/semantic-shared-control/wiki/Development-Workflow), [Issue Standards](https://github.com/ZenWang00/semantic-shared-control/wiki/Issue-Standards), and [Project Board](https://github.com/ZenWang00/semantic-shared-control/wiki/Project-Board) for contribution and acceptance rules. The technical design remains Design Draft v0.1; repository ownership is not evidence that its components have been implemented.

## Source ownership and dependency direction

“Server repository” means [ZenWang00/semantic-shared-control](https://github.com/ZenWang00/semantic-shared-control); “ROS2 repository” means [ZenWang00/Grasp-Pose-Generation](https://github.com/ZenWang00/Grasp-Pose-Generation). These names identify where code is maintained, not which computer runs it or whether a component requires ROS2 at runtime.

| Responsibility | Owning repository | Boundary |
| --- | --- | --- |
| Perception and semantic preparation | Server | Maintain scene-processing and VLM integrations without granting them command authority. |
| Shared records, validators, immutable artifact registration and reference resolution | Server | Supply versioned, transport-independent contracts and reusable pure-library code. |
| Candidate validation, intent selection and bounded numerical solving | Server | Validate data-only candidates, select one target and evaluate a fixed position objective in a supervised solver; validated data alone does not activate a robot context. |
| Intent fusion and short-horizon optimization | Server | Implement algorithms that consume immutable inputs and propose results, without owning the live control lifecycle. |
| Artifact archival, replay and offline analysis | Server | Preserve and inspect evidence without applying archived commands to a controller. |
| Device, capture, measured-state, controller and transport adapters | ROS2 | Own device provenance, coordinate/unit conversion and actual controller interfaces. |
| Active-bundle activation, snapshot assembly and dispatch timing | ROS2 | Own current context, local clocks, dispatch ordering and immutable cycle construction. |
| Final command acceptance, lifecycle, release, watchdog and recovery | ROS2 | Retain final robot-side motion authority and independent checks of host/robot constraints. |
| Synthetic controller/plant and complete offline loop runner | ROS2 | Exercise the same coordination boundaries with synthetic feedback. An offline Python runner remains robot-coordination code even when no ROS graph or hardware is required. |

The ROS2 repository consumes a versioned pure library from the server repository. The shared library must be usable without importing ROS2, device drivers, or the inherited GPU service stack. The server repository must not import the ROS2 repository in return. Robot-specific model and constraint implementations stay in ROS2 and are supplied through a transport-independent host-constraint interface defined with the shared algorithms. The ROS2-owned offline integration supplies the profile's synthetic implementation through that boundary; shared-library unit tests use local test doubles without importing ROS2 code. These are implementation requirements, not claims that a package or interface has already been delivered.

The optimizer can use the injected constraint interface to find a feasible candidate. The robot-side gate must still perform its own final checks using current controller state, local authority, timing, and host constraints; the optimizer's success cannot bypass that gate. Dependency injection does not transfer control authority to the shared library.

Code ownership leaves [deployment](https://github.com/ZenWang00/semantic-shared-control/wiki/System-Architecture-and-Scope) unchanged: the initial fusion and optimizer execution remains on the robot computer, while expensive perception/semantic preparation can run on the workstation. Installing a server-owned library on the robot does not require remote optimization. Moving computation later still requires the timing and failure evidence specified by the runtime contract.

## One Wiki and one Project

The authoring source for shared policy and technical contracts is [`semantic-shared-control/docs/wiki/`](https://github.com/ZenWang00/semantic-shared-control/tree/main/docs/wiki). Its reviewed files are published to the [Semantic Shared Control Wiki](https://github.com/ZenWang00/semantic-shared-control/wiki). The ROS2 repository links to those pages from its contributor entry point and local documentation; it must not maintain a competing copy of shared policy or contract definitions. Repository-specific setup, launch and adapter documentation remain alongside the code they explain.

[Project #3](https://github.com/users/ZenWang00/projects/3) tracks work in both repositories. Its work-in-progress limit applies to their combined In progress and Review cards. The [phase-one Epic](https://github.com/ZenWang00/semantic-shared-control/issues/2) remains in the server repository and owns the child checklist and aggregate evidence. This page defines responsibility, not a second backlog. Follow the Epic and current Issue links for execution status and dependencies.

Create or transfer an executable Issue to the repository that owns its deliverable. A coordinated change with independently verifiable deliverables in both repositories uses linked execution Issues and PRs under the same parent. Use repository-qualified references or full Issue URLs for cross-repository dependencies, and record the exact prerequisite PR/revision rather than relying on coincident issue numbers. Preserve scope, review, evidence and parent relationships when transferring an Issue; update links and verify the existing Project item instead of creating a duplicate card.

## Coordinated changes and acceptance

1. Identify the owning repository and the affected shared interface before Ready. Record the other repository's required input/output, compatible dependency revision and verification procedure in the Issue.
2. Change shared contracts and reusable interfaces in the server repository first. Keep examples and consumers consistent, and obtain the owner's confirmation for changes to core contracts, frames/units, control authority or stop/failure semantics under the Development Workflow. An ownership-only edit does not itself authorize those changes.
3. Review each final diff independently. Integrate a required producer/interface change before declaring its consumer Ready; consumer preparation can proceed separately while the dependency remains explicit.
4. Pin the consumer to the accepted server library version or exact source revision. Verify the resulting server/ROS2 version pair with relevant component and integration scenarios, retaining the configuration and evidence described below.
5. Accept each Issue only when its own criteria are met, then synchronize its state and Project fields explicitly. Cross-repository integration or parent completion also requires the agreed aggregate evidence; one merged PR does not establish that the combined system works.

PRs use ordinary references such as `Refs ZenWang00/semantic-shared-control#19`, not automatic-closing keywords. A handoff records both relevant repository revisions, pending PRs and the next reproducible check. Publish shared Wiki changes only after their reviewed server source has merged, recording that source commit in the Wiki publication.

## Version pairs, configuration and evidence

The inspected ROS2 baseline is the annotated Git tag [`v1.0.0`](https://github.com/ZenWang00/Grasp-Pose-Generation/tree/v1.0.0), whose tag object is `06eec60d8682d18b58d5e8a56d12a38e05afa962` and whose target commit is [`00a42b32ef6d82863d298ccd876d0ee9523afa5d`](https://github.com/ZenWang00/Grasp-Pose-Generation/tree/00a42b32ef6d82863d298ccd876d0ee9523afa5d). The repository had no published GitHub Release when checked for this ownership update. A Git tag and a GitHub Release are different artifacts; this baseline is not a validated shared-control release or a tested cross-repository version pair.

For future dependency updates and integration runs, preserve:

- The server library version and exact source commit, ROS2 tag/version and exact source commit, dependency lock or installation manifest, and relevant runtime/platform versions.
- The shared message schema version, candidate-point API version, validator policy version, and immutable configuration/profile IDs. Contract versions and package versions remain distinct; this governance change does not advance either contract.
- The task/scene/artifact references, model/calibration identities, host-constraint configuration, timing budgets, numerical tolerances and simulation/hardware provenance required to reconstruct the run.
- Commands and scenarios, expected/actual outcomes, acceptance decisions, controller observations when available, archive/checksum references, independent review and any outstanding compatibility limits.

Record which version pair was tested and reject unsupported or mismatched contracts rather than silently adapting them. State whether evidence covers static checks, simulation, replay or hardware. Phase-one offline evidence retains the existing limits: P05's capture-time TF adapter behavior awaits later validation, and P08 covers synthetic feedback provenance only. A successful library test or a new tag does not supply missing robot integration evidence.
