"""Posting to a Slack channel through an incoming webhook (daily reports, daily.py)."""

import requests

from .config import HTTP_TIMEOUT

WEBHOOK_PREFIX = "https://hooks.slack.com/"  # nothing else gets the report


def post(webhook, text):
    if not webhook.startswith(WEBHOOK_PREFIX):
        raise ValueError(f"not a Slack webhook (should start with {WEBHOOK_PREFIX})")
    r = requests.post(webhook, json={"text": text}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
