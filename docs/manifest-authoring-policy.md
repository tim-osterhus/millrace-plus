# Manifest Authoring Policy

Selected policy: frozen manifest.

`millrace_workflow_package/manifest.json` is the committed source of truth for
the official package manifest. The manifest is not generated from a DSL, and
this repository does not define a workflow marketplace, registry, provider,
plugin, native-runner, or broad manifest generator surface.

## Change Policy

Package data changes must edit the manifest bytes directly or use a reviewed
one-off script whose output is committed. A durable generator is out of scope
until a separate generator-specific packet proves that the frozen policy is
insufficient.

Every package-data change must update the freeze evidence block below with:

- manifest digest;
- package digest;
- selected package pin;
- selected workflow fingerprints;
- asset pins.

The evidence block is public review evidence. Public validation enforces this
policy statement with `tests/test_manifest_authoring_policy.py`; it recomputes
the manifest digest, package digest, selected workflow fingerprints, and asset
pins from the current package bytes. A selected package pin, selected workflow
fingerprint, asset digest, or package asset path change without matching
evidence fails the policy test.

`manifest.json` uses canonical authoring format: UTF-8 JSON, two-space
indentation, the documented root key order, and a single trailing newline.
Object-key sorting is used only for digest canonicalization; the committed file
keeps the reviewed presentation order.

For this policy, donor source comparisons are internal evidence only. Public
validation does not need donor workflow functions, sibling runtime source checkouts,
or legacy asset paths to recompute this policy evidence. Internal conformance
can still compare against donor/runtime evidence when the explicit internal
environment gates in `docs/public-validation.md` are set, but those
comparisons are not public CI.

If changing package data would alter selected workflow graph, action, or asset
authority, stop and use a workflow-authority packet. This policy records and
verifies approved package bytes; it does not approve new official workflow
authority.

## Freeze Evidence

<!-- manifest-freeze-evidence:BEGIN -->
```json
{
  "policy": "frozen-manifest",
  "manifest_digest": "sha256:2950bf242303246992a56eb03dfb3584418f100513ce2c630c1176599a60ad68",
  "package_digest": "sha256:5f2db51de3028e647de583154453cd56c8214a456d9d78a8952ae1c477bb948f",
  "selected_package_pin": {
    "package_id": "millrace.plus.official",
    "package_version": "0.0.0",
    "package_format_version": "1"
  },
  "selected_workflow_fingerprints": {
    "execution.lad@0.1": "sha256:75d1b758310703e87220174d06054f0d9263639bcdeb7c2a48129fa306bae051",
    "execution.lad_integrator@0.1": "sha256:3fc6157fc42538190181f80e717d3f5e667a956706dd1b03b3ea768311a39302",
    "lad.full@0.1": "sha256:43a39019580a0676f1d2fe10cc13220388894309ab849808563d8aab019d6b37",
    "planning.lad@0.1": "sha256:0b7e6fc82bea2ecb8b9de0ae3a16afdd420ddb22d2eeb205f895a9f5fb2dbfb1",
    "simple_loop@0.1": "sha256:dd0e916f646f062b1fcc7d2f8b49c0f1076c43d1883cd7c2297ad38ceff7bb5f",
    "vendor_selection@0.1": "sha256:84102abdd6020eb7c80c93df4a8db0ab2f5bc850bcfeb4a24cd6b2e1fa2bdc23"
  },
  "asset_pins": [
    {
      "asset_id": "execution.entrypoints.lad_builder",
      "content_digest": "sha256:19a156cb712c90a0fbb9b05e615888924a18b2f91c56e28c6139ab6f87728d96",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_builder.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_checker",
      "content_digest": "sha256:3e8e6de621e39777dcf29ec541aec64f0dff49f88a1beb2adda6539458312e2e",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_checker.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_consultant",
      "content_digest": "sha256:0d0835e20be607f87c0d36dea5efa5590f53fb1fd6925be3e077d7968247944e",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_consultant.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_doublechecker",
      "content_digest": "sha256:fcc3e335bc80281fabec9a90811cd71de71013aeb38ed7b49a83227f8e08d5b6",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_doublechecker.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_fixer",
      "content_digest": "sha256:b4fda612930a57de459286aa2c4e7bf34668398661609178d026e472bc7ff539",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_fixer.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_integrator",
      "content_digest": "sha256:3a15bed62a92b3f59de7ae951106ffb986b09bb105e146e9980876765872e527",
      "package_path": "assets/workflows/execution.lad_integrator/entrypoints/lad_integrator.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_troubleshooter",
      "content_digest": "sha256:6323f1c2a0bcc6ed4d3111ff38c1eca3b0415422ad6582559cc5f10b003aa669",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_troubleshooter.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_updater",
      "content_digest": "sha256:970e595a57836a50ac4bb283bcc89e2723b529f89182308b4ebc07c72d56ae3e",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_updater.md"
    },
    {
      "asset_id": "execution.skills.builder_core",
      "content_digest": "sha256:f36388c774a90472973eb76ed7d6299ef4bade7e5ea2803a6a888dc214db17b0",
      "package_path": "assets/workflows/execution.lad/skills/builder-core.md"
    },
    {
      "asset_id": "execution.skills.checker_core",
      "content_digest": "sha256:980118b3a7a25af854fd4ec3e713339a90e3ad33cb4c88f43da8c4dace32aea7",
      "package_path": "assets/workflows/execution.lad/skills/checker-core.md"
    },
    {
      "asset_id": "execution.skills.consultant_core",
      "content_digest": "sha256:45248383572d967fd7ce82d72fef2e7b66a6ae1873bed1a03164bb40e6f61ac2",
      "package_path": "assets/workflows/execution.lad/skills/consultant-core.md"
    },
    {
      "asset_id": "execution.skills.doublechecker_core",
      "content_digest": "sha256:ea3d1e139a619851e23542bdbf5d707259fe0fd0f3b044ac43d0ae47224c1bf5",
      "package_path": "assets/workflows/execution.lad/skills/doublechecker-core.md"
    },
    {
      "asset_id": "execution.skills.fixer_core",
      "content_digest": "sha256:4d50ae7da3d72a86e199d273ab2177044a93480d1a0fdcd24a50c4f1191cfd44",
      "package_path": "assets/workflows/execution.lad/skills/fixer-core.md"
    },
    {
      "asset_id": "execution.skills.integrator_core",
      "content_digest": "sha256:05173905835ee94e2ec6f243d3bbdbd35adb9d0d16bdc9061f9df3d85c491564",
      "package_path": "assets/workflows/execution.lad_integrator/skills/integrator-core.md"
    },
    {
      "asset_id": "execution.skills.troubleshooter_core",
      "content_digest": "sha256:752bae8b7588e765af3856189e593ee748c4088ee37da3f7823988e50f6f1cab",
      "package_path": "assets/workflows/execution.lad/skills/troubleshooter-core.md"
    },
    {
      "asset_id": "execution.skills.updater_core",
      "content_digest": "sha256:6a6995fcfd4113100ddfebbe2991e0e5e6e1c14a28ce6fd90348eafba133e37b",
      "package_path": "assets/workflows/execution.lad/skills/updater-core.md"
    },
    {
      "asset_id": "learning.entrypoints.analyst",
      "content_digest": "sha256:fa5f82bbc70fb577fc9e4ac30ff084b186987dd539d7a68db00420f2a4d1c4b3",
      "package_path": "assets/workflows/lad.full/entrypoints/analyst.md"
    },
    {
      "asset_id": "learning.entrypoints.curator",
      "content_digest": "sha256:215a96936e2ff62ebe70f85abec623389a154afc1598be754af1d516db05b1d2",
      "package_path": "assets/workflows/lad.full/entrypoints/curator.md"
    },
    {
      "asset_id": "learning.entrypoints.librarian",
      "content_digest": "sha256:e2c607fb0d6c389c3f661fa763917c9ee36fa5c0dfbef45f246703ae0202023c",
      "package_path": "assets/workflows/lad.full/entrypoints/librarian.md"
    },
    {
      "asset_id": "learning.entrypoints.professor",
      "content_digest": "sha256:d3be1952ff0f32bdbe84ac2826e9d239c3879977c4526ed5b466f86091210ec9",
      "package_path": "assets/workflows/lad.full/entrypoints/professor.md"
    },
    {
      "asset_id": "learning.skills.analyst_core",
      "content_digest": "sha256:40b01f7999c7abe3df70d91955f72339722c6179fbc9ff1aeda229afc1741ba4",
      "package_path": "assets/workflows/lad.full/skills/analyst-core.md"
    },
    {
      "asset_id": "learning.skills.curator_core",
      "content_digest": "sha256:5a5d0fe3516535e35bb0f9a477ab3de6809c8054b34a470c70eb3ecbbd7a6a5c",
      "package_path": "assets/workflows/lad.full/skills/curator-core.md"
    },
    {
      "asset_id": "learning.skills.librarian_core",
      "content_digest": "sha256:69ca888947b4bd9d04036b15965381beeb67541462a1398066870462b05b9a67",
      "package_path": "assets/workflows/lad.full/skills/librarian-core.md"
    },
    {
      "asset_id": "learning.skills.professor_core",
      "content_digest": "sha256:64e00675ce975e29495eec23ae77f27a6ec159d3a42ebf5edead099d956eec18",
      "package_path": "assets/workflows/lad.full/skills/professor-core.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_arbiter",
      "content_digest": "sha256:2070e14b3797fc8c359ee72aa9c1d9c35534684ee21f5f776a66977a1a9809e5",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_arbiter.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_auditor",
      "content_digest": "sha256:b4123ed550a882a2f78763609d89aa10cc544e9b52dae108f39d1464eed21652",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_auditor.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_manager",
      "content_digest": "sha256:22e162c4819c9fde2276894c7aa06dbc5a77c2a3c83a622cebd6cfaf3755bc70",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_manager.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_mechanic",
      "content_digest": "sha256:fc52905a1612bc35ada255c0f79ec3b4f2d2f5b0fd5095a70618763575e78e98",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_mechanic.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_planner",
      "content_digest": "sha256:b19bab14716d77c8c47b4a500b09768f9ded919605d674b629776eb78ab01007",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_planner.md"
    },
    {
      "asset_id": "planning.entrypoints.recon",
      "content_digest": "sha256:ac7b3d4da9abbade7f064a295f5ea7b6f6519f17fec2db5ece92144d56fe9ca9",
      "package_path": "assets/workflows/planning.lad/entrypoints/recon.md"
    },
    {
      "asset_id": "planning.skills.arbiter_core",
      "content_digest": "sha256:92464892cbb0d575374d17b3fadc935abdbda29181d61cfc377d2394cc43ddbc",
      "package_path": "assets/workflows/planning.lad/skills/arbiter-core.md"
    },
    {
      "asset_id": "planning.skills.auditor_core",
      "content_digest": "sha256:b549918a3ecfcca98ef7c99bccd2ff2cf2d0e65188b25ed43dbe4a023b723eac",
      "package_path": "assets/workflows/planning.lad/skills/auditor-core.md"
    },
    {
      "asset_id": "planning.skills.manager_core",
      "content_digest": "sha256:e7e7c52292c05283ab42e642b2418264bd91a971b964a7d2afc8e1880b707e43",
      "package_path": "assets/workflows/planning.lad/skills/manager-core.md"
    },
    {
      "asset_id": "planning.skills.mechanic_core",
      "content_digest": "sha256:23d2a563355368d9e0a75428cec58bf58f595f04e67fb610c1d8a49188e6d2f3",
      "package_path": "assets/workflows/planning.lad/skills/mechanic-core.md"
    },
    {
      "asset_id": "planning.skills.planner_core",
      "content_digest": "sha256:1a3b2d525ae027e1864c4f926cf87f47d990eab687133339c5b33671781fb73a",
      "package_path": "assets/workflows/planning.lad/skills/planner-core.md"
    },
    {
      "asset_id": "planning.skills.recon_core",
      "content_digest": "sha256:2ca33f45fa68a02006563e9fbe17e8fe624621247e2d3c00799606508b75f4a0",
      "package_path": "assets/workflows/planning.lad/skills/recon-core.md"
    },
    {
      "asset_id": "simple_loop.manager_core_skill",
      "content_digest": "sha256:072c2c3fe2a6bf62c5bc58f975e569f7fc731bf1d863d765d144f42433afde21",
      "package_path": "assets/workflows/simple_loop/skills/manager-core.md"
    },
    {
      "asset_id": "simple_loop.manager_prompt",
      "content_digest": "sha256:b4837c4b3b1f302870efc21f34ec3b97ebabdb5292d381dbbe26e0dca3495720",
      "package_path": "assets/workflows/simple_loop/entrypoints/manager.md"
    },
    {
      "asset_id": "simple_loop.reviewer_core_skill",
      "content_digest": "sha256:eaddd593333d592c78a43deaa74e2197831de1d5dda79a31ac0d3e52e0113e34",
      "package_path": "assets/workflows/simple_loop/skills/reviewer-core.md"
    },
    {
      "asset_id": "simple_loop.reviewer_prompt",
      "content_digest": "sha256:83e108af820e02fd77adf24be381cba9c4fca95d422301cff7f7948b186f66c2",
      "package_path": "assets/workflows/simple_loop/entrypoints/reviewer.md"
    },
    {
      "asset_id": "simple_loop.troubleshooter_core_skill",
      "content_digest": "sha256:e841f3da62681fed0d75b28a79da9048c1068b8623c6fc7529912962a64ccf8f",
      "package_path": "assets/workflows/simple_loop/skills/troubleshooter-core.md"
    },
    {
      "asset_id": "simple_loop.troubleshooter_prompt",
      "content_digest": "sha256:e42d3d306dece493afe5577f6a6b2f50030e23795a23b7a327cd1de6bf01573b",
      "package_path": "assets/workflows/simple_loop/entrypoints/troubleshooter.md"
    },
    {
      "asset_id": "simple_loop.worker_core_skill",
      "content_digest": "sha256:5e317e3efc5b7315bf7a40b77dd13cd52e3dca7d71826f546bf269361681e8d9",
      "package_path": "assets/workflows/simple_loop/skills/worker-core.md"
    },
    {
      "asset_id": "simple_loop.worker_prompt",
      "content_digest": "sha256:82f4ba7b804926b9730f69f0a19194b38f7611d5d5bb7bdcd7619b1394b51022",
      "package_path": "assets/workflows/simple_loop/entrypoints/worker.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.award_decider",
      "content_digest": "sha256:8e6c8d0cda846bd396735751542a2726e28a27dfd6e76fbc68d6086002613167",
      "package_path": "assets/workflows/vendor_selection/entrypoints/award_decider.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.candidate_packager",
      "content_digest": "sha256:8de88dd722eb495f2d4120fe90614661a559fbf1068b38da0efba8b75d250b94",
      "package_path": "assets/workflows/vendor_selection/entrypoints/candidate_packager.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.catalog_sourcer",
      "content_digest": "sha256:5a9e53d4d1ad2b7b99bbaae79e9c8586d74333fb17497dee65018558f9c634b1",
      "package_path": "assets/workflows/vendor_selection/entrypoints/catalog_sourcer.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.conflict_checker",
      "content_digest": "sha256:6061913c19edafd7b94a9cd911f8edd8e899a3d341dc7cea78c77cad010c7848",
      "package_path": "assets/workflows/vendor_selection/entrypoints/conflict_checker.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.decision_packager",
      "content_digest": "sha256:d05abefe60d6a32f462ea576fa9b828df857cdf2247788643f92c9a1bc0b1bf6",
      "package_path": "assets/workflows/vendor_selection/entrypoints/decision_packager.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.policy_screener",
      "content_digest": "sha256:db1e61db69d20a6ac939f9f5a138daf61f3b2600588b63bdd94b7d9c8e49a1b4",
      "package_path": "assets/workflows/vendor_selection/entrypoints/policy_screener.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.request_intake",
      "content_digest": "sha256:3a718972c4a8559952cc68b31e5d9a6bd299cfd27386df3013df4c024a1f6b37",
      "package_path": "assets/workflows/vendor_selection/entrypoints/request_intake.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.requirement_freezer",
      "content_digest": "sha256:45b3deeee853e9fa0e39c29eb840a70c4a1f446cab6cd18645475cd1b4a9848f",
      "package_path": "assets/workflows/vendor_selection/entrypoints/requirement_freezer.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.rubric_evaluator",
      "content_digest": "sha256:91c07b8f4c57d05d2fa9a8037242c737996c0f60a5ff30c74b8cb73caf63f003",
      "package_path": "assets/workflows/vendor_selection/entrypoints/rubric_evaluator.md"
    },
    {
      "asset_id": "vendor_selection.skills.award_decider_core",
      "content_digest": "sha256:a0e4b49cdb84bb2d9be7126c45537c34ea1ce444352ca8160ce60fb45b56263e",
      "package_path": "assets/workflows/vendor_selection/skills/award_decider-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.candidate_packager_core",
      "content_digest": "sha256:a15a9175741eade236f33877fe4df76ba4547b2acd166ac3e0283cd97e7f4140",
      "package_path": "assets/workflows/vendor_selection/skills/candidate_packager-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.catalog_sourcer_core",
      "content_digest": "sha256:9c0bb02d8ba3194d03ef5c8345f31154e78c44ad4e9b47b346a9fb178fe8eac6",
      "package_path": "assets/workflows/vendor_selection/skills/catalog_sourcer-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.conflict_checker_core",
      "content_digest": "sha256:94be3012f9af32fdd9e3b6523f8a4128dd9357baab3aacc7d168b64ed2cc9455",
      "package_path": "assets/workflows/vendor_selection/skills/conflict_checker-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.decision_packager_core",
      "content_digest": "sha256:f3f0fff6aace8e8d0e4b081849934a52a7b2b24245383a8fd8c7dd1e89d4c1b1",
      "package_path": "assets/workflows/vendor_selection/skills/decision_packager-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.policy_screener_core",
      "content_digest": "sha256:1c964838f85e58f5c5d4e96c61c31fca0e2d67aca18907c7553dd13b1b3f4618",
      "package_path": "assets/workflows/vendor_selection/skills/policy_screener-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.request_intake_core",
      "content_digest": "sha256:4d7f836be75b65597d9af1654cb6cfa4004069f1665ef5b9c4b2f3de07123304",
      "package_path": "assets/workflows/vendor_selection/skills/request_intake-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.requirement_freezer_core",
      "content_digest": "sha256:111249bfbe7d59c5a90de67f9c774b1e1710d6aca5a775d02950576615574100",
      "package_path": "assets/workflows/vendor_selection/skills/requirement_freezer-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.rubric_evaluator_core",
      "content_digest": "sha256:315dd498cb9fa96d797e9a382e5439b4dc374d7efda8fc2dbce1ddeb161cef2c",
      "package_path": "assets/workflows/vendor_selection/skills/rubric_evaluator-core.md"
    }
  ]
}
```
<!-- manifest-freeze-evidence:END -->
