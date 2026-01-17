from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import List, Optional


def _split_recipients(value: str) -> List[str]:
    # supports "a@b.dk, c@d.dk; e@f.dk"
    parts = [p.strip() for p in value.replace(";", ",").split(",")]
    return [p for p in parts if p]


def send_mail(subject: str, body: str) -> None:
    """
    Sends an email using SMTP credentials from environment variables:

    WAITLY_SMTP_HOST
    WAITLY_SMTP_PORT
    WAITLY_SMTP_USER
    WAITLY_SMTP_PASS
    WAITLY_MAIL_FROM
    WAITLY_MAIL_TO

    Notes:
    - Uses STARTTLS
    - Raises on missing config or SMTP errors (caller can catch if desired)
    """
    host = os.getenv("WAITLY_SMTP_HOST", "").strip()
    port_str = os.getenv("WAITLY_SMTP_PORT", "").strip()
    user = os.getenv("WAITLY_SMTP_USER", "").strip()
    password = os.getenv("WAITLY_SMTP_PASS", "").strip()
    mail_from = os.getenv("WAITLY_MAIL_FROM", "").strip()
    mail_to_raw = os.getenv("WAITLY_MAIL_TO", "").strip()

    if not host:
        raise RuntimeError("Missing env: WAITLY_SMTP_HOST")
    if not port_str:
        raise RuntimeError("Missing env: WAITLY_SMTP_PORT")
    if not mail_from:
        raise RuntimeError("Missing env: WAITLY_MAIL_FROM")
    if not mail_to_raw:
        raise RuntimeError("Missing env: WAITLY_MAIL_TO")

    try:
        port = int(port_str)
    except ValueError as e:
        raise RuntimeError(f"WAITLY_SMTP_PORT must be int, got: {port_str!r}") from e

    recipients = _split_recipients(mail_to_raw)
    if not recipients:
        raise RuntimeError("WAITLY_MAIL_TO parsed to empty recipient list")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()

        # Some SMTP setups allow anonymous send; if user is set, we login.
        if user:
            smtp.login(user, password)

        smtp.send_message(msg)
