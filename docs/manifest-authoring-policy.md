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
  "manifest_digest": "sha256:846fb4c436978dd0d199836c10409513fb499ee57843db4dcc26e3b173cf7d8d",
  "package_digest": "sha256:4e3fdf9f7c3a0bbf168f9af5e784d8efa7348fd4474e135eda597f32f68457db",
  "selected_package_pin": {
    "package_id": "millrace.plus.official",
    "package_version": "0.0.0",
    "package_format_version": "1"
  },
  "selected_workflow_fingerprints": {
    "execution.lad@0.1": "sha256:75d1b758310703e87220174d06054f0d9263639bcdeb7c2a48129fa306bae051",
    "execution.lad_integrator@0.1": "sha256:3fc6157fc42538190181f80e717d3f5e667a956706dd1b03b3ea768311a39302",
    "lad.full@0.1": "sha256:e973aeae67c48566fdeb4d41d40ec3e3b435638a464264540939548a91fcd845",
    "planning.lad@0.1": "sha256:61d72f61c881e013c2500d3280c03909e8df08a72c6b96287faadb7474ce3fc2",
    "simple_loop@0.1": "sha256:dd0e916f646f062b1fcc7d2f8b49c0f1076c43d1883cd7c2297ad38ceff7bb5f",
    "vendor_selection@0.1": "sha256:fb4bc3c1e3d9e9d9f460722d279545563fa606e799b79f32944656a097ef7224"
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
      "content_digest": "sha256:555257062e63a6c0be1f66fbf55d5644f325810bc625d26fba0dc5353100718e",
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
      "content_digest": "sha256:e8f4314ad640da5323821b5e2934c20d8870fa19007d77a3e2b0ee00d9131fbd",
      "package_path": "assets/workflows/lad.full/skills/analyst-core.md"
    },
    {
      "asset_id": "learning.skills.curator_core",
      "content_digest": "sha256:711dddae21da34cd4870b987c22450ef1a173a7e882e58fab7db021e078635a2",
      "package_path": "assets/workflows/lad.full/skills/curator-core.md"
    },
    {
      "asset_id": "learning.skills.librarian_core",
      "content_digest": "sha256:37c9c29fd3dd899fdcd45caad2f17d840fb7bc8beb1a4da6e695ca07f225191e",
      "package_path": "assets/workflows/lad.full/skills/librarian-core.md"
    },
    {
      "asset_id": "learning.skills.professor_core",
      "content_digest": "sha256:ae789a194ef3f764cb1ba92e949f2885ed5ce75295be11fe4ee1251c25d30a16",
      "package_path": "assets/workflows/lad.full/skills/professor-core.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_arbiter",
      "content_digest": "sha256:1a71824684a7a9ea1b9799627cc86107e5c6a7dc26b068d424d46983cb2c8844",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_arbiter.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_auditor",
      "content_digest": "sha256:2bd6bc6b536bb2f755e6986e5db41e645c62336a9bf82d51bc0ff391e34a63c5",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_auditor.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_manager",
      "content_digest": "sha256:1d072cb56e24c8831b0cdc0c30ac8f6efcfdd52901c3204337cd8c868792dd6e",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_manager.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_mechanic",
      "content_digest": "sha256:56710567fc68f3c53320fa54cc8095a304913d98eb80a34a8e4031949736ca11",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_mechanic.md"
    },
    {
      "asset_id": "planning.entrypoints.lad_planner",
      "content_digest": "sha256:b427589b0cac0b72d4e3d42103244fe6f17f799ac662d09478f7e0a799291578",
      "package_path": "assets/workflows/planning.lad/entrypoints/lad_planner.md"
    },
    {
      "asset_id": "planning.entrypoints.recon",
      "content_digest": "sha256:bfb31ec43067704b14921146fcaeeff20c88801735458138ae6e2c11582cf72c",
      "package_path": "assets/workflows/planning.lad/entrypoints/recon.md"
    },
    {
      "asset_id": "planning.skills.arbiter_core",
      "content_digest": "sha256:4ad12a01c6ecf72a61627fd00b3bacc43602dd21af5fd2641b8466c667e88109",
      "package_path": "assets/workflows/planning.lad/skills/arbiter-core.md"
    },
    {
      "asset_id": "planning.skills.auditor_core",
      "content_digest": "sha256:87e2975cf5a9c2685d7f84d2041cb0db25bed32b71f99364a23f4029ceb06d22",
      "package_path": "assets/workflows/planning.lad/skills/auditor-core.md"
    },
    {
      "asset_id": "planning.skills.manager_core",
      "content_digest": "sha256:c325205c1d26003eadeb6394afa511f6298b4c78350fd9ccccbc0bc3b1ba9d55",
      "package_path": "assets/workflows/planning.lad/skills/manager-core.md"
    },
    {
      "asset_id": "planning.skills.mechanic_core",
      "content_digest": "sha256:ff01cea2424b0739e5f37b4c719dee52915f4b329e91e59cba9e04228dddf759",
      "package_path": "assets/workflows/planning.lad/skills/mechanic-core.md"
    },
    {
      "asset_id": "planning.skills.planner_core",
      "content_digest": "sha256:d3dd933249da859bb5418f818bfee3c2a1419feffa4617cfff80442aa7caca33",
      "package_path": "assets/workflows/planning.lad/skills/planner-core.md"
    },
    {
      "asset_id": "planning.skills.recon_core",
      "content_digest": "sha256:728ac33a8fd91bc253796baa3b90b010831c968ec191d14402b0cb3ba3cbb85c",
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
      "content_digest": "sha256:4621182ce3906517ad71034dbc4b3d8f0269a2e3c93ea35e8f21f39bfcae7b4f",
      "package_path": "assets/workflows/vendor_selection/entrypoints/award_decider.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.candidate_packager",
      "content_digest": "sha256:2d8c448c4fb90e7118f17a292c62434ba35a37624ff8b175a849a805d3233339",
      "package_path": "assets/workflows/vendor_selection/entrypoints/candidate_packager.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.catalog_sourcer",
      "content_digest": "sha256:99fd28e93317422dcde306bdea8399ba8d96828b43a0fc10a47d11909c722f5c",
      "package_path": "assets/workflows/vendor_selection/entrypoints/catalog_sourcer.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.conflict_checker",
      "content_digest": "sha256:9c0ccf66458b8e542a0b444b7d8430ea43758029fc228132431ebcd8708c04e4",
      "package_path": "assets/workflows/vendor_selection/entrypoints/conflict_checker.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.decision_packager",
      "content_digest": "sha256:e75fd239d3f7205dca1a66a04f2e4b6d5bebd9f89153b7db12977c85ca2773fc",
      "package_path": "assets/workflows/vendor_selection/entrypoints/decision_packager.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.policy_screener",
      "content_digest": "sha256:91817d822148a2e70b2549fe03f99f00b4e1957789c97afa8ea61bb466997684",
      "package_path": "assets/workflows/vendor_selection/entrypoints/policy_screener.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.request_intake",
      "content_digest": "sha256:0361bbdedbbdd4031ae4dc7528accb1d7b180df7a1a58de6197f2fb2ebc71362",
      "package_path": "assets/workflows/vendor_selection/entrypoints/request_intake.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.requirement_freezer",
      "content_digest": "sha256:98fe63eb6562bb496a7e589261afeb0f1535e4bc92fafbb2950e4867336d72c2",
      "package_path": "assets/workflows/vendor_selection/entrypoints/requirement_freezer.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.rubric_evaluator",
      "content_digest": "sha256:946ba01bc27d511801b9459297572bb9534ca77f5675516d73aba8e54a518ec8",
      "package_path": "assets/workflows/vendor_selection/entrypoints/rubric_evaluator.md"
    },
    {
      "asset_id": "vendor_selection.skills.award_decider_core",
      "content_digest": "sha256:a4a89ebc1ce9092bab146714cc8c2df0aea180556dc2cab11aba1989ced6ec05",
      "package_path": "assets/workflows/vendor_selection/skills/award_decider-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.candidate_packager_core",
      "content_digest": "sha256:e82b01ebbf1c41f6a6be2e5a12da907515c574dd4a1739db1a5bc0f753baecd1",
      "package_path": "assets/workflows/vendor_selection/skills/candidate_packager-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.catalog_sourcer_core",
      "content_digest": "sha256:031e5cba8692160766eae4b553015e28b093004fec332bcbb8cb27cd8647e7ad",
      "package_path": "assets/workflows/vendor_selection/skills/catalog_sourcer-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.conflict_checker_core",
      "content_digest": "sha256:9194aea620cc0f4ecc9463fd192946f66fef0fe8ae1304023a68061c8f0553b3",
      "package_path": "assets/workflows/vendor_selection/skills/conflict_checker-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.decision_packager_core",
      "content_digest": "sha256:45b80b76f4472328989e31d929ca553b5cd3f112d3e7cd4fabc700ebf8be78df",
      "package_path": "assets/workflows/vendor_selection/skills/decision_packager-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.policy_screener_core",
      "content_digest": "sha256:65f50b3da48655c04616b0d4aa2a9768c69862c8efc29422b2c121c108532070",
      "package_path": "assets/workflows/vendor_selection/skills/policy_screener-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.request_intake_core",
      "content_digest": "sha256:649b984b2504f84e20bed3bc9d968cfb824f09710be2b794b9e8e64d06363fa7",
      "package_path": "assets/workflows/vendor_selection/skills/request_intake-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.requirement_freezer_core",
      "content_digest": "sha256:3eab8f42ea85aa5aa0929e29a011bae5c9a5a01134968a9146811adba6f60e49",
      "package_path": "assets/workflows/vendor_selection/skills/requirement_freezer-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.rubric_evaluator_core",
      "content_digest": "sha256:924e49037480df088b9a5998b52dd9ddb99f343f72140a87ab5d6867bf8a9335",
      "package_path": "assets/workflows/vendor_selection/skills/rubric_evaluator-core.md"
    }
  ]
}
```
<!-- manifest-freeze-evidence:END -->
