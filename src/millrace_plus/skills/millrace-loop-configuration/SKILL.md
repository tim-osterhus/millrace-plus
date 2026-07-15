---
name: millrace-loop-configuration
description: Public Millrace loop configuration guide for designing compiler-validated workflow loops as decision structures or graphs with declared outcomes and next actions. Use when an agent needs to create, revise, validate, package, or review Millrace workflow configuration, including planes, stages, queue families, terminal outcomes/actions, waits, approvals, runner bindings, assets, recovery paths, and package-selected authority.
---

# Millrace Loop Configuration

## Core Rule

Design the workflow before writing loop files or entrypoint prompts. A Millrace
loop is a compiler-validated decision structure or graph that the runtime
executes as a governed multi-step workflow. Real workflows may contain loops,
retries, waits, recovery edges, and queue-driven re-entry. If a transition,
terminal outcome, approval, wait, recovery path, package asset, or queue
handoff is not declared in selected workflow data, it is not runtime authority.

Do not put API keys, OAuth tokens, local credential paths, provider secrets, or
adapter config secrets in workflow packages, prompt assets, skill files, or
evidence reports. Adapter config is local operator config, not package
authority.

## Do Not Overclaim

- Do not state a command exists unless it appears in current CLI help or CLI
  tests.
- Do not state a config field exists unless it appears in accepted contracts or
  package/workflow schemas.
- Do not infer runtime behavior from prompt text, filenames, folders, display
  labels, CLI aliases, or runner prose.
- Do not treat examples as generic proof, planes as mandatory, LAD names as
  kernel vocabulary, or skill prose as runtime authority.

## Context Gate

Before authoring a loop, ask enough targeted questions to fill these fields:

1. What work enters the loop, and from what operator/source?
2. What artifact proves each unit of work is complete?
3. Which partitions, planes, lanes, or roles are needed, if any?
4. Which stage runs in each declared partition?
5. What does each stage own: shaping, implementation, review/check,
   reconciliation, wait packaging, operator-decision packaging, or another
   workflow-defined role?
6. What input payload does each stage receive, and should that payload be
   treated as data rather than an imperative command?
7. What decisions can each stage make?
8. What terminal markers can each stage return?
9. What happens after every terminal marker?
10. What retries, waits, approvals, quarantines, or interventions are required?
11. What runner/capability/effect boundaries apply?
12. What entrypoint prompts and core stage skills are required assets?
13. What tests or proof pack define success?

If the answers are unclear, ask the user instead of inventing missing
authority. For the full worksheet, read
`references/decision-tree-design.md`.

## Partition Model

Planes, lanes, roles, and similar partitions are workflow-owned data. They are
useful when a workflow needs different ownership lanes, concurrency limits, or
runner bindings; they are not a kernel requirement.

When a workflow uses plane-like partitions, a plane may be scheduled as a
separate agent/work lane only if the compiled workflow declares that
partition, concurrency, and runner-binding policy.

For partition and compiler boundaries, read
`references/planes-and-compilation.md`.

## Authoring Sequence

1. Draft the workflow as a decision structure in plain language. A simple
   decision tree may be useful as a drafting metaphor, but model loops,
   retries, waits, recovery edges, and queue-driven re-entry explicitly.
2. Convert each decision point into a stage, terminal marker, or runtime wait.
3. Declare queue families and payload schemas for every handoff.
4. Declare terminal outcomes/actions for every branch.
5. Declare recovery behavior for blocked or invalid work.
6. Declare runner bindings, capability policy refs, effect refs, approval
   policies, package assets, and presentation metadata.
7. Write a stage ownership matrix before entrypoint prompts: payload is data,
   and non-implementation stages must not perform downstream implementation
   work.
8. Write entrypoint prompts and core stage skills only after the graph,
   ownership matrix, and handoffs are known.
9. Compile/validate and iterate from compiler diagnostics.

For the workflow-author checklist and selected-plan field guide, read
`references/workflow-author-contract.md`.

If the active runtime schema or compiler surface is not available, stop at a
decision-tree specification and mark exact config syntax as unresolved. Do not
invent config fields that the compiler has not accepted.

## Boundary Checklist

- Workflow data owns graph shape, queue families, stage kinds, terminal
  outcomes/actions, waits, approvals, recovery paths, and presentation data.
- Package assets own prompt/skill text and examples.
- The compiler owns validation, default expansion, package asset resolution,
  and selected plan fingerprinting.
- The runtime owns executing the admitted selected plan.
- Prompt text and runner output can report decisions, but cannot make a route
  legal by themselves.
- Workflow packages, prompt assets, skills, and evidence reports must not
  contain API keys, OAuth tokens, local credential paths, provider secrets, or
  adapter config secrets.

## Reference Map

- `references/decision-tree-design.md`: read before authoring a decision
  structure or when intake context is incomplete.
- `references/planes-and-compilation.md`: read when choosing partitions,
  stage ownership, concurrency, or runner bindings.
- `references/workflow-author-contract.md`: read when reviewing authored
  package/workflow data, selected authority, validation matrices, and negative
  tests.
- `references/worked-examples.md`: read when you need concise `kernel_ping`,
  `simple_loop`, or hosted LAD examples as selected workflow data.
