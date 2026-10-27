# Foreground workspace

Foreground owns the exact request, target copy, LAD state, role results, accepted artifacts, and recovery evidence. Keep edits under the packet’s assigned target paths. Preserve unrelated dirty and ignored work. Do not edit session state, baselines, guidance, or accepted artifacts by hand.


In explicit shared mode, the immutable project `lad-target.json` selects the
external target. The prepared occurrence and assignment pin its root and digest.
Control files and artifacts remain task-local. No shared record means the
standalone contained-target contract applies. An invalid shared record refuses.
