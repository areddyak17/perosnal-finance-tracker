from flask import Flask, render_template, request, redirect, url_for, session, g, flash
import sqlite3, os, json, datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse

app = Flask(__name__, static_folder='static')
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
DB = os.getenv("DATABASE_URL", "finance.db")

# ------------------ Currencies (USD → local demo rates) ------------------
CURRENCY_RATES = {
    "USD": 1.00, "EUR": 0.93, "GBP": 0.78, "INR": 83.0,
    "AUD": 1.50, "CAD": 1.35, "JPY": 156.0
}
CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "INR": "₹",
    "AUD": "A$", "CAD": "C$", "JPY": "¥"
}
CURRENCY_CHOICES = list(CURRENCY_RATES.keys())

def to_usd(amount_local: float, currency: str) -> float:
    rate = CURRENCY_RATES.get(currency, 1.0)
    return amount_local / rate

def from_usd(amount_usd: float, currency: str) -> float:
    rate = CURRENCY_RATES.get(currency, 1.0)
    return amount_usd * rate

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
        # users (with currency pref)
        c.execute("""
          CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            currency TEXT DEFAULT 'USD'
          )
        """)
        # transactions (user-scoped, amounts stored in USD)
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
        # investments (user-scoped, price stored in USD)
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
        # ---- Auto-migrate old DBs ----
        for table in ("users","transactions","investments"):
            cols = _cols(conn, table)
            if table == "users":
                if "password_hash" not in cols:
                    conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
                if "created_at" not in cols:
                    conn.execute("ALTER TABLE users ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
                if "currency" not in cols:
                    conn.execute("ALTER TABLE users ADD COLUMN currency TEXT DEFAULT 'USD'")
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

# ------------------ Template filters ------------------
@app.template_filter('money')
def money_filter(amount_usd):
    # sqlite3.Row supports dict-style indexing but not .get()
    cur = "USD"
    if getattr(g, "user", None) is not None:
        try:
            user_cur = g.user["currency"]
            if user_cur:
                cur = user_cur
        except Exception:
            pass
    sym = CURRENCY_SYMBOLS.get(cur, "")
    val_local = from_usd(float(amount_usd or 0.0), cur)
    return f"{sym}{val_local:,.2f}"

# ------------------ Insights ------------------
def summarize(txns, invests):
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

    msgs = []
    # savings streak
    monthly_net = [month_inc[i] - month_exp[i] for i in range(12)]
    streak, cur = 0, 0
    for v in monthly_net:
        if v >= 0: cur += 1
        else: cur = 0
        streak = max(streak, cur)
    if streak >= 3:
        msgs.append(f"💪 Savings streak: {streak} months in a row.")

    # spend tilt
    spends = {k: v for k,v in cat_totals.items() if v < 0}
    if spends:
        total_spend = -sum(spends.values())
        worst = min(spends, key=spends.get)
        share = (-spends[worst])/total_spend if total_spend else 0
        if share > 0.4:
            msgs.append(f"⚠️ Spending tilt toward **{worst}** "
                        f"({(-spends[worst]):.0f}$ ≈ {share:.0%} of expenses).")

    # income diversity
    incomes = {k:v for k,v in cat_totals.items() if v > 0}
    inc_total = sum(incomes.values())
    if inc_total > 0:
        diversified = sum(1 for v in incomes.values() if v/inc_total > 0.1)
        if diversified >= 2:
            msgs.append("✅ Multiple solid income sources detected.")
        else:
            msgs.append("📈 Consider diversifying income sources.")

    # portfolio diversification
    vals = [i['shares']*i['price'] for i in invests]
    total_assets = sum(vals)
    if total_assets > 0:
        shares = [(v/total_assets) for v in vals]
        hhi = sum(s*s for s in shares)
        if hhi < 0.4:
            msgs.append("🧩 Portfolio looks diversified.")
        else:
            msgs.append("📌 Portfolio concentration is high; consider more variety.")

    # savings rate last 90 days
    inc90 = sum(t['amount'] for t in last_90 if t['amount'] >= 0)
    exp90 = -sum(t['amount'] for t in last_90 if t['amount'] < 0)
    if inc90:
        rate = (inc90 - exp90) / inc90
        if rate < 0: msgs.append("❗ You spent more than you earned in the last 3 months.")
        elif rate < 0.1: msgs.append("💡 Savings rate <10% in the last 3 months—trim discretionary costs.")
        else: msgs.append(f"👍 Savings rate last 3 months: {rate:.0%}.")
    if not msgs:
        msgs.append("Spending looks balanced. Keep it up!")

    return {
        "balance": balance,
        "category_totals": cat_totals,
        "month_income": month_inc,
        "month_expense": month_exp,
        "insights": msgs
    }

# ------------------ Auth routes ------------------
@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        if not username or not password:
            flash("Username and password are required.")
            return render_template("signup.html")
        pw_hash = generate_password_hash(password)
        try:
            with get_conn() as conn:
                conn.execute("INSERT INTO users(username, password_hash) VALUES (?,?)",
                             (username, pw_hash))
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

# ------------------ Settings (currency) ------------------
@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    if request.method == "POST":
        currency = request.form.get("currency","USD")
        if currency not in CURRENCY_CHOICES:
            flash("Invalid currency.")
        else:
            with get_conn() as conn:
                conn.execute("UPDATE users SET currency=? WHERE id=?", (currency, g.user["id"]))
                conn.commit()
            flash("Settings updated.")
            return redirect(url_for("settings"))
    return render_template("settings.html", currencies=CURRENCY_CHOICES,
                           current=(g.user["currency"] or "USD"))

# ------------------ App routes (user-scoped) ------------------
@app.route("/")
@login_required
def dashboard():
    uid = g.user["id"]
    with get_conn() as conn:
        txns = conn.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY date", (uid,)).fetchall()
        invests = conn.execute("SELECT * FROM investments WHERE user_id=? ORDER BY date", (uid,)).fetchall()

    summary = summarize(txns, invests)
    asset_labels = [i['ticker'] for i in invests]
    asset_values = [i['shares']*i['price'] for i in invests]
    net_worth = summary['balance'] + sum(asset_values)

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
        cur = (g.user["currency"] or "USD") if g.user else "USD"
        date = request.form["date"]
        description = request.form["description"]
        category = request.form["category"]
        amount_local = float(request.form["amount"])
        amount_usd = to_usd(amount_local, cur)
        amt = amount_usd if CATEGORY_MAP.get(category,"income")=="income" else -amount_usd
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO transactions(date,description,category,amount,user_id) VALUES (?,?,?,?,?)",
                (date, description, category, amt, uid)
            ); conn.commit()
        return redirect(url_for("dashboard"))
    return render_template("add.html",
                           categories=[name for name,_ in CATEGORIES],
                           currency=((g.user["currency"] or "USD") if g.user else "USD"))

# ----------- Assets page (list + add) -----------
@app.route("/assets", methods=["GET","POST"])
@login_required
def assets():
    uid = g.user["id"]
    cur = (g.user["currency"] or "USD") if g.user else "USD"
    if request.method == "POST":
        date = request.form["date"]
        ticker = request.form["ticker"].strip()
        shares = float(request.form["shares"])
        price_local = float(request.form["price"])
        price_usd = to_usd(price_local, cur)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO investments(date,ticker,shares,price,user_id) VALUES (?,?,?,?,?)",
                (date, ticker, shares, price_usd, uid)
            ); conn.commit()
        return redirect(url_for("assets"))

    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM investments WHERE user_id=? ORDER BY date DESC", (uid,)).fetchall()

    labels = [r["ticker"] for r in rows]
    values_usd = [r["shares"]*r["price"] for r in rows]
    total_usd = sum(values_usd)

    return render_template(
        "assets.html",
        rows=rows,
        pie_labels=json.dumps(labels),
        pie_values=json.dumps(values_usd),
        total_usd=total_usd
    )

if __name__ == "__main__":
    app.run(debug=True)









