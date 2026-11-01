# CI v2 review

Use the exact artifact schema supplied by the captured input's result_schema_ref.
The pure v2 validator is authoritative. Do not add fields. Artifacts are separate
from the json-v1 semantic envelope; null/absent facts follow the selected schema.

Read the exact selected check and patch evidence. Do not implement, run checks, or change target products. ACCEPT requires zero unresolved findings and the same current source/check basis.

Before returning, verify the original subject/input and prepared attempt match;
all refs resolve against the owning record directory and pin regular files;
source/config/assessment/check identities are current; facts remain separate
from interpretation. Model text cannot prove process closure or authorize writes.

A valid terminal envelope is {"outcome":"ACCEPT","summary":"retained evidence","refs":[]}.
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
      "const": "millrace-plus-cicd-review/v2"
    },
    "input_id": {
      "anyOf": [
        {
          "type": "string",
          "minLength": 1
        },
        {
          "type": "null"
        }
      ]
    },
    "candidate_id": {
      "anyOf": [
        {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        },
        {
          "type": "null"
        }
      ]
    },
    "materialization_id": {
      "anyOf": [
        {
          "type": "string",
          "minLength": 1
        },
        {
          "type": "null"
        }
      ]
    },
    "attempt_id": {
      "type": "string",
      "minLength": 1
    },
    "candidate_sha256": {
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
    "checks_refs": {
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
    },
    "verdict": {
      "enum": [
        "ACCEPT",
        "REJECT",
        "BLOCKED"
      ]
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string",
            "minLength": 1
          },
          "scenario": {
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
          },
          "impact": {
            "type": "string",
            "minLength": 1
          },
          "likelihood": {
            "type": "string",
            "minLength": 1
          },
          "status": {
            "enum": [
              "unresolved",
              "resolved",
              "withdrawn"
            ]
          },
          "resolution_ref": {
            "anyOf": [
              {
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
              {
                "type": "null"
              }
            ]
          }
        },
        "required": [
          "id",
          "scenario",
          "evidence_refs",
          "impact",
          "likelihood",
          "status",
          "resolution_ref"
        ],
        "additionalProperties": false
      }
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
    "candidate_id",
    "materialization_id",
    "attempt_id",
    "candidate_sha256",
    "patch_ref",
    "checks_refs",
    "verdict",
    "findings",
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
  "format": "millrace-plus-cicd-review/v2",
  "input_id": "1111111111111111111111111111111111111111111111111111111111111111",
  "attempt_id": "prepared-attempt",
  "candidate_id": null,
  "materialization_id": null,
  "candidate_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "patch_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "checks_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ],
  "verdict": "ACCEPT",
  "findings": [],
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
  "format": "millrace-plus-cicd-review/v2",
  "input_id": "1111111111111111111111111111111111111111111111111111111111111111",
  "attempt_id": "prepared-attempt",
  "candidate_id": null,
  "materialization_id": null,
  "candidate_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "patch_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "checks_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ],
  "verdict": "ACCEPT",
  "findings": [],
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
  "format": "millrace-plus-cicd-review/v2",
  "input_id": "1111111111111111111111111111111111111111111111111111111111111111",
  "candidate_id": null,
  "materialization_id": null,
  "candidate_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "patch_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "checks_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ],
  "verdict": "ACCEPT",
  "findings": [],
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
  "format": "millrace-plus-cicd-review/v2",
  "input_id": "1111111111111111111111111111111111111111111111111111111111111111",
  "attempt_id": 7,
  "candidate_id": null,
  "materialization_id": null,
  "candidate_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "patch_ref": {
    "path": "evidence.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "checks_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ],
  "verdict": "ACCEPT",
  "findings": [],
  "evidence_refs": [
    {
      "path": "evidence.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ]
}
```

Resolve the active pointer from the workspace. The selected input file’s parent directory is the base for its internal relative refs and output path; preserve those paths and raw referenced bytes.
