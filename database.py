import sqlite3
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

DB = "cases.db"
SLA_HOURS = {1: 4, 2: 24, 3: 72}

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'agent'
        );

        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            tier INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'medium',
            created_by TEXT NOT NULL,
            assigned_to TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sla_deadline TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            from_tier INTEGER NOT NULL,
            to_tier INTEGER NOT NULL,
            escalated_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            escalated_at TEXT NOT NULL,
            FOREIGN KEY(case_id) REFERENCES cases(id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(case_id) REFERENCES cases(id)
        );
    """)

    # Seed users
    users = [
        ("admin",  generate_password_hash("admin123"),  "admin"),
        ("agent1", generate_password_hash("agent123"),  "agent"),
        ("agent2", generate_password_hash("agent123"),  "agent"),
    ]
    for u in users:
        c.execute("INSERT OR IGNORE INTO users (username,password,role) VALUES (?,?,?)", u)

    # Seed sample cases
    now = datetime.utcnow()
    sample_cases = [
        ("Cannot access Microsoft 365 portal",
         "User reports SSO login failure. Getting AADSTS50126 error.",
         "Authentication", 2, "open", "high", "agent1",
         now - timedelta(hours=10)),
        ("Outlook not syncing emails",
         "Emails not appearing in inbox. Last sync 6 hours ago.",
         "Email", 1, "open", "medium", "agent1",
         now - timedelta(hours=2)),
        ("VPN connection drops intermittently",
         "Remote worker losing VPN every 20-30 minutes. TCP timeout suspected.",
         "Networking", 2, "in_progress", "high", "agent2",
         now - timedelta(hours=18)),
        ("SharePoint permissions error 403",
         "Team unable to access shared document library.",
         "Access Control", 3, "open", "critical", "agent2",
         now - timedelta(hours=5)),
        ("Password reset not working",
         "Self-service password reset portal returns 500 error.",
         "Authentication", 1, "resolved", "medium", "agent1",
         now - timedelta(hours=30)),
        ("Azure VM unreachable after maintenance",
         "VM stopped responding after scheduled maintenance window.",
         "Cloud Infrastructure", 3, "in_progress", "critical", "agent2",
         now - timedelta(hours=48)),
        ("Teams meeting audio not working",
         "User cannot hear others in Teams calls.",
         "Collaboration", 1, "open", "low", "agent1",
         now - timedelta(hours=1)),
        ("DNS resolution failing for internal domain",
         "Internal .corp domain not resolving from branch office.",
         "Networking", 3, "open", "critical", "agent2",
         now - timedelta(hours=36)),
    ]

    for (title, desc, cat, tier, status, priority, assigned, created) in sample_cases:
        deadline = created + timedelta(hours=SLA_HOURS[tier])
        resolved = (created + timedelta(hours=SLA_HOURS[tier] - 1)) if status == "resolved" else None
        c.execute("""
            INSERT OR IGNORE INTO cases
            (title,description,category,tier,status,priority,created_by,assigned_to,
             created_at,updated_at,sla_deadline,resolved_at)
            VALUES (?,?,?,?,?,?,'admin',?,?,?,?,?)
        """, (title, desc, cat, tier, status, priority, assigned,
              created.isoformat(), created.isoformat(),
              deadline.isoformat(),
              resolved.isoformat() if resolved else None))

    # Seed escalations
    c.execute("SELECT id FROM cases WHERE tier=3 LIMIT 2")
    tier3 = c.fetchall()
    for row in tier3:
        c.execute("""
            INSERT OR IGNORE INTO escalations
            (case_id,from_tier,to_tier,escalated_by,reason,escalated_at)
            VALUES (?,2,3,'agent2','Issue requires engineering-level investigation.',?)
        """, (row["id"], (now - timedelta(hours=2)).isoformat()))

    conn.commit()
    conn.close()
    print("Database initialised.")

if __name__ == "__main__":
    init_db()