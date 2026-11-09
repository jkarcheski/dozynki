"""Posting to a Slack channel through an incoming webhook (daily reports, daily.py).

Each labeler enters their channel's webhook on the Daily page. A webhook lets anyone who has it post to the
channel, so it is stored encrypted, like YSE PZ passwords (brokers/yse.py): a copy of the database alone doesn't
reveal it, but anyone who can read the data directory can. DOZYNKI_SLACK_WEBHOOK in .env still works for a
labeler who hasn't entered one.
"""

import requests

from . import db
from .brokers import yse
from .config import HTTP_TIMEOUT, SLACK_WEBHOOKS

WEBHOOK_PREFIX = "https://hooks.slack.com/"  # nothing else gets the report


def _check(webhook):
    if not webhook.startswith(WEBHOOK_PREFIX):
        raise ValueError(f"not a Slack webhook (should start with {WEBHOOK_PREFIX})")


def save_webhook(labeler, webhook):
    webhook = (webhook or "").strip()
    _check(webhook)
    db.execute("INSERT OR REPLACE INTO slack_webhooks (labeler, webhook, saved_at) VALUES (?, ?, ?)",
               (labeler, yse._fernet().encrypt(webhook.encode()).decode(), db.now()))


def forget_webhook(labeler):
    db.execute("DELETE FROM slack_webhooks WHERE labeler = ?", (labeler,))


def webhook_for(labeler):
    """The labeler's webhook: the one they entered, else theirs in .env, else None."""
    rows = db.query("SELECT webhook FROM slack_webhooks WHERE labeler = ?", (labeler,))
    if rows:
        return yse._fernet().decrypt(rows[0]["webhook"].encode()).decode()
    return SLACK_WEBHOOKS.get(labeler.lower())


def where(labeler):
    """Where the webhook comes from, for the page: "saved", "env" or None. The URL itself never goes back out."""
    if db.query("SELECT 1 FROM slack_webhooks WHERE labeler = ?", (labeler,)):
        return "saved"
    return "env" if labeler.lower() in SLACK_WEBHOOKS else None


def post(webhook, text):
    _check(webhook)
    r = requests.post(webhook, json={"text": text}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
