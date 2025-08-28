from flask import Flask, render_template, request, redirect, url_for, session, g, flash
import sqlite3, os, json, datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse

app = Flask(__name__, static_folder='static')
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
DB = os.getenv("DATABASE_URL", "finance.db")

# ------------------ Categories ------------------
CATEGORIES = [
    ("Salary","income"), ("Freelance","income"), ("Dividends","income"),
    ("Rent","expense"), ("Utilities","expense"), ("Groceries","expense"),
    ("Dining","expense"), ("Shopping","expense"), ("Transport","expense"),
    ("Entertainment","expense"), ("Healthcare","expense"),
]
CATEGORY_MAP = {n:t for n,t in CATEGORIES}

# ------------------ DB helpers ------------------
def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def _cols(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]

def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        # users
        c.execute("""
          CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
          )
        """)
        # transactions (now with user_id)
        c.execute("""
          CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            description TEXT,
            category TEXT,
            amount REAL,
            user_id INTEGER
          )
        """)
        # investments (now with user_id)
        c.execute("""
          CREATE TABLE IF NOT EXISTS investments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            ticker TEXT,
            shares REAL,
            price REAL,
            user_id INTEGER
          )
        """)
        # ---- Auto-migrate old DBs (adds missing cols safely) ----
        for table in ("users","transactions","investments"):
            cols = _cols(conn, table)
            if table == "users":
                if "password_hash" not in cols:
                    conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
                if "created_at" not in cols:
                    conn.execute("ALTER TABLE users ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
            if table in ("transactions","investments"):
                if "user_id" not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
        conn.commit()

with app.app_context():
    init_db()

# ------------------ Auth utils ------------------
def load_logged_in_user():
    uid = session.get("user_id")
    if not uid:
        g.user = None
    else:
        with get_conn() as conn:
            g.user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

@app.before_request
def _before():
    load_logged_in_user()

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

# ------------------ Insights ------------------
def summarize(txns):
    balance = sum(t['amount'] for t in txns)
    cat_totals, month_inc, month_exp = {}, [0]*12, [0]*12
    today = datetime.date.today()
    last_90 = [t for t in txns if (today - datetime.datetime.strptime(t['date'], "%Y-%m-%d").date()).days <= 90]

    for t in txns:
        amt = t['amount']
        d = datetime.datetime.strptime(t['date'], "%Y-%m-%d")
        m = d.month - 1
        if amt >= 0: month_inc[m] += amt
        else:        month_exp[m] += -amt
        cat_totals[t['category']] = cat_totals.get(t['category'], 0) + amt

    insight_msgs = []
    spends = {k:v for k,v in cat_totals.items() if v < 0}
    if spends:
        worst = min(spends, key=spends.get)
        total_spend = -sum(spends.values())
        if total_spend > 0 and (-spends[worst])/total_spend > 0.4:
            insight_msgs.append(f"High spending on **{worst}** ({-spends[worst]:.0f}$ ≈ {(-spends[worst])/total_spend:.0%} of expenses).")
    inc90 = sum(t['amount'] for t in last_90 if t['amount'] >= 0)
    exp90 = -sum(t['amount'] for t in last_90 if t['amount'] < 0)
    if inc90:
        rate = (inc90 - exp90)/inc90
        if   rate < 0:   insight_msgs.append("You spent more than you earned in the last 3 months.")
        elif rate < .1:  insight_msgs.append("Savings rate is below 10 % in the last 3 months—trim discretionary costs.")
        else:            insight_msgs.append(f"Savings rate last 3 months: {rate:.0%}. Good job!")
    if not insight_msgs: insight_msgs.append("Spending looks balanced. Keep it up!")
    return {"balance":balance, "category_totals":cat_totals,
            "month_income":month_inc, "month_expense":month_exp,
            "insights":insight_msgs}

# ------------------ Auth routes ------------------
@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        if not username or not password:
            flash("Username and password are required."); return render_template("signup.html")
        pw_hash = generate_password_hash(password)
        try:
            with get_conn() as conn:
                conn.execute("INSERT INTO users(username,password_hash) VALUES(?,?)",(username,pw_hash))
                conn.commit()
            flash("Account created! Please sign in.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username is taken. Choose another one.")
    return render_template("signup.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        with get_conn() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear(); session["user_id"] = user["id"]
            nxt = request.args.get("next", "/")
            if not nxt or urlparse(nxt).netloc: nxt = url_for("dashboard")
            return redirect(nxt)
        flash("Invalid username or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ------------------ App routes (user-scoped) ------------------
@app.route("/")
@login_required
def dashboard():
    uid = g.user["id"]
    with get_conn() as conn:
        txns = conn.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY date", (uid,)).fetchall()
        invests = conn.execute("SELECT * FROM investments WHERE user_id=? ORDER BY date", (uid,)).fetchall()

    summary = summarize(txns)
    asset_labels = [i['ticker'] for i in invests]
    asset_values = [i['shares']*i['price'] for i in invests]
    net_worth = summary['balance'] + sum(asset_values)

    # expense-only data for pie chart
    exp_labels = [k for k, v in summary['category_totals'].items() if v < 0]
    exp_values = [abs(v) for v in summary['category_totals'].values() if v < 0]

    return render_template(
        "index.html",
        txns=txns, invests=invests, summary=summary, net_worth=net_worth,
        asset_labels=json.dumps(asset_labels), asset_values=json.dumps(asset_values),
        income=json.dumps(summary['month_income']), expense=json.dumps(summary['month_expense']),
        categories=[name for name,_ in CATEGORIES],
        exp_labels=json.dumps(exp_labels), exp_values=json.dumps(exp_values)
    )

@app.route("/add", methods=["GET","POST"])
@login_required
def add():
    if request.method == "POST":
        uid = g.user["id"]
        date = request.form["date"]
        description = request.form["description"]
        category = request.form["category"]
        amount_raw = float(request.form["amount"])
        amt = amount_raw if CATEGORY_MAP.get(category,"income")=="income" else -amount_raw
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO transactions(date,description,category,amount,user_id) VALUES (?,?,?,?,?)",
                (date, description, category, amt, uid)
            ); conn.commit()
        return redirect(url_for("dashboard"))
    return render_template("add.html", categories=[name for name,_ in CATEGORIES])

@app.route("/investments", methods=["POST"])
@login_required
def add_investment():
    uid = g.user["id"]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO investments(date,ticker,shares,price,user_id) VALUES (?,?,?,?,?)",
            (request.form["date"], request.form["ticker"],
             float(request.form["shares"]), float(request.form["price"]), uid)
        ); conn.commit()
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    app.run(debug=True)







