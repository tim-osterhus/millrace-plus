# CI v2 repair

Use the exact artifact schema supplied by the captured input's result_schema_ref.
The pure v2 validator is authoritative. Do not add fields. Artifacts are separate
from the json-v1 semantic envelope; null/absent facts follow the selected schema.

Implement only the accepted assessment paths. Preserve all unrelated edits. Do not run checks; the declared checks stage executes trusted argv. Record actual source before/after and the exact patch artifact.

Before returning, verify the original subject/input and prepared attempt match;
all refs resolve against the owning record directory and pin regular files;
source/config/assessment/check identities are current; facts remain separate
from interpretation. Model text cannot prove process closure or authorize writes.

A valid terminal envelope is {"outcome":"PATCH_READY","summary":"retained evidence","refs":[]}.
Invalid envelopes include {"outcome":7,"summary":"bad type","refs":[]},
{"summary":"missing outcome","refs":[]}, and
{"outcome":"BLOCKED","summary":"extra field","refs":[],"next":"repair"}.
Completion means the exact artifact is retained and the declared marker returned;
the following deterministic guard decides acceptance.

## Exact artifact schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "format": {
      "const": "millrace-plus-cicd-repair/v2"
    },
    "input_id": {
      "type": "string",
      "minLength": 1
    },
    "attempt_id": {
      "type": "string",
      "minLength": 1
    },
    "assessment_ref": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "minLength": 1
        },
        "sha256": {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        }
      },
      "required": [
        "path",
        "sha256"
      ],
      "additionalProperties": false
    },
    "source_before_sha256": {
      "type": "string",
      "pattern": "^[0-9a-f]{64}$"
    },
    "source_after_sha256": {
      "type": "string",
      "pattern": "^[0-9a-f]{64}$"
    },
    "patch_ref": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "minLength": 1
        },
        "sha256": {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        }
      },
      "required": [
        "path",
        "sha256"
      ],
      "additionalProperties": false
    },
    "changed_paths": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "uniqueItems": true
    },
    "change_classes": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "uniqueItems": true
    },
    "summary": {
      "type": "string",
      "minLength": 1
    },
    "evidence_refs": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": {
            "type": "string",
            "minLength": 1
          },
          "sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$"
          }
        },
        "required": [
          "path",
          "sha256"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "format",
    "input_id",
    "attempt_id",
    "assessment_ref",
    "source_before_sha256",
    "source_after_sha256",
    "patch_ref",
    "changed_paths",
    "change_classes",
    "summary",
    "evidence_refs"
  ],
  "additionalProperties": false
}
```

## Structural artifact examples

These examples show shape only. Placeholder refs/IDs grant no acceptance; use the actual prepared input and retained evidence.

Valid shape:

```json
{
  "format": "millrace-plus-cicd-repair/v2",
  "input_id": "1111111111111111111111111111111111111111111111111111111111111111",
  "attempt_id": "prepared-attempt",
  "assessment_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "source_before_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "source_after_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "patch_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "changed_paths": [
    "src/calc.py"
  ],
  "change_classes": [],
  "summary": "use the addition operator",
  "evidence_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ]
}
```

Extra undeclared field:

```json
{
  "format": "millrace-plus-cicd-repair/v2",
  "input_id": "1111111111111111111111111111111111111111111111111111111111111111",
  "attempt_id": "prepared-attempt",
  "assessment_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "source_before_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "source_after_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "patch_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "changed_paths": [
    "src/calc.py"
  ],
  "change_classes": [],
  "summary": "use the addition operator",
  "evidence_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ],
  "extra": "forbidden"
}
```

Missing required field:

```json
{
  "format": "millrace-plus-cicd-repair/v2",
  "input_id": "1111111111111111111111111111111111111111111111111111111111111111",
  "assessment_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "source_before_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "source_after_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "patch_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "changed_paths": [
    "src/calc.py"
  ],
  "change_classes": [],
  "summary": "use the addition operator",
  "evidence_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ]
}
```

Wrong field type:

```json
{
  "format": "millrace-plus-cicd-repair/v2",
  "input_id": "1111111111111111111111111111111111111111111111111111111111111111",
  "attempt_id": 7,
  "assessment_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "source_before_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "source_after_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "patch_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "changed_paths": [
    "src/calc.py"
  ],
  "change_classes": [],
  "summary": "use the addition operator",
  "evidence_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ]
}
```

Resolve the active pointer from the workspace. The selected input file’s parent directory is the base for its internal relative refs and output path; preserve those paths and raw referenced bytes.
