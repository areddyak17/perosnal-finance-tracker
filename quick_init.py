"""
Run once to create the users table and an admin account.

    python quick_init.py
"""

import sqlite3, getpass, bcrypt, os, sys

DB = "finance.db"
if not os.path.exists(DB):
    sys.exit("❌  finance.db not found – run app.py once first.")

con = sqlite3.connect(DB)
cur = con.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pw_hash  BLOB NOT NULL
);
""")

cur.execute("PRAGMA table_info(transactions);")
if "user_id" not in [c[1] for c in cur.fetchall()]:
    cur.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER DEFAULT 1;")

username = input("Admin username: ").strip()
password = getpass.getpass("Password: ").encode()
cur.execute("INSERT OR IGNORE INTO users (username, pw_hash) VALUES (?,?)",
            (username, bcrypt.hashpw(password, bcrypt.gensalt())))
con.commit()
print("✅  Admin user created.")
