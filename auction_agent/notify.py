"""Notification channels: console, webhook (Slack/Discord/ntfy), email (SMTP)."""
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Protocol


class Notifier(Protocol):
    def send(self, subject: str, body: str) -> None: ...


class ConsoleNotifier:
    def send(self, subject: str, body: str) -> None:
        print(f"\n=== {subject} ===\n{body}\n")


class WebhookNotifier:
    """Posts {"text": ...}. Works with Slack incoming webhooks and Discord
    (Discord reads "content", so both keys are sent)."""

    def __init__(self, url: str):
        self.url = url

    def send(self, subject: str, body: str) -> None:
        msg = f"*{subject}*\n{body}"
        req = urllib.request.Request(self.url, data=json.dumps({"text": msg, "content": msg[:1900]}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()


class EmailNotifier:
    def __init__(self, host: str, port: int, user: str, password: str, to: str):
        self.host, self.port, self.user, self.password, self.to = host, port, user, password, to

    def send(self, subject: str, body: str) -> None:
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = subject, self.user, self.to
        m.set_content(body)
        with smtplib.SMTP(self.host, self.port) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.send_message(m)


class MultiNotifier:
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, subject: str, body: str) -> None:
        for n in self.notifiers:
            try:
                n.send(subject, body)
            except Exception as e:  # one broken channel shouldn't stop the others
                print(f"[notify] {type(n).__name__} failed: {e}")


def from_env() -> MultiNotifier:
    ns: list[Notifier] = [ConsoleNotifier()]
    if url := os.getenv("ALERT_WEBHOOK_URL"):
        ns.append(WebhookNotifier(url))
    if os.getenv("SMTP_HOST") and os.getenv("ALERT_EMAIL_TO"):
        ns.append(EmailNotifier(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")),
                                os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"],
                                os.environ["ALERT_EMAIL_TO"]))
    return MultiNotifier(ns)
