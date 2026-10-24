# Issue Standards

**Development Policy v1.1 · 24 October 2026**

An execution Issue defines one independently verifiable outcome. Another agent must be able to pick it up using the Issue and its linked persistent artifacts without recovering an earlier chat. This page defines the outline to use when tasks are eventually decomposed; it does not create an initial backlog.

Use [Development Workflow](https://github.com/ZenWang00/semantic-shared-control/wiki/Development-Workflow) for decision authority and review, and [Project Board](https://github.com/ZenWang00/semantic-shared-control/wiki/Project-Board) for fields and status operations. These requirements apply equally to Issues created through the web forms, API, or CLI. Form validation is a convenience, not a server-enforced guarantee of complete task content.

## Size work for one conversation

**Default: one execution Issue fits one working conversation's context budget, including implementation, self-checks, and delivery.** Allow headroom for debugging and unexpected evidence. Independent review may happen in a separate conversation, provided it can assess the full result from the Issue, relevant documentation, diff, and evidence.

Estimate context demand from the required background, related source files, likely tool output, implementation steps, verification, and handoff. Use the actual AI's available context when known. Do not invent a universal token limit or use lines of code, file count, or elapsed time as the sole sizing rule.

Split before Ready when the work is likely to require repeated context compression, repeated loading of a large codebase, several independent questions, or several separately verifiable outcomes. Automatic summarization is a continuity aid; it is not a reason to keep an oversized execution card.

For larger work:

1. Create a parent Issue describing the overall outcome, cross-cutting acceptance criteria, and ordered stages or modules. Mark it explicitly as a tracking parent in **Execution Slice**.
2. Create linked child Issues, each delivering one independently verifiable result. Record the parent, prerequisite children, required inputs, and the output consumed by the next stage.
3. Make each child self-contained through persistent references. Avoid instructions such as “continue the previous chat” or “use the earlier result” without exact artifact references.
4. Execute children according to their dependencies. Update the parent's child checklist and integration evidence as stages finish. The parent is complete only after required children and overall integration criteria are accepted.

A stage may deliver a reviewed interface, a working implementation slice, a reproducible experiment, or an evidence-backed analysis. Do not split mechanically by file, commit, or chat count, and do not leave a child whose only deliverable is “work on” an unfinished feature. Verification required for a child's outcome remains part of that child.

If execution reveals a larger scope, save a durable handoff **before** context is exhausted. Record what changed, preserve completed work, and split the remaining scope into linked tasks. Keep unmet criteria visible. Ending a conversation or delegating the rest never constitutes Done.

## Required outline

All task types use the following eight sections. Backlog items may explicitly mark unknowns; Ready items must contain enough information for execution and acceptance. Delivery records are completed as work progresses, but their expected artifacts must be specified before Ready.

| Section | Required content |
| --- | --- |
| **Outcome** | The observable result and why it matters. Use a specific result rather than “improve,” “optimize,” or “finish” without a measurable meaning. |
| **Scope and Non-goals** | Included behavior or artifacts and the boundaries that prevent scope expansion. |
| **Context and References** | Relevant design pages, interfaces, source entrypoints, related Issues, and durable decision references. Link only the context needed for this slice. |
| **Execution Slice** | Executable child, standalone task, or tracking parent; its stage or module; why the execution scope fits one conversation; expected inputs and outputs at stage boundaries. Split oversized execution work before Ready. |
| **Dependencies and Inputs** | Prerequisite Issues, data, configuration, environment, and external dependencies, including availability. Write `None` where applicable. |
| **Acceptance Criteria** | A checklist of observable conditions by which the result will be accepted, including relevant failure behavior. Each condition must admit a concrete judgment. |
| **Verification Plan** | Procedures or checks, required environment, relevant cases, and evidence to retain. Distinguish static inspection, simulation, replay, and hardware verification. |
| **Delivery and Handoff** | Planned deliverables initially; later the actual PR or artifact links, revision, verification evidence, limitations, unresolved work, and resumption instructions if needed. |

Use the real GitHub owner in Assignees and the defined project Priority field. Record the executor or agent and working branch in a claim or handoff comment. Do not invent an AI GitHub account. Label the Issue with exactly one task type and add `review:human` when its acceptance requires the owner's confirmation.

## Repository and cross-repository context

These rules apply to both project repositories. Create the Issue where its main deliverable is implemented, following [Repository Ownership](https://github.com/ZenWang00/semantic-shared-control/wiki/Repository-Ownership-and-Cross-Repository-Workflow). Keep project-level Epics and shared design in the server repository; link native sub-issues and blockers across repositories. Use full URLs or `owner/repository#N` for cross-repository references.

In **Scope and Non-goals**, identify the owning repository and affected consumers. In **Dependencies and Inputs**, identify required companion revisions/interfaces. In **Delivery and Handoff**, record related PRs, both tested revisions and compatibility evidence when integration spans repositories. Transfer an existing card rather than duplicating it, preserving acceptance, ownership and board fields; verify parent and dependency relations after transfer.

## Task types

### Engineering Task

Use `type:engineering` for features, refactoring, developer infrastructure, and substantive documentation. Describe the expected behavior and any affected interface in the relevant outline sections. Explain the current and intended behavior when that distinction matters.

Verification must target meaningful behavior and relevant failure cases. Documentation can be accepted through content, link, rendering, or configuration checks as appropriate; no new code test is required simply to satisfy the template.

### Bug Report

Use `type:bug`. Include actual behavior, expected behavior, reproducible steps or a minimal example, affected revision, and environment. Reference logs or data without including secrets. Put the observable failure in **Outcome** or **Context and References**, and the regression check in **Verification Plan**.

If reproduction is unknown, keep the report in Backlog or explicitly scope an executable diagnostic Issue. A diagnostic task can finish with a justified finding and evidence; it must not claim the underlying bug is fixed. Fix acceptance must demonstrate the corrected behavior and check relevant regressions.

### Research Task

Use `type:research`. State the research question and whether the work is exploratory or confirmatory. Specify the data and scenario, baseline or comparison method, metrics, and analysis procedure before execution. If a baseline is inapplicable, explain the alternative comparison or evidence standard.

Deliver code and data references, configurations, experiment conditions, results, analysis, and limitations sufficient to reproduce the work. Record changes to the protocol and their reasons; analyses introduced after seeing results must be identified as exploratory. Do not silently choose successful runs or change acceptance criteria to match an outcome.

A negative result or a hypothesis that is not supported can meet acceptance criteria when the experiment is credible and answers its stated question. Separate “the experiment ran” from “the evidence supports this claim.” Claims intended for the thesis or external communication require the owner's confirmation under [Development Workflow](https://github.com/ZenWang00/semantic-shared-control/wiki/Development-Workflow).

## Ready and Done checks

Before moving an execution Issue to **Ready**, confirm:

- The eight sections are actionable; unknowns that block execution have been resolved.
- The scope fits one working conversation with debugging and handoff headroom.
- Prerequisites and required inputs are available, and stage boundaries are explicit where applicable.
- Acceptance criteria and verification are concrete and appropriate to the task type.
- A real owner, priority, and review path are recorded, including human confirmation where required.

Before moving it to **Done**, confirm:

- Every criterion is supported by delivery and verification evidence, with limitations recorded.
- Independent review covers the final result, and required human confirmation is recorded.
- Repository changes are merged into `main`; other required artifacts and publication are available.
- Remaining work is clearly recorded and does not contradict the accepted scope. A tracking parent also satisfies its overall integration criteria.
- The Issue and board have been explicitly synchronized after acceptance.

Record all scope or acceptance changes with their reason and the disposition of removed work. Apply the normal authority rules to design or research changes. Neither an executor's context limit nor a failed check permits silently weakening the task. Cancellation and duplicates use the board's `not planned` closure process rather than Done.
