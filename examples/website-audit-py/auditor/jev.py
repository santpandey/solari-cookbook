"""Optional judgment layer: TypeSafe AI's Jev (System One model).

Rules detect; Jev judges. For each unique finding type it asks three typed
questions in ONE request:

  choice  — which impact area best fits (probability distribution)
  score   — contextual severity (is this a real problem *here*)
  noul    — is this a real user-facing problem worth reporting

It only writes `impact` / `severity` / a `jev` details marker — never
`verification`: a model probability is a prior, not proof.

Requires TYPESAFE_API_KEY. Absent the key or on any error, findings keep the
deterministic IMPACT_TABLE values — zero behavior change.

API: POST https://api.typesafe.ai/v1/systemone
     {model, state, questions:{name:{type,instructions,criteria}}}
"""

import os

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

IMPACT_CHOICES = {
    "customer_churn": "visitors leave, bounce, or don't come back",
    "seo": "lost rankings, crawlability, or search visibility",
    "conversion": "fewer signups, purchases, or enquiries completed",
    "accessibility": "excludes users with disabilities; compliance exposure",
    "security": "data exposure, interception, or trust warnings",
    "cost": "wasted bandwidth, compute, or infrastructure spend",
}

SEVERITY_LEVELS = [
    "cosmetic — invisible to visitors",
    "noticeable — a visitor might feel it",
    "serious — measurably hurts a real task",
    "critical — blocks or breaks a real task",
]
SEVERITY_MAP = {0: "low", 1: "medium", 2: "high", 3: "critical"}


def enabled():
    return bool(os.environ.get("TYPESAFE_API_KEY"))


async def _ask(state):
    import httpx
    payload = {
        "model": JEV_MODEL,
        "state": state,
        "questions": {
            "impact_area": {
                "type": "choice",
                "instructions": "For a small-business website, which business "
                                "impact best fits this issue?",
                "criteria": IMPACT_CHOICES,
            },
            "severity": {
                "type": "score",
                "instructions": "How severe is this issue for a visitor trying "
                                "to use the site?",
                "criteria": SEVERITY_LEVELS,
            },
            "real_problem": {
                "type": "noul",
                "instructions": "Is this a real user-facing problem worth "
                                "reporting to the site owner, rather than a "
                                "technicality or false positive?",
            },
        },
    }
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(
            JEV_URL,
            headers={
                "Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        return r.json()


def _apply(finding, answers):
    impact_ans = answers.get("impact_area", {})
    sev_ans = answers.get("severity", {})
    real_ans = answers.get("real_problem", {})

    if impact_ans.get("choice") in IMPACT_CHOICES:
        finding["impact"]["area"] = impact_ans["choice"]
        finding["impact"]["confidence"] = round(
            float(impact_ans.get("confidence", 0)), 3)
        finding["impact"]["distribution"] = impact_ans.get("probabilities")

    sev = sev_ans.get("score")
    if isinstance(sev, (int, float)):
        idx = max(0, min(3, round(sev)))
        finding["severity"] = SEVERITY_MAP[idx]

    noul = real_ans.get("noul")
    if isinstance(noul, (int, float)):
        finding.setdefault("jev", {})
        finding["jev"]["real_problem_p"] = round(float(noul), 3)
        if noul < 0.4:
            finding["verification"]["result"] = "jev_low_confidence"
            finding["verification"]["reproducible"] = False


async def enrich_findings(findings, site_context=None):
    """Score one representative finding per type (impact is per-issue-type;
    per-occurrence calls would just multiply cost). Mutates findings in place.
    Returns a summary dict for verification_summary."""
    if not enabled() or not findings:
        return {"jev": "disabled"}

    by_type = {}
    for f in findings:
        by_type.setdefault(f["type"], f)

    used_model = None
    scored = 0
    for ftype, rep in by_type.items():
        state = {
            "finding_type": ftype,
            "category": rep["category"],
            "message": rep["evidence"].get("message", ""),
            "metric": rep["evidence"].get("metric"),
            "value": rep["evidence"].get("value"),
            "page_url": rep["evidence"].get("urls", [""])[0],
            "site": site_context or {},
        }
        try:
            resp = await _ask(state)
        except Exception:
            continue  # stay on deterministic values
        used_model = resp.get("model", JEV_MODEL)
        answers = resp.get("answers", {})
        for f in findings:
            if f["type"] == ftype:
                _apply(f, answers)
        scored += 1

    return {"jev": used_model, "types_scored": scored}
