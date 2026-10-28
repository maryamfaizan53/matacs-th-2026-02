"""Run the triage pipeline on the sample set.

  python run.py              # offline: uses the golden extractions in fixtures/ (no API key needed)
  python run.py --live       # live: sends proposals/A.txt ... E.txt to the model, checks, then scores
  python run.py --weights price=40,experience=15   # what-if: change weights and re-score (no AI calls)
  python run.py --budget 240000                    # what-if: change the budget ceiling
  python run.py --set D.completion_weeks=12        # what-if: a reviewer records a bidder's clarification

Outputs go to output/: ranking.json, report.md (reviewer view) and audit.json.
"""
import argparse
import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pipeline import reason, sanity_check, scan_for_injection, score_all, verify_evidence
from prompts import PROMPT_VERSION

HERE = Path(__file__).parent
CRITERIA = HERE / "criteria" / "commercial_solar_v1.json"
FIXTURE = HERE / "fixtures" / "extractions_v1.json"
OUT = HERE / "output"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_offline():
    data = json.loads(FIXTURE.read_text())
    extracted, audit = {}, {}
    for pid, p in data["proposals"].items():
        extracted[pid] = {
            "values": p["fields"],
            "flags": sanity_check(p["fields"]),
            "embedded_instructions": p["embedded_instructions"],
            "notes": p["notes"],
        }
        audit[pid] = {"source": "fixture", "prompt_version": data["prompt_version"]}
    return extracted, audit


def load_live(config):
    from extract import extract  # imported here so offline mode needs no API key

    extracted, audit = {}, {}
    for pid in "ABCDE":
        path = HERE / "proposals" / f"{pid}.txt"
        text = path.read_text()
        cache = OUT / "cache" / f"{pid}-{sha(text)}-{PROMPT_VERSION}-v{config['criteria_version']}.json"
        if cache.exists():  # same document + prompt + criteria -> reuse the stored extraction
            raw = json.loads(cache.read_text())
        else:
            raw = extract(config, pid, text)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(raw, indent=2))
        checked = copy.deepcopy(raw)
        flags = verify_evidence(checked, text)
        values = {k: item["value"] for k, item in checked["fields"].items()}
        flags += sanity_check(values)
        rule_hits = scan_for_injection(text)
        instructions = checked["embedded_instructions"] + [f"[keyword scan] {h}" for h in rule_hits]
        extracted[pid] = {"values": values, "flags": flags,
                          "embedded_instructions": instructions, "notes": checked["notes"]}
        audit[pid] = {"source": "live", "document_sha256": sha(text), "prompt_version": PROMPT_VERSION,
                      "model": os.getenv("OPENAI_MODEL"), "raw_model_output": raw, "keyword_scan_hits": rule_hits}
    return extracted, audit


def apply_weights(config, spec):
    """Weights must still add up to 100, the same check the admin screen would enforce."""
    for pair in spec.split(","):
        cid, w = pair.split("=")
        next(c for c in config["criteria"] if c["id"] == cid.strip())["weight"] = float(w)
    total = sum(c["weight"] for c in config["criteria"] if c["type"] == "weighted")
    if abs(total - 100) > 1e-9:
        raise SystemExit(f"Weights add up to {total}, not 100. Rejected.")
    config["criteria_version"] = f"{config['criteria_version']}-whatif"


def report_md(results, extracted, config):
    lines = [f"# Reviewer view: {config['category_name']} (criteria v{config['criteria_version']})", ""]
    order = sorted(results.values(), key=lambda r: ({"ranked": 0, "flagged": 1, "ineligible": 2}[r["status"]],
                                                    r["rank"] or 99, -(r["score"] or 0)))
    lines += ["| Proposal | Status | Rank | Score | " + " | ".join(c["name"] + f" ({c['weight']})" for c in config["criteria"] if c["type"] == "weighted") + " |",
              "|---" * (4 + sum(c["type"] == "weighted" for c in config["criteria"])) + "|"]
    for r in order:
        cells = [r["proposal"], r["status"], str(r["rank"] or "-"), str(r["score"] if r["score"] is not None else "-")]
        for c in config["criteria"]:
            if c["type"] == "weighted":
                b = r["breakdown"].get(c["id"])
                cells.append(f"{b['points']}" if b else "-")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    for r in order:
        lines += [f"## Proposal {r['proposal']}: {r['status']}", "", reason(r, extracted, config), ""]
        lines.append("Mandatory: " + ", ".join(f"{k} {'PASS' if ok else 'FAIL'}" for k, ok in r["mandatory"].items()))
        lines.append("Project constraints: " + ", ".join(f"{k} {s.upper()}" for k, s in r["constraints"].items()))
        for cid, b in r["breakdown"].items():
            lines.append(f"- {b['name']}: {b['points']} / {b['weight']} (inputs: {b['inputs']})")
        for f in r["flags"]:
            lines.append(f"- FLAG: {f}")
        for w in r["warnings"]:
            lines.append(f"- WARNING: {w}")
        if extracted[r["proposal"]]["notes"]:
            lines.append(f"- Extraction notes: {extracted[r['proposal']]['notes']}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--weights", help="what-if weights, e.g. price=40,experience=15")
    ap.add_argument("--budget", type=float, help="what-if budget ceiling in CAD")
    ap.add_argument("--set", action="append", default=[], help="human correction, e.g. D.completion_weeks=12")
    args = ap.parse_args()

    config = json.loads(CRITERIA.read_text())
    if args.weights:
        apply_weights(config, args.weights)
    if args.budget:
        next(p for p in config["project_constraints"] if p["id"] == "budget_ceiling")["max"] = args.budget
        next(c for c in config["criteria"] if c["id"] == "price")["rule"]["limit"] = args.budget
        config["criteria_version"] = f"{config['criteria_version']}-whatif"

    extracted, audit = load_live(config) if args.live else load_offline()
    for item in args.set:  # a human override: recorded in the audit log with old and new value
        key, val = item.split("=")
        pid, field = key.split(".")
        old = extracted[pid]["values"].get(field)
        new = json.loads(val) if val not in ("", "null") else None
        extracted[pid]["values"][field] = new
        audit[pid].setdefault("human_corrections", []).append(
            {"field": field, "old": old, "new": new, "by": "reviewer (demo)", "reason": "bidder clarification"})
    results = score_all(config, extracted)

    ranking = []
    for r in sorted(results.values(), key=lambda r: (r["rank"] is None, r["rank"] or 0, r["proposal"])):
        ranking.append({"proposal": r["proposal"], "rank": r["rank"], "status": r["status"],
                        "score": r["score"] if r["status"] != "ineligible" else None,
                        "reason": reason(r, extracted, config)})

    OUT.mkdir(exist_ok=True)
    (OUT / "ranking.json").write_text(json.dumps(ranking, indent=2))
    (OUT / "report.md").write_text(report_md(results, extracted, config))
    (OUT / "audit.json").write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "criteria_version": config["criteria_version"],
        "criteria_sha256": sha(json.dumps(config, sort_keys=True)),
        "per_proposal": audit,
        "results": results,
        "human_decisions": [],
    }, indent=2, default=str))
    print(json.dumps(ranking, indent=2))


if __name__ == "__main__":
    main()
