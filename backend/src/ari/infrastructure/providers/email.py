"""E-mail delivery: the log (development and tests) or any SMTP relay (production).

SMTP keeps the provider swappable (Brevo, Scaleway TEM, Postmark, ...) with settings only.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid
from typing import Literal

from ari.application.ports.email import EmailMessage
from ari.domain.errors import EmailDeliveryError

logger = logging.getLogger(__name__)


class LogEmailSender:
    """Writes each message to the log and keeps it in memory: nothing leaves the machine."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)
        logger.warning(
            "E-mail (non envoyé) à %s : %s\n%s", message.to, message.subject, message.text
        )


class SmtpEmailSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str | None,
        password: str | None,
        security: Literal["starttls", "ssl", "none"],
        timeout_seconds: float,
    ) -> None:
        self._host, self._port, self._sender = host, port, sender
        self._username, self._password = username, password
        self._security, self._timeout = security, timeout_seconds

    def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Date"] = formatdate(localtime=False)
        mime["Message-ID"] = make_msgid()
        mime.set_content(message.text)
        smtp_class = smtplib.SMTP_SSL if self._security == "ssl" else smtplib.SMTP
        try:
            with smtp_class(self._host, self._port, timeout=self._timeout) as smtp:
                if self._security == "starttls":
                    smtp.starttls()
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                # Explicit envelope: the recipient is exactly the one address, never parsed.
                smtp.send_message(mime, to_addrs=[message.to])
        except (OSError, ValueError, smtplib.SMTPException) as exc:
            logger.error("SMTP delivery to %s failed: %s", message.to, exc)
            raise EmailDeliveryError(
                "L'e-mail n'a pas pu être envoyé ; réessayez plus tard"
            ) from exc
