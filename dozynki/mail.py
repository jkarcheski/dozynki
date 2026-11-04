"""Outgoing email through the DOZYNKI_SMTP_* server: password resets (accounts.py) and daily reports (daily.py)."""

import smtplib
from email.message import EmailMessage

from .config import SMTP


def configured():
    return bool(SMTP["host"])


def send(to, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP["from"] or SMTP["user"], to, subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP["host"], int(SMTP["port"] or 587), timeout=20) as s:
        s.starttls()
        if SMTP["user"]:
            s.login(SMTP["user"], SMTP["password"])
        s.send_message(msg)
