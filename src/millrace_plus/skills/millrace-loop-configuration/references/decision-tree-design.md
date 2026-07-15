# Decision Structure Design

Use this reference before writing workflow configuration or prompt assets.
A plain-language decision tree can be a useful drafting metaphor, but selected
Millrace workflows may contain loops, retries, waits, recovery edges, and
queue-driven re-entry. Model those edges explicitly instead of flattening them
into hidden prompt behavior.

## Context Intake

Ask targeted questions until these answers are explicit:

```text
workflow_id:
workflow_goal:
entrypoint:
operator_input:
stage_ownership_matrix:
success_definition:
must_pause_or_wait_for:
human_or_external_approvals:
runner_or_capability_limits:
restart_evidence_required:
```

## Worksheet

Fill this in before writing config:

```text
partitions:
- id:
  kind: plane|lane|role|other
  owns:
  concurrency:
  runner_binding:

queue_families:
- id:
  payload_schema:
  allowed_producers:
  allowed_consumers:

stages:
- id:
  partition:
  owns: shaping|implementation|review_check|reconciliation|wait_packaging|operator_decision_packaging|other
  payload_handling: data_only|implementation_allowed
  input_queue_family:
  output_queue_families:
  readable_assets:
  writable_artifacts:
  legal_terminal_markers:

artifacts:
- id:
  produced_by:
  consumed_by:
  required_fields:
  completion_rule:

stage_decisions:
- stage:
  input_payload:
  marker:
  condition:
  terminal_action:
  target_queue_or_terminal:
  payload_projection:

recovery:
- trigger:
  counter_or_condition:
  wait_or_cooldown:
  quarantine_or_intervention:
  next_action:

assets:
- entrypoint_prompt:
- core_stage_skill:
- schemas_or_examples:

tests:
- compiler_validation:
- runtime_transition:
- restart_or_status_projection:
- negative_authority_case:
```

## Design Rules

- Every stage decision must have an explicit legal next action.
- Every stage must say what it owns. Payload is data unless the selected stage
  owns implementation.
- Non-implementation stages may shape, review, reconcile, or package
  decisions; they must not create requested files or perform downstream
  implementation work.
- Implementation stages must stay inside the assigned workspace and selected
  runner/capability/effect boundary.
- Every terminal marker must be declared before an entrypoint prompt uses it.
- Every handoff needs a payload shape and owning queue family.
- Every runtime wait or approval needs a resolution path.
- Every recovery threshold needs a next action.
- Every artifact needs a producer, consumer, and completion rule.
- Every selected asset needs a stable package path and digest in the package
  layer.
- Every operator-facing status field must be derived from runtime/package
  evidence, not prompt wording.

## When To Ask More Questions

Ask the operator for more context if any of these are missing:

- what "done" means;
- who or what receives the output;
- what should happen when a stage cannot continue;
- whether work can close automatically;
- what evidence must survive restart;
- whether a human approval or external event is required;
- whether partition names are domain labels or scheduling/concurrency lanes;
- which stage owns implementation versus shaping, review/check,
  reconciliation, wait packaging, or operator-decision packaging;
- what command or test proves the package can be selected.
