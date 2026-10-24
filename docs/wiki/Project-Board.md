# Project Board

**Development Policy v1.0 · 24 October 2026**

This page defines the task tracking configuration and operating rules. It does not change the technical contracts or claim that the proposed control system has been implemented. Read it with [Issue Standards](https://github.com/ZenWang00/semantic-shared-control/wiki/Issue-Standards) and [Development Workflow](https://github.com/ZenWang00/semantic-shared-control/wiki/Development-Workflow).

## Sources of truth

The repository's Issues hold task scope, acceptance criteria, dependencies, and delivery evidence. The Project presents their status and priority. Pull requests hold implementation differences and review evidence. The version-controlled Wiki source holds stable policies and technical decisions; the published Wiki is its synchronized copy. Do not maintain a second task list in chat or another document.

The public Project is [**Semantic Shared Control — Development**](https://github.com/users/ZenWang00/projects/3), owned by `ZenWang00` and linked to `ZenWang00/semantic-shared-control`. Public issues, board fields, and comments must not contain credentials or private experimental data; use accessible, appropriately permissioned evidence references instead.

## Views and fields

| Setting | Value and meaning |
| --- | --- |
| Default view | **Board**, grouped by Status in the order below |
| Secondary view | **All Tasks**, a table with Title, Status, Priority, Assignees, and Labels |
| Card source | Repository Issues only; link PRs to their issue rather than adding duplicate PR cards |
| Status | Backlog, Ready, In progress, Review, Done |
| Priority | P0: urgent blocker; P1: next focus; P2: normal backlog work |
| Assignees | A real GitHub account accountable for integration; the execution agent and branch are recorded in the claim/handoff comment |

Priority can be unset in Backlog and must be explicit before Ready. Do not default every new idea to P0 or invent dates, estimates, or milestones. The maintainer determines priorities; an executing agent may propose changes with reasons.

Use exactly one task type label: `type:engineering`, `type:bug`, or `type:research`. Engineering includes documentation, refactoring, and development infrastructure. `blocked` indicates an obstacle with a recorded cause and unblock condition. `review:human` indicates a required maintainer decision under the Development Workflow; keep it until that decision is recorded, then remove it if no further human decision is required. Preserve unrelated repository labels.

## State transitions

| Status | Entry condition | Normal next step |
| --- | --- | --- |
| Backlog | A proposed task; unresolved details are allowed and identified | Refine and check the Definition of Ready |
| Ready | Scope, conversation context fit, dependencies, owner, priority, review route, acceptance, and verification are clear | Claim the task and record its branch/execution scope |
| In progress | An execution owner is actively working on the agreed slice | Submit deliverables and evidence for Review |
| Review | Deliverables, verification results, and limitations are available | Obtain independent review and any required human decision |
| Done | All acceptance criteria and applicable reviews are satisfied; repository changes are integrated into main | Close the issue as completed and retain the evidence |

The execution owner maintains status at claim, review submission, completion, and handoff. A request for changes returns the task to In progress when work resumes. A waiting human decision remains in Review. A task that loses its Ready conditions returns to Backlog with the reason documented.

The work-in-progress limit is **two cards total across In progress and Review**, including blocked cards. This is an operating rule, not a claim that GitHub enforces a hard limit. Multiple agents can serve one card within agreed modification boundaries. To pause a blocked task and free capacity, record its current state and next action, move it to Backlog, and retain the blocker information. Do not increase the limit simply to hide unfinished review work.

## Parent tasks and phased execution

A parent issue records a larger outcome, links its child issues, and specifies stage dependencies and aggregate acceptance. Child issues are independently executable, conversation-sized results as defined in Issue Standards. Only executable slices are claimed for implementation. Keep a tracking parent in Backlog while children execute; move it to Review for aggregate acceptance after the required child results are available. A parent in Review counts toward the same work-in-progress limit.

A parent is not Done merely because all children are closed: canceled children do not satisfy the original outcome. Confirm aggregate acceptance or explicitly agree and record a revised scope. Do not create empty placeholder child issues to make a plan appear complete.

## Completion, cancellation, and reopening

After acceptance, the execution owner posts a completion record with evidence and review references, closes the issue with reason `completed`, and sets its Project status to Done. Do these steps as one completion operation and verify both systems. If any update fails, report which update remains and reconcile it before claiming completion.

Use ordinary references such as `Refs #123` in PR descriptions rather than automatic issue-closing keywords. A merged implementation PR must not silently close a research task or bypass an outstanding human decision.

Cancel abandoned or duplicate work with reason `not planned`, record why and link any replacement, and archive its Project item without marking it Done. For a previously completed issue that is reopened, unarchive it if necessary and return it to Backlog for a fresh Ready check. Archiving preserves the issue and its evidence.

## Automation configuration

| Workflow | Required configuration |
| --- | --- |
| Auto-add to project | Select this repository; filter `is:issue is:open` |
| Item added to project | Set Status to Backlog |
| Item closed | Disabled: closing is not sufficient evidence of acceptance |
| Pull request merged | Disabled: PRs are not separate execution cards |
| Pull request linked to issue | Disabled: linking a PR does not establish that a task is ready or claimed |
| Auto-add sub-issues | Disabled: the repository-filtered auto-add rule handles children without importing unrelated repository work |
| Code changes requested / Code review approved | Disabled: the execution owner applies the full review and acceptance rules |
| Item reopened | Disabled; the execution owner explicitly resets status and checks readiness |
| Auto-close issue from status | Disabled; completion is explicitly verified in both systems |
| Auto-archive | Disabled initially; cancel/archive deliberately and retain completed work for inspection |

Auto-add handles future matching issue creation or updates; it is not a historical backfill guarantee. If a new card is absent, the execution owner adds its existing Issue URL manually and sets Backlog without creating another issue. CLI/API-created issues follow the same content and label rules as browser forms. Forms and automation are aids, not server-side enforcement of every policy.

GitHub's default workflows can mark closed items and merged PRs Done; disable them during initialization. See the official [built-in automation](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations) and [auto-add](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically) documentation.

## Initialization and maintenance

Initialize an empty board and the three Issue Forms. Do not create development tasks, example cards, or milestones during this governance bootstrap. The bootstrap configuration PR is a one-time exception to the issue requirement, authorized by the maintainer's approved implementation plan.

Maintain policy and template changes through reviewed repository PRs. Publish the same Markdown files to the Wiki only after their source is integrated, recording the source commit in the publication commit. Check the public board link, views, fields, labels, and saved workflows when configuration changes. Verify templates through their form previews without submitting test issues.

Separate **configuration verified** from **automation exercised by a real task**. The empty-board bootstrap does not demonstrate end-to-end event handling. During the first real task, verify auto-add and the initial Backlog status, then record any needed correction.

### Initialization record

Configuration checked on 24 October 2026:

- Public [Project #3](https://github.com/users/ZenWang00/projects/3), linked only to `ZenWang00/semantic-shared-control`, with zero items at initialization.
- Board and All Tasks views saved; the five Status options and P0/P1/P2 Priority field are present.
- The three task type labels, `blocked`, and `review:human` exist; existing repository labels are preserved.
- Only Auto-add to project and Item added to project are enabled. Auto-add selects this repository with `is:issue is:open`; Item added sets Backlog. Other automatic status/closure paths are disabled.
- Three English forms and the PR template are checked for structure and policy consistency. Browser forms are previewed after integration without submitting test issues.

These are configuration checks. Auto-add and initial status have not been exercised by a real task because the bootstrap intentionally creates none. The implementation delivery identifies the source PR and Wiki publication commit; the Wiki commit records the exact source revision. No runtime, robot, or experimental validation is implied by these management checks.
