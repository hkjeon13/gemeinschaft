import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _email_delivery_mode() -> str:
    mode = os.getenv("AUTH_EMAIL_DELIVERY_MODE", "log").strip().lower()
    if mode not in {"log", "smtp"}:
        raise ValueError("AUTH_EMAIL_DELIVERY_MODE must be one of: log, smtp.")
    return mode


def _smtp_port() -> int:
    raw = os.getenv("AUTH_SMTP_PORT", "587").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("AUTH_SMTP_PORT must be an integer.") from exc
    if value <= 0:
        raise ValueError("AUTH_SMTP_PORT must be greater than 0.")
    return value


def _smtp_timeout_seconds() -> float:
    raw = os.getenv("AUTH_SMTP_TIMEOUT_SECONDS", "10").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("AUTH_SMTP_TIMEOUT_SECONDS must be a number.") from exc
    if value <= 0:
        raise ValueError("AUTH_SMTP_TIMEOUT_SECONDS must be greater than 0.")
    return value


def _truthy(env_name: str, default: str = "false") -> bool:
    return os.getenv(env_name, default).strip().lower() in {"1", "true", "yes", "y"}


def _required_env(env_name: str) -> str:
    value = os.getenv(env_name, "").strip()
    if not value:
        raise ValueError(f"{env_name} must be set.")
    return value


def send_verification_email(*, recipient_email: str, recipient_name: str, verify_url: str) -> None:
    mode = _email_delivery_mode()
    if mode == "log":
        logger.warning(
            "[AUTH] Verification link generated for %s <%s>: %s",
            recipient_name,
            recipient_email,
            verify_url,
        )
        return

    smtp_host = _required_env("AUTH_SMTP_HOST")
    smtp_from = _required_env("AUTH_SMTP_FROM")
    smtp_port = _smtp_port()
    smtp_timeout = _smtp_timeout_seconds()
    smtp_username = os.getenv("AUTH_SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("AUTH_SMTP_PASSWORD", "").strip()
    use_ssl = _truthy("AUTH_SMTP_USE_SSL", "false")
    use_starttls = _truthy("AUTH_SMTP_USE_STARTTLS", "true")

    if use_ssl and use_starttls:
        raise ValueError("AUTH_SMTP_USE_SSL and AUTH_SMTP_USE_STARTTLS cannot both be true.")
    if smtp_username and not smtp_password:
        raise ValueError("AUTH_SMTP_PASSWORD must be set when AUTH_SMTP_USERNAME is set.")

    message = EmailMessage()
    message["Subject"] = "[Gemeinschaft] 이메일 인증을 완료해주세요"
    message["From"] = smtp_from
    message["To"] = recipient_email
    message.set_content(
        f"""안녕하세요, {recipient_name}님.

회원가입 이메일 인증을 완료하려면 아래 링크를 클릭하세요.

{verify_url}

만약 본인이 요청하지 않았다면 이 메일을 무시해 주세요.
"""
    )

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=smtp_timeout) as smtp:
                if smtp_username:
                    smtp.login(smtp_username, smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as smtp:
                if use_starttls:
                    smtp.starttls()
                if smtp_username:
                    smtp.login(smtp_username, smtp_password)
                smtp.send_message(message)
    except Exception as exc:
        raise RuntimeError("Failed to send verification email.") from exc
