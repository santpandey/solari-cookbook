"""SQLite history store for audit runs and run-to-run diffs.

Every audit is persisted to audits/history.db (one row per run, full report
JSON blob) so a new run can be diffed against the same target's previous
run — that's how we notice "a deployment changed something" on a site we
don't control. Findings are aggregated per `type`, which is the stable key
for comparing runs.
"""

import json
import pathlib
import sqlite3

from .paths import audits_dir

def _default_db():
    return audits_dir() / "history.db"


def init_db(path=None):
    """Create the runs table + (target, ts) index; returns the db Path."""
    p = pathlib.Path(path) if path else _default_db()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "target TEXT NOT NULL, "
            "ts TEXT NOT NULL, "
            "issue_count INTEGER NOT NULL DEFAULT 0, "
            "report_json TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_target_ts "
            "ON runs(target, ts)"
        )
        conn.commit()
    finally:
        conn.close()
    return p


def save_run(report, path=None):
    """Insert a report dict; returns the new run id."""
    p = init_db(path)
    conn = sqlite3.connect(p)
    try:
        # bulky/ephemeral keys live in raw.json.gz, not in the history blob
        slim = {k: v for k, v in report.items()
                if k not in ("pages", "raw_findings", "diff")}
        cur = conn.execute(
            "INSERT INTO runs (target, ts, issue_count, report_json) "
            "VALUES (?, ?, ?, ?)",
            (report["target"], report["timestamp"],
             report.get("issue_count", len(report.get("findings", []))),
             json.dumps(slim, default=str)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def previous_run(target, before_ts=None, path=None):
    """Most recent stored report for `target` (optionally strictly before
    `before_ts`), parsed back to a dict; None when no such run exists."""
    conn = _connect(path)
    if conn is None:
        return None
    try:
        if before_ts:
            row = conn.execute(
                "SELECT report_json FROM runs "
                "WHERE target = ? AND ts < ? "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (target, before_ts)).fetchone()
        else:
            row = conn.execute(
                "SELECT report_json FROM runs WHERE target = ? "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (target,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def list_runs(target=None, path=None):
    """id/target/ts/issue_count summaries, newest first; all targets when
    `target` is None."""
    conn = _connect(path)
    if conn is None:
        return []
    try:
        if target:
            rows = conn.execute(
                "SELECT id, target, ts, issue_count FROM runs "
                "WHERE target = ? ORDER BY ts DESC, id DESC",
                (target,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, target, ts, issue_count FROM runs "
                "ORDER BY ts DESC, id DESC").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [{"id": r[0], "target": r[1], "ts": r[2], "issue_count": r[3]}
            for r in rows]


def diff_runs(old, new):
    """Diff two report dicts. `type` is the stable finding key; numeric
    evidence values are flagged when they move more than 20%."""
    old_f = {f["type"]: f for f in old.get("findings", [])}
    new_f = {f["type"]: f for f in new.get("findings", [])}
    shared = sorted(old_f.keys() & new_f.keys())

    sev_changes = [
        {"type": t, "old": old_f[t].get("severity"),
         "new": new_f[t].get("severity")}
        for t in shared
        if old_f[t].get("severity") != new_f[t].get("severity")
    ]

    metric_deltas = []
    for t in shared:
        ov = old_f[t].get("evidence", {}).get("value")
        nv = new_f[t].get("evidence", {}).get("value")
        if not (isinstance(ov, (int, float)) and
                isinstance(nv, (int, float))):
            continue
        if ov == nv:
            continue
        pct = round((nv - ov) / abs(ov) * 100, 1) if ov else None
        if pct is None or abs(pct) > 20:
            metric_deltas.append({
                "type": t,
                "metric": new_f[t].get("evidence", {}).get("metric")
                          or old_f[t].get("evidence", {}).get("metric"),
                "old_value": ov, "new_value": nv, "pct_change": pct,
            })

    old_scores = old.get("category_scores", {})
    new_scores = new.get("category_scores", {})
    score_deltas = {
        c: (old_scores.get(c), new_scores.get(c))
        for c in sorted(set(old_scores) | set(new_scores))
        if old_scores.get(c) != new_scores.get(c)
    }

    old_tech = {_tech_name(t) for t in old.get("technologies", [])}
    new_tech = {_tech_name(t) for t in new.get("technologies", [])}

    new_findings = sorted(set(new_f) - set(old_f))
    resolved = sorted(set(old_f) - set(new_f))

    return {
        "target": new.get("target") or old.get("target"),
        "old_ts": old.get("timestamp"),
        "new_ts": new.get("timestamp"),
        "new_findings": new_findings,
        "resolved_findings": resolved,
        "severity_changes": sev_changes,
        "metric_deltas": metric_deltas,
        "score_deltas": score_deltas,
        "tech_added": sorted(new_tech - old_tech),
        "tech_removed": sorted(old_tech - new_tech),
        "summary": _summary(new_findings, resolved, sev_changes,
                            metric_deltas),
    }


def diff_latest(target, current_report, path=None):
    """Diff `current_report` against the previous stored run for `target`
    (excluding anything at/after the current run's own timestamp); None
    when there is no earlier run to compare against."""
    prev = previous_run(target, before_ts=current_report.get("timestamp"),
                        path=path)
    if prev is None:
        return None
    return diff_runs(prev, current_report)


def _connect(path):
    p = pathlib.Path(path) if path else _default_db()
    if not p.exists():
        return None
    return sqlite3.connect(p)


def _tech_name(t):
    return t.get("name") if isinstance(t, dict) else t


def _summary(new_findings, resolved, sev_changes, metric_deltas):
    parts = []
    for d in metric_deltas:
        if d["pct_change"] is None:
            parts.append(f"{d['metric']} appeared (0 → {d['new_value']})")
        else:
            direction = "regressed" if d["pct_change"] > 0 else "improved"
            parts.append(f"{d['metric']} {direction} "
                         f"{abs(d['pct_change']):.0f}%")
    if new_findings:
        n = len(new_findings)
        parts.append(f"{n} new issue{'s' if n != 1 else ''}")
    if resolved:
        parts.append(f"{len(resolved)} resolved")
    if sev_changes:
        n = len(sev_changes)
        parts.append(f"{n} severity change{'s' if n != 1 else ''}")
    return ", ".join(parts) if parts else "no changes"
