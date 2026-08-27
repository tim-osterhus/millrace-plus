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
runtime checkout, or legacy asset paths. It validates the packaged bytes
through public APIs installed from Millrace and Millforge distributions.

If a change alters a selected graph, terminal action, schema, runner binding,
or asset, review it as a workflow behavior change rather than a digest-only
maintenance edit. This document records package bytes; it does not decide
which workflow behavior should be official.

## Freeze Evidence

<!-- manifest-freeze-evidence:BEGIN -->
```json
{
  "asset_pins": [
    {
      "asset_id": "execution.entrypoints.lad_builder",
      "content_digest": "sha256:2ae327cd582353e2510de7dff31ee55eb70f4ffcdfa2d2303db6bd2201b92a8c",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_builder.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_checker",
      "content_digest": "sha256:4444f19a996e9329f2ec0bda4c9b3c61600f29e91d362b05c1777713e2dc369a",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_checker.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_consultant",
      "content_digest": "sha256:af500ad1e542b4ad582931a12549c72544ac4709c819931daa7800c4f133952f",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_consultant.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_doublechecker",
      "content_digest": "sha256:9af86c634a8159967489118ecba47a965b1685a62180cfa4789c079f196167d0",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_doublechecker.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_fixer",
      "content_digest": "sha256:9af21092225299aab1ba99311fa3c32933ab9d5c44597a257004b0aed59a9917",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_fixer.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_integrator",
      "content_digest": "sha256:4ab997199cf5eacdc6026cda02d5abafe972011dc852ba8a4bae20c5ca13f980",
      "package_path": "assets/workflows/execution.lad_integrator/entrypoints/lad_integrator.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_troubleshooter",
      "content_digest": "sha256:cc9c35e0a78e627e572cd2eb183275fc4c1a9c4964201cc5e267e6a2c2f86d3c",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_troubleshooter.md"
    },
    {
      "asset_id": "execution.entrypoints.lad_updater",
      "content_digest": "sha256:f72aa2b7fe13d6eb78be2aa62184dc67d644e0a7834f0b1fefb6d232ab3f6544",
      "package_path": "assets/workflows/execution.lad/entrypoints/lad_updater.md"
    },
    {
      "asset_id": "execution.lad_codex_semantic_worktree.context_router",
      "content_digest": "sha256:00053a2572581acd9441ebc8c1199accee49073afe87190a60bf847fec25d129",
      "package_path": "assets/workflows/execution.lad_codex_semantic_worktree/context/router.md"
    },
    {
      "asset_id": "execution.lad_codex_semantic_worktree.entrypoints.lad_builder",
      "content_digest": "sha256:839385509979a7fad68fd5069289bf95589670f1d9b273e2f774f42527b222b5",
      "package_path": "assets/workflows/execution.lad_codex_semantic_worktree/entrypoints/lad_builder.md"
    },
    {
      "asset_id": "execution.lad_codex_semantic_worktree.entrypoints.lad_checker",
      "content_digest": "sha256:32a10558a77fe49613b53eb0779bd64dc5d14e6d8cdd6230dd70897aedfaec0b",
      "package_path": "assets/workflows/execution.lad_codex_semantic_worktree/entrypoints/lad_checker.md"
    },
    {
      "asset_id": "execution.lad_codex_semantic_worktree.entrypoints.lad_doublechecker",
      "content_digest": "sha256:e99a49e9e5cbe59273ca01b052662e3d945a7c52dd6f7ff07be22b63cbb1904b",
      "package_path": "assets/workflows/execution.lad_codex_semantic_worktree/entrypoints/lad_doublechecker.md"
    },
    {
      "asset_id": "execution.lad_codex_semantic_worktree.entrypoints.lad_fixer",
      "content_digest": "sha256:1f4547179ea2de9eac48231c547689d96c8995bbccf221ded60b7c1d4bea7bbb",
      "package_path": "assets/workflows/execution.lad_codex_semantic_worktree/entrypoints/lad_fixer.md"
    },
    {
      "asset_id": "execution.lad_codex_semantic_worktree.entrypoints.lad_updater",
      "content_digest": "sha256:27d0e0f522d417c197f3538df47ad4ce9737c190eedc9e903acf45c1400b47ef",
      "package_path": "assets/workflows/execution.lad_codex_semantic_worktree/entrypoints/lad_updater.md"
    },
    {
      "asset_id": "execution.lad_codex_semantic_worktree.skills.updater_core",
      "content_digest": "sha256:4f5426983fd665ec5a0daeef4756fd559cd27594911dd69ec157b8b1a8b5eee1",
      "package_path": "assets/workflows/execution.lad_codex_semantic_worktree/skills/updater-core.md"
    },
    {
      "asset_id": "execution.skills.builder_core",
      "content_digest": "sha256:d789eb8a142451cffd3476b11671ab7b636a3e704c109d204c82db3e9d5d3dac",
      "package_path": "assets/workflows/execution.lad/skills/builder-core.md"
    },
    {
      "asset_id": "execution.skills.checker_core",
      "content_digest": "sha256:81a4b59af7f82d91951dfa08d5d2d7f6fce548d8f2b370fe7c8dd99d26fc7140",
      "package_path": "assets/workflows/execution.lad/skills/checker-core.md"
    },
    {
      "asset_id": "execution.skills.consultant_core",
      "content_digest": "sha256:ac23328c80c9445f5302ebd361b25fded1c4b36fd0bc8e6cebff563e808cfe55",
      "package_path": "assets/workflows/execution.lad/skills/consultant-core.md"
    },
    {
      "asset_id": "execution.skills.doublechecker_core",
      "content_digest": "sha256:0ce450272d17ca87534064c798a0aa8e63ec69703f91140e356e5b22a49b9026",
      "package_path": "assets/workflows/execution.lad/skills/doublechecker-core.md"
    },
    {
      "asset_id": "execution.skills.fixer_core",
      "content_digest": "sha256:5834155f56f905c2a6344932cdaddd6ffd7f5d6f131e7027f33c6452c6acae75",
      "package_path": "assets/workflows/execution.lad/skills/fixer-core.md"
    },
    {
      "asset_id": "execution.skills.integrator_core",
      "content_digest": "sha256:380105b6251148c888602d73ce4b2da41cabe24d57c8df2a6761ccd4d04e2d91",
      "package_path": "assets/workflows/execution.lad_integrator/skills/integrator-core.md"
    },
    {
      "asset_id": "execution.skills.troubleshooter_core",
      "content_digest": "sha256:c483243abe2afa86c447c47b16ada4b8d1a160147a5e4f95afa602be2899dc1b",
      "package_path": "assets/workflows/execution.lad/skills/troubleshooter-core.md"
    },
    {
      "asset_id": "execution.skills.updater_core",
      "content_digest": "sha256:728998443904834ab25ca2f05da420b7d8b74f738fe506523df91902fefdbee2",
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
      "content_digest": "sha256:635ce98cd9cfde35aea167cd2708ad146326f8df189a9a05f083af6fae0c5a44",
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
      "content_digest": "sha256:3e0401fa1297a57129ab12a0ad0f08f5ee8cb274b89b1d47221d68542ae2d49e",
      "package_path": "assets/workflows/planning.lad/entrypoints/recon.md"
    },
    {
      "asset_id": "planning.skills.arbiter_core",
      "content_digest": "sha256:bb1d2a5705db190d441b38090330f024c276cd29956f3dcfb6dd0f420e9ec99f",
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
      "content_digest": "sha256:835ea6d7b124a74a3bb0d331c4f8748d2f8fe12fe8cf85bc4ea169f61d9ff682",
      "package_path": "assets/workflows/planning.lad/skills/recon-core.md"
    },
    {
      "asset_id": "simple_loop.context_router",
      "content_digest": "sha256:f2609ab35c232a423fc0f15826e14024d5edc0e50ec58b610611c7568682cdfc",
      "package_path": "assets/workflows/simple_loop/context/router.md"
    },
    {
      "asset_id": "simple_loop.manager_core_skill",
      "content_digest": "sha256:f6c0728e9dfdf04d7d11b5b9e8dac86795a8eaeacebd544abc3fcb807f528d4c",
      "package_path": "assets/workflows/simple_loop/skills/manager-core.md"
    },
    {
      "asset_id": "simple_loop.manager_prompt",
      "content_digest": "sha256:8f684884b717e0b38e20f7b92a0c0174bf4c15808cbf65a022e5fc4a294df515",
      "package_path": "assets/workflows/simple_loop/entrypoints/manager.md"
    },
    {
      "asset_id": "simple_loop.reviewer_core_skill",
      "content_digest": "sha256:998a09034bf5e063f1e6c8a57bcbd9123c14c9bdea45ad5539f325cc0c7f078d",
      "package_path": "assets/workflows/simple_loop/skills/reviewer-core.md"
    },
    {
      "asset_id": "simple_loop.reviewer_prompt",
      "content_digest": "sha256:c39670a62f9b0311e40e2eb3f065059b01325e28f6e40b4684212997d965d2b2",
      "package_path": "assets/workflows/simple_loop/entrypoints/reviewer.md"
    },
    {
      "asset_id": "simple_loop.troubleshooter_core_skill",
      "content_digest": "sha256:e841f3da62681fed0d75b28a79da9048c1068b8623c6fc7529912962a64ccf8f",
      "package_path": "assets/workflows/simple_loop/skills/troubleshooter-core.md"
    },
    {
      "asset_id": "simple_loop.troubleshooter_prompt",
      "content_digest": "sha256:d238c70b0f70e3d06ec04b8a3f97cd3e31241a6b32fd3f04215a6b783ebd7aaf",
      "package_path": "assets/workflows/simple_loop/entrypoints/troubleshooter.md"
    },
    {
      "asset_id": "simple_loop.worker_core_skill",
      "content_digest": "sha256:578c6ea31adc36e4c773250284861a8b3303aed41adb57cdb549c9a6efa54abc",
      "package_path": "assets/workflows/simple_loop/skills/worker-core.md"
    },
    {
      "asset_id": "simple_loop.worker_prompt",
      "content_digest": "sha256:4aaddb4cdd36a27f135b52e8e4308db5d81f74e86acb0dc5cb5dbe7ee2cb3709",
      "package_path": "assets/workflows/simple_loop/entrypoints/worker.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.award_decider",
      "content_digest": "sha256:e9b40de45a2b1d572844a91f418f26c25e25dcc0b809c2ffb1a18d5343704d24",
      "package_path": "assets/workflows/vendor_selection/entrypoints/award_decider.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.candidate_packager",
      "content_digest": "sha256:736ea920d8297b6261066af7375ac067c99bfb3823ebc5884981c48e30f68693",
      "package_path": "assets/workflows/vendor_selection/entrypoints/candidate_packager.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.catalog_sourcer",
      "content_digest": "sha256:f470d90df56e87b519b4cf17c0c4155fcc7de4bbce51475bcf9d64d0897b8376",
      "package_path": "assets/workflows/vendor_selection/entrypoints/catalog_sourcer.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.conflict_checker",
      "content_digest": "sha256:2a44e16b041440de0d2eb4d91b340802c63bef396870acf1423dff4d53e8be06",
      "package_path": "assets/workflows/vendor_selection/entrypoints/conflict_checker.md"
    },
    {
      "asset_id": "vendor_selection.entrypoints.decision_packager",
      "content_digest": "sha256:d31fbc98e4606424907cffd31d07d3c164d9444f0c66cd158a36944aedb61a3c",
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
      "content_digest": "sha256:eec21c7107cc9f9ace77ae89aff42e48fd3e795121cf4ec437aef915eb99cc97",
      "package_path": "assets/workflows/vendor_selection/skills/award_decider-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.candidate_packager_core",
      "content_digest": "sha256:368bb273a16e1352848b9accfcc3ada63558874964c618f833c32723c784b3ec",
      "package_path": "assets/workflows/vendor_selection/skills/candidate_packager-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.catalog_sourcer_core",
      "content_digest": "sha256:09fc91dd9caf07909466bffe3c45efe0be94711ceefd966eb30f5a21c5397a38",
      "package_path": "assets/workflows/vendor_selection/skills/catalog_sourcer-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.conflict_checker_core",
      "content_digest": "sha256:d5460856aef962902801b8ce320b23ef84db3ef6a6242da9e5c5e044bc84a353",
      "package_path": "assets/workflows/vendor_selection/skills/conflict_checker-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.decision_packager_core",
      "content_digest": "sha256:5de481850de6597c86034f5e930943342f27cf7d69aef1c71b7cac5e9e0241cb",
      "package_path": "assets/workflows/vendor_selection/skills/decision_packager-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.policy_screener_core",
      "content_digest": "sha256:e12e1f2c603d75206728e3ee9bf9346fa4aa23a36b30ff80543c210448b37193",
      "package_path": "assets/workflows/vendor_selection/skills/policy_screener-core.md"
    },
    {
      "asset_id": "vendor_selection.skills.request_intake_core",
      "content_digest": "sha256:e1ae139ae9a2ae2685c540d61bb98482ac58db02c5e6be8c3c003cc50b08b159",
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
  ],
  "manifest_digest": "sha256:cef079a6601cf1f9dffd764957dcdbfddd94f60b3f314711086b29c06039507a",
  "package_digest": "sha256:7958992ba51e598ff2a3c3abbf633ba1aa618e2becc416a2eabef35c4cd6494e",
  "policy": "frozen-manifest",
  "selected_package_pin": {
    "package_format_version": "1",
    "package_id": "millrace.plus.official",
    "package_version": "0.22.3"
  },
  "selected_workflow_fingerprints": {
    "execution.lad@0.1": "sha256:195fbe1c5a982366b214373e65d8a20b0393605af4dbc1aff361a0413a589fdd",
    "execution.lad_codex_control@0.1": "sha256:3b88a81c2c6a29ffca95ae1a9052e6e44df48c8c64a1c894b36aa85da3c2ac46",
    "execution.lad_codex_semantic_worktree@0.2": "sha256:a8869898be7957d317fcd2b3441b656bbc7dbeb007fa7974c0c1745f8114f975",
    "execution.lad_integrator@0.1": "sha256:5f0e8093b007b2e026034cdda8222804bba0deeaec90441fe97c54097c6d2a28",
    "lad.full@0.1": "sha256:60e075360d232d99b2e57cafbbe5afaf0a10be1fb8546429718e1ee943db3b77",
    "planning.lad@0.1": "sha256:5d5b2420bedf88f4baf3acea78de444356cb5fe9d6921f2c953db76643baa78c",
    "simple_loop@0.1": "sha256:6712ff418127e9464096ee69ae80e0d1c8b7f1d6d17b2226f9fd9fe8b40c22da",
    "vendor_selection@0.1": "sha256:41430d1a6c96149209f2983d66c68fbd0ae7d030b3ae01061ec8c2971c3c9e27"
  }
}
```
<!-- manifest-freeze-evidence:END -->
