from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash)
from database import get_db, init_db, SLA_HOURS
from werkzeug.security import check_password_hash
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = "casedesk-secret-2026"

# ── helpers ──────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def sla_status(deadline_str, resolved_at_str=None):
    deadline = datetime.fromisoformat(deadline_str)
    if resolved_at_str:
        resolved = datetime.fromisoformat(resolved_at_str)
        return "met" if resolved <= deadline else "breached"
    now = datetime.utcnow()
    if now > deadline:
        return "breached"
    remaining = deadline - now
    if remaining.total_seconds() < 3600:
        return "warning"
    return "ok"

def format_countdown(deadline_str, resolved_at_str=None):
    if resolved_at_str:
        return "Resolved"
    deadline = datetime.fromisoformat(deadline_str)
    now = datetime.utcnow()
    diff = deadline - now
    if diff.total_seconds() < 0:
        secs = abs(int(diff.total_seconds()))
        h, m = divmod(secs // 60, 60)
        return f"Breached {h}h {m}m ago"
    secs = int(diff.total_seconds())
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m}m remaining"

app.jinja_env.globals["sla_status"] = sla_status
app.jinja_env.globals["format_countdown"] = format_countdown

# ── auth ─────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        db.close()
        if user and check_password_hash(user["password"], password):
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── dashboard ─────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    cases = db.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()

    total       = len(cases)
    open_cases  = sum(1 for c in cases if c["status"] == "open")
    in_progress = sum(1 for c in cases if c["status"] == "in_progress")
    resolved    = sum(1 for c in cases if c["status"] == "resolved")
    breached    = sum(1 for c in cases if sla_status(c["sla_deadline"], c["resolved_at"]) == "breached")

    resolved_ids = [c["id"] for c in cases if c["status"] == "resolved"]
    escalated_ids = set()
    if resolved_ids:
        rows = db.execute(
            "SELECT DISTINCT case_id FROM escalations WHERE case_id IN ({})".format(
                ",".join("?" * len(resolved_ids))), resolved_ids).fetchall()
        escalated_ids = {r["case_id"] for r in rows}
    fcr_count = sum(1 for cid in resolved_ids if cid not in escalated_ids)
    fcr_rate  = round((fcr_count / resolved * 100) if resolved else 0)

    tier_counts = {1: 0, 2: 0, 3: 0}
    for c in cases:
        tier_counts[c["tier"]] = tier_counts.get(c["tier"], 0) + 1

    escalations = db.execute(
        "SELECT e.*, c.title FROM escalations e JOIN cases c ON e.case_id=c.id ORDER BY e.escalated_at DESC LIMIT 5"
    ).fetchall()

    db.close()
    return render_template("dashboard.html",
        total=total, open_cases=open_cases, in_progress=in_progress,
        resolved=resolved, breached=breached, fcr_rate=fcr_rate,
        tier_counts=tier_counts, escalations=escalations, cases=cases[:6])

# ── cases list ────────────────────────────────────────────────

@app.route("/cases")
@login_required
def cases():
    db = get_db()
    tier     = request.args.get("tier", "")
    status   = request.args.get("status", "")
    priority = request.args.get("priority", "")

    query = "SELECT * FROM cases WHERE 1=1"
    params = []
    if tier:
        query += " AND tier=?";     params.append(int(tier))
    if status:
        query += " AND status=?";   params.append(status)
    if priority:
        query += " AND priority=?"; params.append(priority)
    query += " ORDER BY created_at DESC"

    all_cases = db.execute(query, params).fetchall()
    db.close()
    return render_template("cases.html", cases=all_cases,
                           tier=tier, status=status, priority=priority)

# ── case detail ───────────────────────────────────────────────

@app.route("/cases/<int:case_id>")
@login_required
def case_detail(case_id):
    db = get_db()
    case        = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    comments    = db.execute("SELECT * FROM comments WHERE case_id=? ORDER BY created_at", (case_id,)).fetchall()
    escalations = db.execute("SELECT * FROM escalations WHERE case_id=? ORDER BY escalated_at", (case_id,)).fetchall()
    db.close()
    if not case:
        flash("Case not found.", "danger")
        return redirect(url_for("cases"))
    return render_template("case_detail.html", case=case,
                           comments=comments, escalations=escalations)

# ── new case ──────────────────────────────────────────────────

@app.route("/cases/new", methods=["GET", "POST"])
@login_required
def new_case():
    if request.method == "POST":
        title    = request.form["title"].strip()
        desc     = request.form["description"].strip()
        category = request.form["category"]
        priority = request.form["priority"]

        tier_map = {"low": 1, "medium": 1, "high": 2, "critical": 3}
        tier     = tier_map.get(priority, 1)
        now      = datetime.utcnow()
        deadline = now + timedelta(hours=SLA_HOURS[tier])

        db = get_db()
        db.execute("""
            INSERT INTO cases (title,description,category,tier,status,priority,
                               created_by,assigned_to,created_at,updated_at,sla_deadline)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (title, desc, category, tier, "open", priority,
              session["user"], session["user"],
              now.isoformat(), now.isoformat(), deadline.isoformat()))
        db.commit()
        db.close()
        flash(f"Case created — Tier {tier} (SLA: {SLA_HOURS[tier]}h).", "success")
        return redirect(url_for("cases"))
    return render_template("new_case.html")

# ── update status ─────────────────────────────────────────────

@app.route("/cases/<int:case_id>/status", methods=["POST"])
@login_required
def update_status(case_id):
    new_status = request.form["status"]
    now = datetime.utcnow().isoformat()
    db  = get_db()
    if new_status == "resolved":
        db.execute("UPDATE cases SET status=?, updated_at=?, resolved_at=? WHERE id=?",
                   (new_status, now, now, case_id))
    else:
        db.execute("UPDATE cases SET status=?, updated_at=? WHERE id=?",
                   (new_status, now, case_id))
    db.commit()
    db.close()
    flash("Status updated.", "success")
    return redirect(url_for("case_detail", case_id=case_id))

# ── escalate ──────────────────────────────────────────────────

@app.route("/cases/<int:case_id>/escalate", methods=["POST"])
@login_required
def escalate(case_id):
    reason = request.form["reason"].strip()
    db     = get_db()
    case   = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()

    if case["tier"] >= 3:
        flash("Already at Tier 3 — contact engineering directly.", "warning")
        db.close()
        return redirect(url_for("case_detail", case_id=case_id))

    new_tier     = case["tier"] + 1
    now          = datetime.utcnow()
    new_deadline = now + timedelta(hours=SLA_HOURS[new_tier])

    db.execute("""
        INSERT INTO escalations (case_id,from_tier,to_tier,escalated_by,reason,escalated_at)
        VALUES (?,?,?,?,?,?)
    """, (case_id, case["tier"], new_tier, session["user"], reason, now.isoformat()))

    db.execute("""
        UPDATE cases SET tier=?, sla_deadline=?, updated_at=?, status='in_progress' WHERE id=?
    """, (new_tier, new_deadline.isoformat(), now.isoformat(), case_id))

    db.commit()
    db.close()
    flash(f"Case escalated to Tier {new_tier}. New SLA: {SLA_HOURS[new_tier]}h.", "success")
    return redirect(url_for("case_detail", case_id=case_id))

# ── add comment ───────────────────────────────────────────────

@app.route("/cases/<int:case_id>/comment", methods=["POST"])
@login_required
def add_comment(case_id):
    body = request.form["body"].strip()
    if body:
        db = get_db()
        db.execute("INSERT INTO comments (case_id,author,body,created_at) VALUES (?,?,?,?)",
                   (case_id, session["user"], body, datetime.utcnow().isoformat()))
        db.execute("UPDATE cases SET updated_at=? WHERE id=?",
                   (datetime.utcnow().isoformat(), case_id))
        db.commit()
        db.close()
    return redirect(url_for("case_detail", case_id=case_id))

# ── run ───────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)