"""Rule-based parts of the pipeline: checks, eligibility, scoring, ranking, reasons.

No AI is used in this file. Same inputs -> same outputs, every time.
"""
import re

# ---------------------------------------------------------------- guards (code)

INJECTION_PATTERNS = [
    r"\b(automated|ai|artificial intelligence|language model|llm|chatbot)\b[^.]{0,80}\b(review|system|evaluat|scor|rank)",
    r"\bignore (all |any )?(previous|prior|above|other) (instructions|criteria|rules)",
    r"\brank (this|it|us|our proposal) (first|1st|highest|top)",
    r"\b(full|maximum|max|perfect) (marks|points|score)",
    r"\bpre-?approved\b",
    r"\b(system|assistant) prompt\b",
]


def scan_for_injection(text):
    """Cheap keyword scan that runs on the raw text BEFORE any AI call.
    It is a second, independent line of defence next to the AI's own
    'embedded_instructions' field."""
    hits = []
    for pattern in INJECTION_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            hits.append(m.group(0))
    return hits


def _norm(s):
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def verify_evidence(extraction, source_text):
    """Every non-null value must come with a quote that really is in the source.
    If not, the value is dropped (set to null) and a flag is raised.
    This is what stops made-up facts from reaching the scores."""
    flags = []
    src = _norm(source_text)
    for name, item in extraction["fields"].items():
        value, quote = item["value"], item["evidence"]
        if value in (None, False):
            continue
        if not quote or _norm(quote) not in src:
            flags.append(f"{name}: value '{value}' has no matching quote in the document; set to empty for human check")
            item["value"] = None
    return flags


RANGES = {
    "total_price_cad": (1_000, 10_000_000),
    "completion_weeks": (1, 104),
    "panel_warranty_years": (0, 50),
    "workmanship_warranty_years": (0, 50),
    "qualifying_installs_count": (0, 1_000),
    "liability_insurance_cad": (0, 100_000_000),
}


def sanity_check(values):
    flags = []
    for name, (lo, hi) in RANGES.items():
        v = values.get(name)
        if v is not None and not (lo <= v <= hi):
            flags.append(f"{name}: value {v} is outside the plausible range {lo}-{hi}")
    return flags


def experience_window_warning(period, award_year, years=5):
    """'since 2020' with a 2026 award means some projects may be older than 5 years."""
    if not period:
        return None
    m = re.search(r"(?:since|from)\s+(\d{4})", period, flags=re.IGNORECASE)
    if m and int(m.group(1)) < award_year - years:
        return (f"Experience period '{period}' reaches back more than {years} years; "
                f"some counted projects may fall outside the window. Verify the project list.")
    return None


# ---------------------------------------------------------------- rules

def _check(value, op, target):
    if value is None:
        return False
    return {"eq": value == target, "gte": value >= target, "lte": value <= target}[op]


def _part_fraction(rule, v, pool_values):
    """Fraction (0..1) of a criterion's points for one value, given the eligible pool."""
    method = rule["method"]
    known = [x for x in pool_values if x is not None]
    if v is None:
        return 0.0
    if method == "higher_is_better":
        best = max(known) if known else 0
        return 0.0 if best <= 0 else v / best
    if method == "lower_is_better_within_limit":
        limit, best = rule["limit"], min(known)
        if v > limit:
            return 0.0
        return 1.0 if limit == best else (limit - v) / (limit - best)
    raise ValueError(f"unknown method {method}")


def score_all(config, extracted, award_year=2026):
    """extracted: {proposal_id: {"values": {...}, "flags": [...], "embedded_instructions": [...], "notes": str}}"""
    results = {}

    # 1. Mandatory criteria and project constraints (pass/fail rules)
    for pid, ex in extracted.items():
        v = ex["values"]
        r = {"proposal": pid, "mandatory": {}, "constraints": {}, "flags": list(ex.get("flags", [])),
             "warnings": [], "flag_short": [], "breakdown": {}, "status": None, "score": None}
        for c in config["criteria"]:
            if c["type"] != "mandatory":
                continue
            passed = all(_check(v.get(cond["field"]), cond["op"], cond["value"]) for cond in c["rule"]["conditions"])
            r["mandatory"][c["id"]] = passed
        for pc in config["project_constraints"]:
            val = v.get(pc["field"])
            r["constraints"][pc["id"]] = "missing" if val is None else ("pass" if val <= pc["max"] else "fail")
            if val is None:
                r["flags"].append(f"{pc['label']}: not stated or not committed in the proposal; ask the bidder to clarify")
                r["flag_short"].append(f"{pc.get('missing_text', 'value not stated')}, so '{pc['label'].lower()}' cannot be confirmed (scored 0 for now)")
        if ex.get("embedded_instructions"):
            r["flags"].append("Possible manipulation: text addressed to reviewers/automated systems asking for a rank or score. Ignored for scoring; needs human review.")
            r["flag_short"].append("contains text telling automated reviewers to rank it first with full marks (ignored for scoring; integrity check needed)")
        w = experience_window_warning(v.get("experience_period_stated"), award_year)
        if w:
            r["warnings"].append(w)
        r["ineligible"] = (not all(r["mandatory"].values())) or ("fail" in r["constraints"].values())
        results[pid] = r

    # 2. Weighted criteria, scored only against the eligible pool
    pool = [pid for pid, r in results.items() if not r["ineligible"]]
    for c in config["criteria"]:
        if c["type"] != "weighted":
            continue
        rule = c["rule"]
        for pid in pool:
            v = extracted[pid]["values"]
            if rule["method"] == "checklist":
                ticks = {f: bool(v.get(f)) for f in rule["fields"]}
                frac = sum(ticks.values()) / len(ticks)
                detail = ticks
            elif rule["method"] == "parts":
                frac, detail = 0.0, {}
                for part in rule["parts"]:
                    pv = [extracted[p]["values"].get(part["field"]) for p in pool]
                    f = _part_fraction(part, v.get(part["field"]), pv)
                    frac += part["share"] * f
                    detail[part["field"]] = v.get(part["field"])
            else:
                pv = [extracted[p]["values"].get(rule["field"]) for p in pool]
                frac = _part_fraction(rule, v.get(rule["field"]), pv)
                detail = {rule["field"]: v.get(rule["field"])}
            results[pid]["breakdown"][c["id"]] = {
                "name": c["name"], "weight": c["weight"], "fraction": round(frac, 4),
                "points": round(frac * c["weight"], 2), "inputs": detail,
            }
    for pid in pool:
        results[pid]["score"] = round(sum(b["points"] for b in results[pid]["breakdown"].values()), 1)

    # 3. Status: ineligible > flagged > ranked. Flags block ranking until a human clears them.
    for r in results.values():
        r["status"] = "ineligible" if r["ineligible"] else ("flagged" if r["flags"] else "ranked")

    # 4. Rank. Tie-break is fixed so order never changes between runs: score, then lower price, then letter.
    ranked = sorted(
        (r for r in results.values() if r["status"] == "ranked"),
        key=lambda r: (-r["score"], extracted[r["proposal"]]["values"].get("total_price_cad") or 0, r["proposal"]),
    )
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    for r in results.values():
        r.setdefault("rank", None)
    return results


# ---------------------------------------------------------------- reasons (template, not AI)

def _fact(cid, b):
    i = b["inputs"]
    if cid == "experience":
        return f"{i['qualifying_installs_count']} installs"
    if cid == "price":
        return f"${i['total_price_cad']:,.0f}"
    if cid == "timeline":
        w = i["completion_weeks"]
        return f"{w:g} wk" if w is not None else "no date"
    if cid == "warranty":
        return f"{i['panel_warranty_years']:g}/{i['workmanship_warranty_years']:g} yr"
    if cid == "team":
        return f"{sum(i.values())}/{len(i)} items"
    return ", ".join(f"{k}={v}" for k, v in i.items())


def reason(r, extracted, config):
    """One-line reason built from the score breakdown, so it can never disagree with the numbers."""
    v = extracted[r["proposal"]]["values"]
    if r["status"] == "ineligible":
        failed = [c["name"] for c in config["criteria"] if c["type"] == "mandatory" and not r["mandatory"].get(c["id"], True)]
        failed += [pc["label"] for pc in config["project_constraints"] if r["constraints"].get(pc["id"]) == "fail"]
        why = " (electrical work by a subcontractor to be confirmed after award, not the bidder's own licence)" if v.get("licence_holder") == "subcontractor" else ""
        return f"Fails mandatory: {'; '.join(failed)}{why}. Not scored, so it does not affect others' price scores."
    b = r["breakdown"]
    best = [f"{cid} ({_fact(cid, x)})" for cid, x in b.items() if x["fraction"] >= 0.999]
    wcid, weak = max(b.items(), key=lambda kv: kv[1]["weight"] - kv[1]["points"])
    lost = weak["weight"] - weak["points"]
    lead = f"full marks on {', '.join(best)}; " if best else ""
    core = f"{r['score']}/100: {lead}biggest loss is {wcid} ({_fact(wcid, weak)}, -{lost:.1f})."
    if r["status"] == "flagged":
        return f"Held for reviewer: {'; '.join(r['flag_short'])}. Provisional {core}"
    return core
