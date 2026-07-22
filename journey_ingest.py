#!/usr/bin/env python3
"""
journey_ingest.py — discover and ingest user-journey graph files, join them against
per-service scan output, and compute a chaos-engineering priority score per edge.

Discovery is CONTENT-based, not path- or filename-based: any *.json file anywhere
under --root that has top-level "journey", "nodes", and "edges" keys is treated as
a journey file. This is deliberately future-proof — new journeys can live anywhere,
be named anything, and still get picked up without touching this script.

Usage:
    python3 journey_ingest.py --root <root> --scan-output ./scan-output \
                               --out scan-output/journey-registry.json
"""
import argparse, json, os, glob


def is_journey_file(obj):
    return isinstance(obj, dict) and "journey" in obj and "edges" in obj and "nodes" in obj


def load_json_safe(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


# --- criticality / data-class / grade weights -------------------------------
# These weights are proposals, same as the original catalog's Likelihood/Severity
# scale — recalibrate against real incident history over time, don't treat as final.
CRITICALITY_WEIGHT = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
DATA_CLASS_WEIGHT = {"money-movement": 3, "state-mutating": 2, "read-only": 1}
GRADE_WEIGHT = {"red": 3, "yellow": 2, "pending-sme-input": 2, "green": 1}

# --- interaction-family mapping from "via" (open vocabulary — extend freely) -
# Add new via values here as your journeys use them. An unrecognized via never
# gets silently miscategorized — see via_family() below.
VIA_TO_FAMILY = {
    "http": "sync-api",
    "kafka": "messaging-async",
    "event": "unclear-internal-handoff",       # ambiguous transport — needs a human look
    "ui-flow": "user-interaction",
    "rollback-http": "sync-api-compensation",
    "rollback-kafka": "messaging-async-compensation",
    "rollback-event": "unclear-internal-handoff-compensation",
    # anticipated, not yet seen in any journey file — batch trigger patterns
    "s3-event": "batch-file-triggered",
    "eventbridge-schedule": "batch-scheduled",
}


def via_family(via):
    return VIA_TO_FAMILY.get(via, f"UNMAPPED:{via}")


def action_for_grade(grade):
    if grade == "red":
        return "CONFIRMED_ISSUE — file as a defect; add a regression-guard chaos scenario"
    if grade in ("pending-sme-input", "yellow"):
        return "NEEDS_ASSESSMENT — prioritize scanning/SME review before scheduling a chaos scenario"
    if grade == "green":
        return "CONFIRMATORY — periodic chaos re-validation at a cadence set by criticality"
    return "UNKNOWN_GRADE_VALUE — review grading rubric"


def priority_score(criticality, data_class, grade):
    cw = CRITICALITY_WEIGHT.get(criticality)
    dw = DATA_CLASS_WEIGHT.get(data_class)
    gw = GRADE_WEIGHT.get(grade)
    missing = [name for name, v in [("criticality", cw), ("data_class", dw), ("grade", gw)] if v is None]
    if missing:
        return None, missing
    return cw * dw * gw, []


def find_journey_files(root):
    found = []
    for path in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        obj = load_json_safe(path)
        if is_journey_file(obj):
            found.append((path, obj))
    return found


def load_service_registry(scan_output_dir):
    """service_id -> {repo_entry, findings}, keyed by the CANONICAL service id (the
    top-level "service" field each app-scan card declares) — not by repo folder name
    or acronym. Falls back to a weak acronym match only when no canonical id is
    found, and flags that fallback explicitly rather than trusting it silently."""
    registry = load_json_safe(os.path.join(scan_output_dir, "repo-registry.json")) or []
    service_index = {}
    acronym_fallback = {}
    for entry in registry:
        if not entry.get("app_present"):
            continue
        findings_path = os.path.join(scan_output_dir, entry["app_repo"], "findings.json")
        findings = load_json_safe(findings_path)
        canonical_id = findings.get("service") if findings else None
        if canonical_id:
            service_index[canonical_id] = {"repo_entry": entry, "findings": findings}
        acronym_fallback[entry["acronym"]] = entry
    return service_index, acronym_fallback


def resolve_node_service(node_id, service_index, acronym_fallback):
    if node_id in service_index:
        return service_index[node_id], "canonical_id_match"
    for acr, entry in acronym_fallback.items():
        if acr and acr in node_id.replace("-", ""):
            return {"repo_entry": entry, "findings": None}, f"WEAK_ACRONYM_FALLBACK:{acr}"
    return None, "NO_MATCH"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--scan-output", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    journey_files = find_journey_files(args.root)
    service_index, acronym_fallback = load_service_registry(args.scan_output)

    journeys_out = []
    ranked_edges = []
    coverage_gaps = []
    unmapped_vias = set()

    for path, jf in journey_files:
        journey_id = jf.get("journey")
        criticality = jf.get("criticality")
        top_profile = jf.get("profile", {})

        declared_scanned = set(jf.get("scanned_services", []))
        node_scanned = {n["id"] for n in jf.get("nodes", []) if n.get("scanned")}
        if declared_scanned != node_scanned:
            coverage_gaps.append({
                "journey": journey_id, "type": "SCANNED_LIST_MISMATCH",
                "declared_scanned_services": sorted(declared_scanned),
                "nodes_marked_scanned": sorted(node_scanned),
            })

        for node in jf.get("nodes", []):
            resolved, match_type = resolve_node_service(node["id"], service_index, acronym_fallback)
            if resolved is None:
                coverage_gaps.append({
                    "journey": journey_id, "type": "NODE_NOT_IN_ANY_APP_SCAN",
                    "node_id": node["id"], "node_scanned_flag": node.get("scanned"),
                })
            elif match_type != "canonical_id_match":
                coverage_gaps.append({
                    "journey": journey_id, "type": "WEAK_MATCH_ONLY",
                    "node_id": node["id"], "match_type": match_type,
                })

        for edge in jf.get("edges", []):
            via = edge.get("via")
            family = via_family(via)
            if family.startswith("UNMAPPED:"):
                unmapped_vias.add(via)

            grade = edge.get("grade")
            data_class = edge.get("data_class", top_profile.get("data_class"))
            edge_criticality = edge.get("criticality", criticality)

            score, missing = priority_score(edge_criticality, data_class, grade)

            ranked_edges.append({
                "journey": journey_id, "step": edge.get("step"),
                "from": edge.get("from"), "to": edge.get("to"),
                "via": via, "interaction_family": family,
                "grade": grade, "criticality": edge_criticality,
                "data_class": data_class,
                "volume": edge.get("volume", top_profile.get("volume")),
                "priority_score": score, "score_missing_inputs": missing,
                "action": action_for_grade(grade) if grade else "UNKNOWN_GRADE_VALUE — grade field absent",
                "intent": edge.get("intent"),
            })

        journeys_out.append({
            "journey": journey_id, "file": path, "criticality": criticality,
            "profile": top_profile, "node_count": len(jf.get("nodes", [])),
            "edge_count": len(jf.get("edges", [])),
            "unresolved_count": len(jf.get("unresolved", [])),
        })

    ranked_edges.sort(key=lambda r: (r["priority_score"] is None, -(r["priority_score"] or 0)))

    out = {
        "journeys": journeys_out,
        "edges_ranked_by_priority": ranked_edges,
        "coverage_gaps": coverage_gaps,
        "unmapped_via_values": sorted(unmapped_vias),
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Discovered {len(journey_files)} journey file(s), {len(ranked_edges)} edges scored.")
    print(f"Coverage gaps: {len(coverage_gaps)}. Unmapped via values: {sorted(unmapped_vias)}")


if __name__ == "__main__":
    main()
