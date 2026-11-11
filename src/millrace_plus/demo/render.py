"""Regenerated generic display primitives and one escaped standalone HTML template."""

from __future__ import annotations

import copy
from html import escape
from typing import Any

from .contracts import DemoError, validate_candidate

__all__ = ["baseline_display", "build_report", "standalone_html"]


def _amount(value: int | None) -> str:
    return "unknown" if value is None else f"{value} bytes"


def baseline_display(baseline: dict[str, Any]) -> dict[str, Any]:
    hardware = baseline["hardware"]
    memory = hardware["memory"]
    facts = [
        {"label": "Platform", "value": hardware["platform"] or "unknown"},
        {"label": "Architecture", "value": hardware["architecture"] or "unknown"},
        {"label": "Logical CPUs", "value": str(hardware["logical_cpus"] or "unknown")},
        {"label": "Physical RAM", "value": _amount(memory["physical_bytes"])},
        {"label": "Available RAM", "value": _amount(memory["available_bytes"])},
        {
            "label": "Memory placement",
            "value": "one unified pool"
            if memory["unified"]
            else "unified placement unknown"
            if memory["unified"] is None
            else "separate pools",
        },
        {"label": "Throughput / prefill", "value": "unknown; no model run"},
    ]
    for index, gpu in enumerate(hardware["gpus"]):
        facts.append(
            {
                "label": f"GPU {index + 1}",
                "value": f"{gpu['model'] or gpu['device_id'] or 'unknown'}; "
                f"total {_amount(gpu['total_bytes'])}; "
                f"free {_amount(gpu['free_bytes'])}",
            }
        )
    rows = []
    sources = []
    for entry in baseline["entries"]:
        model = entry["reference"]
        for scenario in entry["scenarios"]:
            rows.append(
                [
                    model["name"],
                    scenario["scenario_id"],
                    str(scenario["context_tier"])
                    if scenario["context_tier"] is not None
                    else scenario["fit_status"],
                    _amount(scenario["pool_bytes"]),
                ]
            )
        sources.append(
            {
                "label": model["name"] + " frozen artifacts",
                "url": f"https://huggingface.co/{model['repository']}/tree/{model['revision']}",
            }
        )
    # At most 24 rows per fixed generic table; eight GPUs produce 27 rows.
    tables = [
        {
            "caption": "Provisional estimated total context (prompt plus output)",
            "columns": ["Reference model", "Placement", "Context tokens", "Pool"],
            "rows": rows[offset : offset + 24],
        }
        for offset in range(0, len(rows), 24)
    ]
    return {
        "title": "Hardware and provisional model context",
        "paragraphs": [
            baseline["explanation"],
            "Estimated context; loading and fit are not guaranteed. "
            "Backend compatibility is unverified and multi-device placement "
            "is unsupported.",
        ],
        "facts": facts,
        "tables": tables,
        "sources": sources,
    }


def build_report(
    demo_id: str,
    baseline: dict[str, Any],
    *,
    native: dict[str, Any] | None = None,
    completion: str = "complete",
) -> dict[str, Any]:
    """Keep immutable baseline arithmetic independent from optional native prose."""
    native = copy.deepcopy(
        native
        or {
            "status": "skipped",
            "phase": None,
            "reason": "not_selected",
            "candidate": None,
        }
    )
    display = baseline_display(baseline)
    candidate = native["candidate"]
    display_notes = []
    if candidate is not None:
        validate_candidate(candidate)
        display["paragraphs"].append(candidate["summary"])

        def short(text: str) -> str:
            if len(text) <= 240:
                return text
            display_notes.append("Display text was truncated; full JSON is retained.")
            return text[:200] + "… (truncated; full JSON retained)"

        rows = [
            [item["model_id"], short(item["rationale"])]
            for item in candidate["suggestions"]
        ]
        for measurement in candidate["published_measurements"]:
            rows.append(
                [
                    "Unverified published measurement",
                    short(
                        f"{measurement['model']}: {measurement['value']} "
                        f"{measurement['unit']}; "
                        f"{measurement['hardware']}; {measurement['conditions']}"
                    ),
                ]
            )
        if rows:
            # Reserve the third table for suggestions when eight GPUs need two.
            display["tables"].append(
                {
                    "caption": "Unverified native evidence; "
                    "no new host fit or speed estimate",
                    "columns": ["Suggestion / evidence", "Detail"],
                    "rows": rows,
                }
            )
        display["facts"].append(
            {"label": "Research", "value": candidate["research_status"]}
        )
        seen = {source["url"] for source in display["sources"]}
        for item in candidate["suggestions"]:
            for url in [item["artifact_url"], *item["source_urls"]]:
                if url not in seen:
                    if len(display["sources"]) < 12:
                        display["sources"].append(
                            {"label": "Unverified suggestion source", "url": url}
                        )
                        seen.add(url)
                    else:
                        display_notes.append(
                            "Source display was truncated; full JSON is retained."
                        )
        for measurement in candidate["published_measurements"]:
            url = measurement["source_url"]
            if url not in seen:
                if len(display["sources"]) < 12:
                    display["sources"].append(
                        {"label": "Unverified published measurement", "url": url}
                    )
                    seen.add(url)
                else:
                    display_notes.append(
                        "Source display was truncated; full JSON is retained."
                    )
    display["facts"].append({"label": "Native attempt", "value": native["status"]})
    limitations = list(baseline["limitations"])
    limitations.append(
        "Native source links and published measurements are unverified assertions, "
        "not measurements of this host."
    )
    if completion != "complete":
        display_notes.append(
            f"This run is {completion}; saved facts do not prove writer closure."
        )
    if display_notes:
        display["paragraphs"].append(" ".join(dict.fromkeys(display_notes)))
    return {
        "format": "millrace-demo-report/v1",
        "demo_id": demo_id,
        "baseline": baseline,
        "native": native,
        "completion": completion,
        "limitations": limitations,
        "display": display,
    }


def standalone_html(value: dict[str, Any]) -> str:
    """Render only validated plain display primitives, with no active content."""
    from .contracts import validate_baseline, validate_report

    if value.get("format") == "millrace-demo-baseline/v1":
        validate_baseline(value)
    elif value.get("format") == "millrace-demo-report/v1":
        validate_report(value)
    else:
        raise DemoError("invalid_report_format")
    display = value["display"]
    parts = [
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'\">",
        "<title>" + escape(display["title"]) + "</title>",
        "<style>body{font:16px system-ui;margin:2rem auto;padding:0 1rem;"
        "max-width:80rem;line-height:1.5;color:#17212b}table{border-collapse:"
        "collapse;width:100%;margin:1rem 0}th,td{border:1px solid #bac4ce;"
        "padding:.5rem;text-align:left}dt{font-weight:600}"
        "dd{margin:0 0 .5rem}a{overflow-wrap:anywhere}</style><main>",
        "<h1>" + escape(display["title"]) + "</h1>",
    ]
    parts.extend("<p>" + escape(text) + "</p>" for text in display["paragraphs"])
    parts.append("<dl>")
    for fact in display["facts"]:
        parts.append(
            "<dt>"
            + escape(fact["label"])
            + "</dt><dd>"
            + escape(fact["value"])
            + "</dd>"
        )
    parts.append("</dl>")
    for table in display["tables"]:
        parts.append(
            "<table><caption>" + escape(table["caption"]) + "</caption><thead><tr>"
        )
        parts.extend("<th>" + escape(column) + "</th>" for column in table["columns"])
        parts.append("</tr></thead><tbody>")
        for row in table["rows"]:
            parts.append(
                "<tr>"
                + "".join("<td>" + escape(cell) + "</td>" for cell in row)
                + "</tr>"
            )
        parts.append("</tbody></table>")
    parts.append("<h2>Sources</h2><ul>")
    for source in display["sources"]:
        parts.append(
            '<li><a rel="noreferrer" href="'
            + escape(source["url"], quote=True)
            + '">'
            + escape(source["label"])
            + "</a></li>"
        )
    return "".join(parts) + "</ul></main></html>"
