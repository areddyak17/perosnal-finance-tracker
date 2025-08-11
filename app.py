# app.py — Personal Finance Tracker (self-healing schema + UX + goals)
import sqlite3, bcrypt
from datetime import date
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, g
from flask_login import (
    LoginManager, UserMixin, login_user, login_required, logout_user, current_user
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-me-in-prod"
DB = Path(__file__).with_name("finance.db")

INCOME_CATEGORIES  = ["Salary", "Bonus", "Interest", "Investment Income", "Other Income"]
EXPENSE_CATEGORIES = ["Food", "Rent", "Utilities", "Transport", "Shopping", "Health", "Entertainment", "Travel", "Misc"]

# ----------------------------- Auth -----------------------------
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, id_, username):
        self.id = id_; self.username = username
    @staticmethod
    def get(user_id):
        row = get_db().execute("SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone()
        return User(row["id"], row["username"]) if row else None

@login_manager.user_loader
def load_user(user_id): return User.get(int(user_id))

# ----------------------------- DB -------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    if (db := g.pop("db", None)) is not None: db.close()

def ensure_schema():
    """Create any missing tables/columns. Safe to run repeatedly."""
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      pw_hash BLOB NOT NULL
    );
    CREATE TABLE IF NOT EXISTS transactions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      date DATE NOT NULL,
      description TEXT NOT NULL,
      category TEXT NOT NULL,
      amount REAL NOT NULL,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS user_settings (
      user_id INTEGER PRIMARY KEY,
      savings_goal REAL NOT NULL DEFAULT 5000,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)
    con.commit(); con.close()

# Run once at import time
ensure_schema()

# --------------------------- Helpers ----------------------------
def get_summary():
    row = get_db().execute("""
        SELECT
          COALESCE(SUM(CASE WHEN amount > 0 THEN amount END),0)  AS income,
          COALESCE(SUM(CASE WHEN amount < 0 THEN amount END),0)  AS expenses,
          COALESCE(SUM(amount),0)                                AS balance
        FROM transactions WHERE user_id=?""", (current_user.id,)
    ).fetchone()
    return {"income": row["income"], "expenses": abs(row["expenses"]), "balance": row["balance"]}

def get_recent_transactions(limit=10):
    return get_db().execute("""
        SELECT id, date, description, category, amount
        FROM transactions WHERE user_id=? ORDER BY date DESC, id DESC LIMIT ?""",
        (current_user.id, limit)
    ).fetchall()

def get_category_breakdown():
    rows = get_db().execute("""
        SELECT category, ABS(SUM(amount)) total
        FROM transactions WHERE user_id=? AND amount<0
        GROUP BY category ORDER BY total DESC""", (current_user.id,)
    ).fetchall()
    return [r["category"] for r in rows], [r["total"] for r in rows]

def get_monthly_income_expenses():
    rows = get_db().execute("""
        SELECT strftime('%Y-%m', date) AS m,
               SUM(CASE WHEN amount>0 THEN amount ELSE 0 END) AS income,
               ABS(SUM(CASE WHEN amount<0 THEN amount ELSE 0 END)) AS expenses
        FROM transactions WHERE user_id=? GROUP BY m ORDER BY m""", (current_user.id,)
    ).fetchall()
    return [r["m"] for r in rows], [r["income"] for r in rows], [r["expenses"] for r in rows]

def get_recent_categories(n=6):
    rows = get_db().execute("""
        SELECT DISTINCT category FROM transactions
        WHERE user_id=? ORDER BY id DESC LIMIT ?""", (current_user.id, n)
    ).fetchall()
    return [r["category"] for r in rows]

def get_savings_goal():
    row = get_db().execute("SELECT savings_goal FROM user_settings WHERE user_id=?", (current_user.id,)).fetchone()
    return float(row["savings_goal"]) if row else 5000.0

def set_savings_goal(value: float):
    get_db().execute("""
        INSERT INTO user_settings (user_id, savings_goal)
        VALUES (?,?)
        ON CONFLICT(user_id) DO UPDATE SET savings_goal=excluded.savings_goal
    """, (current_user.id, value))
    get_db().commit()

def ensure_user_settings_row(user_id: int):
    get_db().execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    get_db().commit()

def build_ai_insights():
    labels, vals = get_category_breakdown()
    tips = []
    if labels:
        total = sum(vals) or 1
        top, tv = labels[0], vals[0]
        share = round(100 * tv / total)
        tips.append(f"{'⚠️' if share>=50 else '👍'} {'High concentration in' if share>=50 else 'Balanced spending. Largest is'} <strong>{top}</strong> ({share}%).")
    s = get_summary()
    if s["income"]:
        rate = max(0, round(100*(s["income"]-s["expenses"])/max(s["income"],1)))
        tips.append(f"Savings rate this period: <strong>{rate}%</strong>.")
    if s["balance"] < 0:
        tips.append("Balance is negative. Reduce discretionary spending this week.")
    return tips or ["Add a few transactions to unlock insights."]

# ---------------------------- Routes ----------------------------
@app.route("/")
@login_required
def index():
    # make sure the settings row exists for this user
    ensure_user_settings_row(current_user.id)

    summary = get_summary()
    recent = get_recent_transactions(10)
    cat_labels, cat_values = get_category_breakdown()
    months, m_inc, m_exp = get_monthly_income_expenses()
    ai = build_ai_insights()

    goal = get_savings_goal()
    pct = 0 if goal <= 0 else max(0, min(100, (summary["balance"]/goal)*100))

    return render_template("index.html",
        summary=summary, net_worth=summary["balance"], recent=recent,
        cat_labels=cat_labels, cat_values=cat_values,
        months=months, monthly_income=m_inc, monthly_expenses=m_exp,
        ai_insights=ai, savings_goal=goal, savings_percent=pct
    )

@app.route("/set-goal", methods=["POST"])
@login_required
def set_goal():
    try:
        goal = float(request.form.get("savings_goal","0"))
    except ValueError:
        flash("Enter a valid goal amount.", "warning"); return redirect(url_for("index"))
    if goal <= 0:
        flash("Goal must be greater than zero.", "warning"); return redirect(url_for("index"))
    set_savings_goal(goal); flash("Savings goal updated.", "success")
    return redirect(url_for("index"))

# ---- Auth
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u, p = request.form["username"].strip(), request.form["password"]
        row = get_db().execute("SELECT id, pw_hash FROM users WHERE username=?", (u,)).fetchone()
        if row and bcrypt.checkpw(p.encode(), row["pw_hash"]):
            user = User(row["id"], u); login_user(user)
            ensure_user_settings_row(user.id)
            flash("Welcome back!", "success"); return redirect(url_for("index"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        u, p = request.form["username"].strip(), request.form["password"]
        try:
            get_db().execute("INSERT INTO users (username, pw_hash) VALUES (?,?)",
                             (u, bcrypt.hashpw(p.encode(), bcrypt.gensalt())))
            uid = get_db().execute("SELECT id FROM users WHERE username=?", (u,)).fetchone()["id"]
            ensure_user_settings_row(uid)
            flash("Account created. Please log in.", "info"); return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already taken.", "warning")
    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    logout_user(); flash("Signed out.", "info"); return redirect(url_for("login"))

# ---- Add/Delete Transactions
@app.route("/add", methods=["GET","POST"])
@login_required
def add_transaction():
    if request.method=="POST":
        date_    = request.form["date"]
        desc     = request.form["description"].strip()
        category = request.form["category"]
        amount   = float(request.form["amount"])
        # Normalize sign
        if category in EXPENSE_CATEGORIES and amount > 0: amount = -amount
        if category in INCOME_CATEGORIES and amount < 0:  amount = abs(amount)
        get_db().execute("""
            INSERT INTO transactions (user_id, date, description, category, amount)
            VALUES (?,?,?,?,?)""", (current_user.id, date_, desc, category, amount))
        get_db().commit()
        flash("Transaction added!", "success")
        return redirect(url_for("index"))
    return render_template("add.html",
        income_categories=INCOME_CATEGORIES,
        expense_categories=EXPENSE_CATEGORIES,
        recent_categories=get_recent_categories(),
        today=date.today().isoformat()
    )

@app.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete_transaction(id):
    get_db().execute("DELETE FROM transactions WHERE id=? AND user_id=?", (id, current_user.id))
    get_db().commit(); flash("Transaction deleted.", "info")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=False)




