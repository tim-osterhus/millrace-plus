# Manifest Maintenance

`millrace_workflow_package/manifest.json` is the committed source of truth for
the official workflow package. It is canonical JSON rather than generated
output from another configuration language.

This keeps review direct: a workflow change and the exact authority it selects
appear in the same file. A durable manifest generator should be added only if
maintaining the canonical JSON becomes a demonstrated problem.

## Change Policy

Package-data changes edit the manifest directly or use a one-time script whose
result is reviewed and committed. The committed JSON remains authoritative.

Every package-data change must update the freeze evidence block below with:

- manifest digest;
- package digest;
- selected package pin;
- selected workflow fingerprints;
- asset pins.

The evidence block below makes package drift visible.
`tests/test_manifest_authoring_policy.py` recomputes
the manifest digest, package digest, selected workflow fingerprints, and asset
pins from the current package bytes. A selected package pin, selected workflow
fingerprint, asset digest, or package asset path change without matching
evidence fails the policy test.

`manifest.json` uses canonical authoring format: UTF-8 JSON, two-space
indentation, the documented root key order, and a single trailing newline.
Object-key sorting is used only for digest canonicalization; the committed file
keeps the reviewed presentation order.

Standalone validation does not need donor workflow functions, a sibling
runtime checkout, or legacy asset paths. Source-conformance tests may compare
against those references when their explicit environment variables are set,
but the manifest and package bytes remain independently verifiable.

If a change alters a selected graph, terminal action, schema, runner binding,
or asset, review it as a workflow behavior change rather than a digest-only
maintenance edit. This document records package bytes; it does not decide
which workflow behavior should be official.

## Freeze Evidence

<!-- manifest-freeze-evidence:BEGIN -->
```json
{
  "policy": "frozen-manifest",
  "manifest_digest": "sha256:7c9a1ed4746ab8e244920484782da51d04d876a4f41bd60ffac21b0bb4df7174",
  "package_digest": "sha256:d3ad40e592a3070808467bb215fb694b341022f2d9ed799c04d95d76f1325a0d",
  "selected_package_pin": {
    "package_id": "millrace.plus.official",
    "package_version": "0.0.0",
    "package_format_version": "1"
  },
  "selected_workflow_fingerprints": {
    "execution.lad@0.1": "sha256:75d1b758310703e87220174d06054f0d9263639bcdeb7c2a48129fa306bae051",
    "execution.lad_integrator@0.1": "sha256:3fc6157fc42538190181f80e717d3f5e667a956706dd1b03b3ea768311a39302",
    "lad.full@0.1": "sha256:d1d6245fa1f7a77409ab3104a821bbc56d5a1e463fecc7c7fda416efc3c4541e",
    "planning.lad@0.1": "sha256:e3435807aead8a077dc4f28550b26ade406cd75c021be020399d1e8721275306",
    "simple_loop@0.1": "sha256:dd0e916f646f062b1fcc7d2f8b49c0f1076c43d1883cd7c2297ad38ceff7bb5f",
    "vendor_selection@0.1": "sha256:4dfad85a5d3e0adfb20c9326532ad56ee188b7d9207661435d122a225c66ec42"
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
      "content_digest": "sha256:07c43a588ea413e092789ff4285827b864106e2b4ab7b1fb5b168cb1569b7a1c",
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
      "content_digest": "sha256:7e5ec3b7541de1ca4cffb5358fa78e3c937ec2ef1b2085a0e0bc89b4f02ed0be",
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
      "content_digest": "sha256:df6282f2762296119aa2a31b8e68997c6fcc40de0bd82fb96c910bb997399aef",
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
      "content_digest": "sha256:4a2743ab0211929f686eec48a2347f5053570e79acd9221955bbf5c2c25ad811",
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
      "content_digest": "sha256:88a55ca64c6f0240be1ec0d658cb4405209a7da4a177c0af221903e69225da9f",
      "package_path": "assets/workflows/vendor_selection/entrypoints/award_decider.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.candidate_packager",
      "content_digest": "sha256:a267e80ac858381657fb2fa955ebc4a7e09781381b9cf8def328e26a4c5cb15b",
      "package_path": "assets/workflows/vendor_selection/entrypoints/candidate_packager.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.catalog_sourcer",
      "content_digest": "sha256:ce618ae374bfa51dedb67893c4756bebd773b337c63bc8e598849ffd026c471e",
      "package_path": "assets/workflows/vendor_selection/entrypoints/catalog_sourcer.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.conflict_checker",
      "content_digest": "sha256:2a44e16b041440de0d2eb4d91b340802c63bef396870acf1423dff4d53e8be06",
      "package_path": "assets/workflows/vendor_selection/entrypoints/conflict_checker.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.decision_packager",
      "content_digest": "sha256:e75fd239d3f7205dca1a66a04f2e4b6d5bebd9f89153b7db12977c85ca2773fc",
      "package_path": "assets/workflows/vendor_selection/entrypoints/decision_packager.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.policy_screener",
      "content_digest": "sha256:4f057b0c5aaf3360a00a42813b6b73dfa9d37ad330dcd3616d4b5341338d71d9",
      "package_path": "assets/workflows/vendor_selection/entrypoints/policy_screener.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.request_intake",
      "content_digest": "sha256:0361bbdedbbdd4031ae4dc7528accb1d7b180df7a1a58de6197f2fb2ebc71362",
      "package_path": "assets/workflows/vendor_selection/entrypoints/request_intake.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.requirement_freezer",
      "content_digest": "sha256:134a62ddb8510c87b4bbf93a0b67b33646752caacf810089d2e2c320213ade71",
      "package_path": "assets/workflows/vendor_selection/entrypoints/requirement_freezer.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.rubric_evaluator",
      "content_digest": "sha256:433df90de96f93a2d47ebe1aac968d0fe72cdde39a2e493872d5c7ab9964e2d0",
      "package_path": "assets/workflows/vendor_selection/entrypoints/rubric_evaluator.md"
    },
    {
      "asset_id": "vendor_selection.skills.award_decider_core",
      "content_digest": "sha256:20f0fa7bb75e524da98b37c166b4381230e739dfcc4d3dd24089526cf41ed9ae",
      "package_path": "assets/workflows/vendor_selection/skills/award_decider-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.candidate_packager_core",
      "content_digest": "sha256:5f281f257f1bfd8e64c70754201b00743116705d4493f8bf59c0fb8fecd9febe",
      "package_path": "assets/workflows/vendor_selection/skills/candidate_packager-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.catalog_sourcer_core",
      "content_digest": "sha256:7365c311c4537aba4abefa0228e7675ed8eb3d20bb890c4c3e0dd160888ba98a",
      "package_path": "assets/workflows/vendor_selection/skills/catalog_sourcer-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.conflict_checker_core",
      "content_digest": "sha256:d5460856aef962902801b8ce320b23ef84db3ef6a6242da9e5c5e044bc84a353",
      "package_path": "assets/workflows/vendor_selection/skills/conflict_checker-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.decision_packager_core",
      "content_digest": "sha256:e2807c450664bb700682e53c74539f320b225bef5dee1073519d849736d7134d",
      "package_path": "assets/workflows/vendor_selection/skills/decision_packager-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.policy_screener_core",
      "content_digest": "sha256:e12e1f2c603d75206728e3ee9bf9346fa4aa23a36b30ff80543c210448b37193",
      "package_path": "assets/workflows/vendor_selection/skills/policy_screener-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.request_intake_core",
      "content_digest": "sha256:5f9dea6e4ac2074edbd3a288afa1090fb63441f5bc19cc1a66eb8722f6fc02d6",
      "package_path": "assets/workflows/vendor_selection/skills/request_intake-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.requirement_freezer_core",
      "content_digest": "sha256:046cd2c62c7f7d5faa621a7f3b27541126f1cc073855638657afcc81c8fcbfd3",
      "package_path": "assets/workflows/vendor_selection/skills/requirement_freezer-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.rubric_evaluator_core",
      "content_digest": "sha256:8f736c7d764e3d31d87e43d9310a4b28116cfec40d1bfe1ec8eec7da36e1b669",
      "package_path": "assets/workflows/vendor_selection/skills/rubric_evaluator-core.md"
    }
  ]
}
```
<!-- manifest-freeze-evidence:END -->
