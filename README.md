# Semantic Shared Control

**Status: design and documentation phase — Design Draft v0.1.**

This project investigates continuous shared control of a robot end effector from a two-dimensional joystick, task semantics from a vision-language model (VLM), and geometric information represented by keypoints and axes. The proposed optimizer combines these inputs with measured robot state to propose the next short-horizon TCP pose.

The first prototype is scoped to a static scene and a fixed robot base. The operator controls motion in the base XY plane; geometric assistance can influence height and orientation. Gripper actuation, attached-object motion, and autonomous multi-stage tasks are outside this first contract.

## Documentation

- [Technical documentation on GitHub Wiki](https://github.com/ZenWang00/semantic-shared-control/wiki)
- [Version-controlled documentation source](docs/wiki/Home.md)
- [Architecture](docs/wiki/System-Architecture-and-Scope.md)
- [Data model and message contracts](docs/wiki/Data-Model-and-Message-Contracts.md)
- [Generated constraint program contract](docs/wiki/Generated-Constraint-Program-Contract.md)
- [Runtime lifecycle and timing](docs/wiki/Runtime-Lifecycle-and-Timing.md)
- [Validation and legacy integration](docs/wiki/Validation-and-Legacy-Integration.md)

The Wiki is a publication copy of `docs/wiki/`. Change the source on a project branch, review it, and synchronize the same Markdown files into the separate Wiki Git repository. The Wiki publication commit records the source project commit.

## Development policy

**Development Policy v1.0** governs human and AI contributions independently of the technical design draft:

- [Development workflow](docs/wiki/Development-Workflow.md)
- [Issue standards and conversation-sized execution](docs/wiki/Issue-Standards.md)
- [Project board and tracking rules](docs/wiki/Project-Board.md)
- [Live development board](https://github.com/users/ZenWang00/projects/3)
- [Create an issue using a task template](https://github.com/ZenWang00/semantic-shared-control/issues/new/choose)

Execution issues should fit one working conversation's context, including implementation, verification, and handoff. Split larger outcomes into independently verifiable stages before starting. Repository agents begin with [AGENTS.md](AGENTS.md).

## Implementation baseline

Git history and existing source files are inherited from [ZenWang00/vla-grasp-server](https://github.com/ZenWang00/vla-grasp-server) at commit [`2390315`](https://github.com/ZenWang00/vla-grasp-server/tree/2390315278ad6f734f28a345483f3c144793a5a0). The upstream remote identifies that source repository.

The inherited code implements the earlier VLM/SAM2/Contact-GraspNet grasp service. It **does not implement the new shared-control contracts**. Its endpoints, dependency files, notebooks, reports, and launch scripts are retained as a development baseline; no new runtime behavior is introduced by this documentation revision. For the original operating instructions, see the [baseline README](https://github.com/ZenWang00/vla-grasp-server/blob/2390315278ad6f734f28a345483f3c144793a5a0/README.md).

The companion robot repository is [ZenWang00/Grasp-Pose-Generation](https://github.com/ZenWang00/Grasp-Pose-Generation), inspected at [`00a42b3`](https://github.com/ZenWang00/Grasp-Pose-Generation/tree/00a42b32ef6d82863d298ccd876d0ee9523afa5d). Its adapters are reference material, not a verified implementation of the proposed stop or timing behavior.

No control frequency, network deadline, safety property, or robot performance result is claimed by this design draft.
