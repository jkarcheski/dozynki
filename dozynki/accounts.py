"""Profiles for a shared server: handle + password, sessions in a cookie, guests by a random id.

Only with DOZYNKI_ACCOUNTS=1; otherwise everyone is DOZYNKI_LABELER, as before.
A profile's handle is the labeler name on its labels. Visitors who haven't signed in label as
"guest:<random id>" (a cookie), which the leaderboard counts together as "guests"; creating a profile
moves that browser's guest work to it. The email is kept only as a salted hash: a reset request
must name the handle and the same address, and the link goes to the address just typed.

For an admin: python -m dozynki.accounts add <handle> [--labels-from <labeler>] | password <handle> | list
"""

import hashlib
import hmac
import re
import secrets
import smtplib
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from . import db
from .config import BASE_URL, SMTP

SESSION_COOKIE = "dozynki_session"
GUEST_COOKIE = "dozynki_guest"
SESSION_DAYS = 30
RESET_MINUTES = 60
HANDLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,31}")
RESERVED = {"anonymous", "guest", "guests", "admin"}
MIN_PASSWORD = 8
SCRYPT = {"n": 2**14, "r": 8, "p": 1}


class AccountError(ValueError):
    pass


# ---------- hashing ----------

def _hash(secret, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _check(secret, stored):
    _, salt, _ = stored.split("$")
    return hmac.compare_digest(_hash(secret, bytes.fromhex(salt)), stored)


_DUMMY = _hash("timing")  # checked against when there is no such user, so both take as long


def _norm_email(email):
    return (email or "").strip().lower()


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _later(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat(timespec="seconds")


# ---------- rate limits (in memory: a restart forgets them) ----------

_attempts = defaultdict(deque)


def _limited(key, n, seconds):
    """True if key has had n attempts in the last `seconds`; otherwise records this one."""
    q, t = _attempts[key], time.time()
    while q and q[0] < t - seconds:
        q.popleft()
    if len(q) >= n:
        return True
    q.append(t)
    return False


# ---------- users ----------

def _user(handle):
    rows = db.query("SELECT * FROM users WHERE handle = ?", (handle,))
    return rows[0] if rows else None


def _validate(handle, password, email):
    if not HANDLE_RE.fullmatch(handle or ""):
        raise AccountError("handle: 2-32 letters, digits, '.', '_' or '-', starting with a letter or digit")
    if handle.lower() in RESERVED:
        raise AccountError(f"{handle!r} is reserved")
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"password: at least {MIN_PASSWORD} characters")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", _norm_email(email)):
        raise AccountError("that doesn't look like an email address")


def _labeler_in_use(handle):
    """Someone has labeled or farmed under this name (e.g. a laptop install's labeler)."""
    return any(db.query(f"SELECT 1 FROM {t} WHERE labeler = ? COLLATE NOCASE LIMIT 1", (handle,))
               for t in ("labels", "purchases", "wearing", "queue_status"))


def create(handle, password, email, adopt=False):
    """New profile. A handle someone already labels under is refused unless adopt (the admin command)."""
    handle = (handle or "").strip()
    _validate(handle, password, email)
    if _user(handle) or (_labeler_in_use(handle) and not adopt):
        raise AccountError("that handle is taken")
    if adopt:  # keep the existing labels' spelling, since labeler names are matched exactly
        rows = db.query("SELECT labeler FROM labels WHERE labeler = ? COLLATE NOCASE LIMIT 1", (handle,))
        handle = rows[0]["labeler"] if rows else handle
    db.execute("INSERT INTO users (handle, password, email_hash, created_at) VALUES (?, ?, ?, ?)",
               (handle, _hash(password), _hash(_norm_email(email)), db.now()))
    return _user(handle)


def claim_guest(guest, handle):
    """Move a guest's labels, queue and any old farm-shop rows to a new profile."""
    if valid_guest(guest):
        move_labeler(guest, handle)


def move_labeler(old, new):
    for table in ("labels", "purchases", "wearing", "queue_status"):
        db.execute(f"UPDATE OR IGNORE {table} SET labeler = ? WHERE COALESCE(labeler, 'anonymous') = ?", (new, old))


def authenticate(handle, password, ip):
    if _limited(("login", ip), 20, 900) or _limited(("login", (handle or "").lower()), 10, 900):
        raise AccountError("too many attempts; try again in 15 minutes")
    user = _user((handle or "").strip())
    if not _check(password or "", user["password"] if user else _DUMMY) or not user:
        raise AccountError("wrong handle or password")
    return user


def set_password(user_id, password):
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"password: at least {MIN_PASSWORD} characters")
    db.execute("UPDATE users SET password = ? WHERE id = ?", (_hash(password), user_id))
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # sign out everywhere


# ---------- sessions ----------

def start_session(user_id):
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
               (_token_hash(token), user_id, db.now(), _later(days=SESSION_DAYS)))
    return token


def session_user(token):
    if not token:
        return None
    rows = db.query("SELECT u.id, u.handle FROM sessions s JOIN users u ON u.id = s.user_id "
                    "WHERE s.token_hash = ? AND s.expires_at > ?", (_token_hash(token), db.now()))
    return rows[0] if rows else None


def end_session(token):
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def new_guest():
    return "guest:" + secrets.token_hex(6)


def valid_guest(value):
    return bool(re.fullmatch(r"guest:[0-9a-f]{12}", value or ""))


# ---------- password reset ----------

def request_reset(handle, email, base_url):
    """Email a reset link if handle and email match a profile. Says nothing either way, so it
    can't be used to find out who has a profile or which address they use."""
    email = _norm_email(email)
    if _limited(("reset", (handle or "").lower()), 3, 3600):
        return
    user = _user((handle or "").strip())
    if not _check(email, user["email_hash"] if user else _DUMMY) or not user:
        return
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
               (_token_hash(token), user["id"], _later(minutes=RESET_MINUTES)))
    # in the fragment, so the token never reaches a server log or a Referer header
    link = f"{BASE_URL or base_url.rstrip('/')}/account#reset={token}"
    _send(email, "DOZYNKI password reset",
          f"Someone (hopefully you) asked to reset the password of the DOZYNKI profile {user['handle']}.\n\n"
          f"Set a new one here within {RESET_MINUTES} minutes:\n{link}\n\nIf it wasn't you, ignore this email.")


def finish_reset(token, password):
    rows = db.query("SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                    (_token_hash(token or ""), db.now()))
    if not rows:
        raise AccountError("this reset link is invalid, used or expired; ask for a new one")
    set_password(rows[0]["user_id"], password)
    db.execute("UPDATE password_resets SET used_at = ? WHERE token_hash = ?", (db.now(), rows[0]["token_hash"]))
    return db.query("SELECT id, handle FROM users WHERE id = ?", (rows[0]["user_id"],))[0]


def _send(to, subject, body):
    if not SMTP["host"]:
        # no mail server configured (a test install): the admin can pass the link on
        print(f"[accounts] no DOZYNKI_SMTP_HOST; reset link for {to}:\n{body}", file=sys.stderr)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP["from"] or SMTP["user"], to, subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP["host"], int(SMTP["port"] or 587), timeout=20) as s:
        s.starttls()
        if SMTP["user"]:
            s.login(SMTP["user"], SMTP["password"])
        s.send_message(msg)


# ---------- admin commands ----------

def main(argv=None):
    import argparse
    import getpass

    parser = argparse.ArgumentParser(prog="python -m dozynki.accounts", description="Manage DOZYNKI profiles")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="create a profile; may take over labels already made under that labeler name")
    add.add_argument("handle")
    add.add_argument("--labels-from", metavar="LABELER",
                     help="also move everything labeled under this name (e.g. 'anonymous' from a laptop install) to the profile")
    pw = sub.add_parser("password", help="set a profile's password (signs it out everywhere)")
    pw.add_argument("handle")
    sub.add_parser("list", help="list profiles")
    args = parser.parse_args(argv)

    if args.cmd == "list":
        for u in db.query("SELECT handle, created_at FROM users ORDER BY handle"):
            print(u["handle"], u["created_at"])
        return
    if args.cmd == "add":
        email = input("email (stored only as a hash, for password resets): ")
        password = getpass.getpass("password: ")
        try:
            user = create(args.handle, password, email, adopt=True)
        except AccountError as e:
            sys.exit(str(e))
        if args.labels_from:
            move_labeler(args.labels_from, user["handle"])
        print(f"created {user['handle']}" + (" with existing labels" if _labeler_in_use(user["handle"]) else ""))
        return
    user = _user(args.handle)
    if not user:
        sys.exit(f"no profile {args.handle!r}")
    try:
        set_password(user["id"], getpass.getpass("new password: "))
    except AccountError as e:
        sys.exit(str(e))
    print("password changed")


if __name__ == "__main__":
    main()
