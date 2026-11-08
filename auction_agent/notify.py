"""Notification channels.

Phone push:
- ntfy (https://ntfy.sh): install the ntfy app, subscribe to a topic name only
  you know, set NTFY_TOPIC. No account needed. Self-host with NTFY_SERVER.
- Telegram: create a bot with @BotFather, set TELEGRAM_BOT_TOKEN and
  TELEGRAM_CHAT_ID.

Also: console (always), webhook (Slack/Discord), email (SMTP).
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Optional, Protocol

# priority: "low" | "default" | "high" | "urgent"


class Notifier(Protocol):
    def send(self, subject: str, body: str, url: Optional[str] = None, priority: str = "default") -> None: ...


def _post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=15).read()


class ConsoleNotifier:
    def send(self, subject, body, url=None, priority="default"):
        print(f"\n=== {subject} ===\n{body}\n")


class NtfyNotifier:
    PRIORITY = {"low": "2", "default": "3", "high": "4", "urgent": "5"}

    def __init__(self, topic: str, server: str = "https://ntfy.sh", token: Optional[str] = None):
        self.endpoint = f"{server.rstrip('/')}/{urllib.parse.quote(topic)}"
        self.token = token

    def send(self, subject, body, url=None, priority="default"):
        headers = {"Title": subject.encode("utf-8").decode("latin-1", "replace"),
                   "Priority": self.PRIORITY.get(priority, "3"), "Tags": "car"}
        if url:
            headers["Click"] = url
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        _post(self.endpoint, body[:3500].encode(), headers)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id

    def send(self, subject, body, url=None, priority="default"):
        text = f"{'🚨 ' if priority in ('high', 'urgent') else ''}{subject}\n\n{body}"
        if url:
            text += f"\n\n{url}"
        payload = {"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True,
                   "disable_notification": priority == "low"}
        _post(self.endpoint, json.dumps(payload).encode(), {"Content-Type": "application/json"})


class WebhookNotifier:
    """Posts {"text": ...}. Works with Slack incoming webhooks and Discord."""

    def __init__(self, url: str):
        self.url = url

    def send(self, subject, body, url=None, priority="default"):
        msg = f"*{subject}*\n{body}" + (f"\n{url}" if url else "")
        _post(self.url, json.dumps({"text": msg, "content": msg[:1900]}).encode(),
              {"Content-Type": "application/json"})


class EmailNotifier:
    def __init__(self, host: str, port: int, user: str, password: str, to: str):
        self.host, self.port, self.user, self.password, self.to = host, port, user, password, to

    def send(self, subject, body, url=None, priority="default"):
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = subject, self.user, self.to
        m.set_content(body + (f"\n\n{url}" if url else ""))
        with smtplib.SMTP(self.host, self.port) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.send_message(m)


class MultiNotifier:
    def __init__(self, notifiers: list):
        self.notifiers = notifiers

    def send(self, subject, body, url=None, priority="default"):
        for n in self.notifiers:
            try:
                n.send(subject, body, url=url, priority=priority)
            except Exception as e:  # one broken channel shouldn't stop the others
                print(f"[notify] {type(n).__name__} failed: {e}")

    @property
    def channels(self) -> list[str]:
        return [type(n).__name__.replace("Notifier", "") for n in self.notifiers]


def from_env() -> MultiNotifier:
    ns: list = [ConsoleNotifier()]
    if topic := os.getenv("NTFY_TOPIC"):
        ns.append(NtfyNotifier(topic, os.getenv("NTFY_SERVER", "https://ntfy.sh"), os.getenv("NTFY_TOKEN")))
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        ns.append(TelegramNotifier(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]))
    if url := os.getenv("ALERT_WEBHOOK_URL"):
        ns.append(WebhookNotifier(url))
    if os.getenv("SMTP_HOST") and os.getenv("ALERT_EMAIL_TO"):
        ns.append(EmailNotifier(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")),
                                os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"],
                                os.environ["ALERT_EMAIL_TO"]))
    return MultiNotifier(ns)
