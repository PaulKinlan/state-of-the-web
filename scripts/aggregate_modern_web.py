#!/usr/bin/env python3
"""Aggregate modern-web feature adoption across probe evidence.

Reads the `modern-web-features.json` files written by `scripts/modern_web_probe.py`
and reports how many sites actually use each feature family: `viewTransitions`,
`scrollDrivenAnimations`, `anchorPositioning`, `scrollStateChrome`,
`gesturePlatforms`.

    python3 scripts/aggregate_modern_web.py <evidence-dir> [--json out.json]

THREE THINGS THIS TOOL REFUSES TO DO, because each one produces a confident
wrong number rather than a loud failure:

1. **Aggregate pre-fix evidence.** Until the value-aware fix, the probe matched
   property NAMES and ignored values, so `view-transition-name: none` -- an
   explicit opt-out -- counted as usage, and an ordinary `animation:` shorthand
   counted as scroll-driven. Evidence carrying `matching: "value-aware"` is from
   the fixed probe; evidence without it is not, and its adoption numbers are
   inflated by an unknown amount. Mixing the two generations in one percentage is
   meaningless. Pass `--allow-legacy` to aggregate anyway; every output is then
   labelled `generation: "legacy-inflated"`.

2. **Report one denominator.** A site whose probe failed was never judged, and a
   site whose CSS was partly cross-origin was judged on partial evidence. Both
   are reported separately, and every percentage states which denominator it
   used. `adoptionOfJudged` counts sites the probe actually read; `adoptionOfFullyReadable`
   counts only sites where no stylesheet was unreadable.

3. **Treat absence as proof.** `used: false` on a site with unreadable
   stylesheets means "not found in the CSS we could read", not "not used". Those
   sites are counted in `judged` but excluded from `fullyReadable`, and the gap
   between the two percentages is the size of the doubt.

Exit codes: 0 on success, 1 when the evidence cannot be aggregated honestly
(mixed generations, or legacy evidence without `--allow-legacy`), 2 on bad usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

FAMILIES = [
    "viewTransitions",
    "scrollDrivenAnimations",
    "anchorPositioning",
    "scrollStateChrome",
    "gesturePlatforms",
]

VALUE_AWARE = "value-aware"


def find_reports(root: Path) -> list[Path]:
    """Every modern-web-features.json beneath `root`, in stable order."""
    if root.is_file():
        return [root]
    return sorted(root.rglob("modern-web-features.json"))


def classify(report: dict) -> str:
    """Which probe generation wrote this report?

    `matching: "value-aware"` is the marker the fixed probe emits. Its absence
    means property-name matching, which counted opt-outs and shorthand-expanded
    defaults as usage.
    """
    if report.get("matching") == VALUE_AWARE:
        return "value-aware"
    return "legacy"


def family_used(detail: dict) -> bool:
    """Did this family register as used?

    Both generations expose `used`; only the value-aware one is trustworthy, and
    `classify` is what decides whether we are allowed to read it at all.
    """
    return bool(detail.get("used"))


def summarise(reports: list[tuple[Path, dict]], allow_legacy: bool) -> dict:
    generations = Counter()
    failures: list[dict] = []
    judged: list[tuple[Path, dict]] = []

    for path, report in reports:
        if report.get("ok") is not True:
            failures.append({
                "path": str(path),
                "url": report.get("url"),
                "error": report.get("error") or "probe reported ok=false",
            })
            continue
        generations[classify(report)] += 1
        judged.append((path, report))

    present = sorted(generations)
    if len(present) > 1:
        raise SystemExit(
            "evidence mixes probe generations "
            f"({', '.join(f'{name}={generations[name]}' for name in present)}); "
            "adoption percentages across generations are not comparable. "
            "Re-probe the legacy targets, or aggregate each generation separately."
        )
    generation = present[0] if present else "none"
    if generation == "legacy" and not allow_legacy:
        raise SystemExit(
            f"all {generations['legacy']} reports come from the pre-fix probe "
            "(no `matching: \"value-aware\"` marker). That probe matched property "
            "names and ignored values, so opt-outs such as `view-transition-name: "
            "none` and shorthand-expanded defaults such as `animation-timeline: "
            "auto` were counted as usage; its adoption numbers are inflated by an "
            "unknown amount. Re-probe with the current "
            "scripts/probes/modern-web-features.js, or pass --allow-legacy to "
            "aggregate anyway (output is labelled legacy-inflated)."
        )

    fully_readable = [
        (path, report)
        for path, report in judged
        if ((report.get("css") or {}).get("sheets") or {}).get("inaccessible", 0) == 0
    ]

    families: dict[str, dict] = {}
    for family in FAMILIES:
        used_judged = sum(1 for _, report in judged
                          if family_used(((report.get("css") or {}).get("families") or {}).get(family, {})))
        used_readable = sum(1 for _, report in fully_readable
                            if family_used(((report.get("css") or {}).get("families") or {}).get(family, {})))
        entry = {
            "usedSites": used_judged,
            "judged": len(judged),
            "adoptionOfJudged": percent(used_judged, len(judged)),
            "usedSitesFullyReadable": used_readable,
            "fullyReadable": len(fully_readable),
            "adoptionOfFullyReadable": percent(used_readable, len(fully_readable)),
        }
        if generation == VALUE_AWARE:
            # Only the value-aware probe separates a deliberate opt-out from an
            # inert default a shorthand produced. Reporting them is what lets an
            # auditor tell "did not consider the feature" from "switched it off".
            entry["sitesWithDeclaredOptOut"] = sum(
                1 for _, report in judged
                if (((report.get("css") or {}).get("families") or {}).get(family, {})).get("optedOutCount", 0) > 0
            )
            entry["sitesWithInertDefaultsOnly"] = sum(
                1 for _, report in judged
                if (((report.get("css") or {}).get("families") or {}).get(family, {})).get("inertDefaultCount", 0) > 0
                and not family_used(((report.get("css") or {}).get("families") or {}).get(family, {}))
            )
        families[family] = entry

    live_timelines = sum(
        1 for _, report in judged
        if ((report.get("animations") or {}).get("scrollTimelines", 0)
            + (report.get("animations") or {}).get("viewTimelines", 0)) > 0
    )

    return {
        "generation": "legacy-inflated" if generation == "legacy" else generation,
        "trustworthy": generation == VALUE_AWARE,
        "denominators": {
            "reportsFound": len(reports),
            "judged": len(judged),
            "failed": len(failures),
            "fullyReadable": len(fully_readable),
            "partialCss": len(judged) - len(fully_readable),
        },
        "families": families,
        "crossCheck": {
            "sitesWithLiveScrollOrViewTimeline": live_timelines,
            "note": (
                "Live timelines are observed at runtime and survive unreadable CSS, "
                "so they corroborate scrollDrivenAnimations independently of the "
                "stylesheet text."
            ),
        },
        "failures": failures[:20],
        "caveats": caveats(generation, len(judged), len(fully_readable), len(failures)),
    }


def percent(count: int, total: int) -> float | None:
    return None if not total else round(100.0 * count / total, 1)


def caveats(generation: str, judged: int, fully_readable: int, failed: int) -> list[str]:
    notes = []
    if generation != VALUE_AWARE:
        notes.append(
            "Evidence predates the value-aware fix: opt-outs and shorthand-expanded "
            "defaults were counted as usage, so every adoption figure here is an "
            "upper bound, not a measurement."
        )
    if failed:
        notes.append(
            f"{failed} target(s) produced no usable evidence and are excluded from "
            "every percentage. They are not zeros; they are unmeasured."
        )
    if judged and fully_readable < judged:
        notes.append(
            f"{judged - fully_readable} of {judged} judged site(s) served at least one "
            "unreadable cross-origin stylesheet. For those, a non-detection means "
            "'not found in the readable CSS', not 'not used'. Compare "
            "adoptionOfJudged with adoptionOfFullyReadable: the gap is the doubt."
        )
    notes.append(
        "Every percentage states its denominator. A figure of the form 'X% of the "
        "top N sites' is wrong unless N is the judged count above."
    )
    return notes


def render(summary: dict) -> str:
    d = summary["denominators"]
    lines = [
        f"Probe generation : {summary['generation']}"
        + ("" if summary["trustworthy"] else "   <-- NOT A MEASUREMENT, upper bound only"),
        f"Reports found    : {d['reportsFound']}",
        f"Judged           : {d['judged']}   (failed: {d['failed']})",
        f"Fully readable   : {d['fullyReadable']}   (partial CSS: {d['partialCss']})",
        "",
        f"{'family':<24} {'used':>6} {'/judged':>9} {'used':>6} {'/readable':>11}",
        f"{'':<24} {'':>6} {'':>9} {'(full)':>6} {'':>11}",
    ]
    for family in FAMILIES:
        entry = summary["families"][family]
        judged_pct = "n/a" if entry["adoptionOfJudged"] is None else f"{entry['adoptionOfJudged']}%"
        readable_pct = "n/a" if entry["adoptionOfFullyReadable"] is None else f"{entry['adoptionOfFullyReadable']}%"
        lines.append(
            f"{family:<24} {entry['usedSites']:>6} {judged_pct:>9} "
            f"{entry['usedSitesFullyReadable']:>6} {readable_pct:>11}"
        )
    lines += [
        "",
        f"Live scroll/view timelines observed on {summary['crossCheck']['sitesWithLiveScrollOrViewTimeline']} site(s).",
        "",
        "Caveats:",
    ]
    lines += [f"  - {note}" for note in summary["caveats"]]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("evidence", type=Path,
                        help="directory of probe evidence (searched recursively) or a single report")
    parser.add_argument("--json", type=Path, metavar="PATH", help="write the full summary as JSON")
    parser.add_argument("--allow-legacy", action="store_true",
                        help="aggregate pre-fix evidence anyway; output is labelled legacy-inflated")
    args = parser.parse_args(argv[1:])

    if not args.evidence.exists():
        print(f"evidence path not found: {args.evidence}", file=sys.stderr)
        return 2
    paths = find_reports(args.evidence)
    if not paths:
        print(f"no modern-web-features.json under {args.evidence}", file=sys.stderr)
        return 2

    reports = []
    for path in paths:
        try:
            reports.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError) as exc:
            # An unreadable report is missing evidence, never a zero.
            reports.append((path, {"ok": False, "error": f"unreadable evidence: {type(exc).__name__}: {exc}"}))

    summary = summarise(reports, args.allow_legacy)
    print(render(summary))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
