# Authoring Workflows

A Millrace workflow should begin as a fully specified decision tree, not as a
collection of prompts.

Before writing package files, identify:

- what enters the workflow;
- which decisions must be made;
- which decisions require an agent and which are deterministic;
- what each stage receives and produces;
- every legal outcome and route;
- retry, recovery, wait, quarantine, and intervention behavior;
- the exact condition that closes the work.

If any branch still ends in “the agent decides what happens next,” the workflow
is not fully specified enough to compile into governed runtime behavior.

## Planes And Stages

A plane groups stages that share one domain of responsibility. A workflow may
have one plane, several planes, or stages such as a cross-plane troubleshooter
that do not belong to a domain plane.

Use planes to clarify ownership and concurrency, not to imitate LAD. Their
names are workflow data. Management, Implementation, Review, Requirements,
Sourcing, Evaluation, and Authorization are all ordinary examples.

Each stage should have one bounded responsibility. Define:

- its input queues;
- its output queues;
- selected runner binding;
- artifact schemas it may produce;
- legal terminal markers;
- the runtime action selected for each marker;
- selected prompt and skill assets.

## Entrypoint And Core Skill

Agent stages use a two-layer instruction pattern.

### Entrypoint prompt

The entrypoint is the stage's operating prompt. It should explain:

- the role and scope of this stage;
- the dispatch evidence it must read;
- the work process to follow;
- what it must not decide;
- the legal terminal markers and when each applies;
- when to stop instead of guessing.

Millrace tells the runner to open the selected entrypoint and follow its
instructions. Write it as a complete prompt, not as notes about a prompt.

### Stage-core skill

The paired core skill defines the exact handoff contract:

- required artifact fields and types;
- valid and invalid JSON examples;
- how upstream context maps into the artifact;
- validation and evidence requirements;
- completion criteria.

Keep structured artifact details here rather than duplicating them across the
entrypoint prompt. The entrypoint explains the job; the core skill explains the
format in which the job must be handed back.

## Runtime Authority

Prompt and skill text instruct the model. The workflow definition owns:

- legal routes;
- queue creation and closure;
- retries and attempt limits;
- waits and operator choices;
- fanout and join behavior;
- completion and quarantine behavior;
- capabilities and runner bindings.

Do not encode those rules only in prose. If the runtime must enforce a rule,
declare it in workflow data and make the prompt explain the already-declared
contract.

## Package Assets

Every selected asset needs a stable asset ID, package-relative path, byte
length, and content digest in `millrace_workflow_package/manifest.json`.

The manifest is currently maintained as committed canonical JSON. Follow
[Manifest maintenance](manifest-authoring-policy.md) whenever assets or
selected workflow authority change.

## Validate In Layers

1. Validate manifest shape and asset digests.
2. Compile every selectable workflow and entrypoint.
3. Exercise each legal terminal marker and route offline.
4. Test refusal cases for invalid markers, schemas, and stale identity.
5. Run focused actual-model flows with fixed input payloads.
6. Review prompt or schema failures as asset defects before weakening runtime
   validation.

The included `millrace-loop-configuration` and
`millrace-entrypoint-authoring` skills provide a question-driven design process
and more detailed authoring checklists.
