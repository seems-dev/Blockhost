from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from blockhost_backend.config.config_manager import get_settings

logger = logging.getLogger(__name__)


def send_email(*, to_email: str, subject: str, text_body: str) -> None:
    settings = get_settings()
    from_email = settings.smtp_from_email or settings.smtp_user

    if not settings.smtp_host or not from_email:
        logger.warning(
            "SMTP is not configured. Email to %s with subject %r:\n%s",
            to_email,
            subject,
            text_body,
        )
        return

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


def send_verification_email(*, to_email: str, nickname: str, otp: str) -> None:
    body = (
        f"Hi {nickname},\n\n"
        "Use this verification code to finish creating your BlockHost account:\n\n"
        f"{otp}\n\n"
        "This code expires in 24 hours. If you did not create a BlockHost account, you can ignore this email.\n"
    )
    send_email(
        to_email=to_email,
        subject="Verify your BlockHost email",
        text_body=body,
    )
