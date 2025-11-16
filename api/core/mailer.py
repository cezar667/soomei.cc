"""
Email adapter for the Soomei backend.

The default implementation uses SMTP, reading credentials from Settings.
"""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import ssl

from .config import get_settings


def send_email(subject: str, to_email: str, html_body: str, text_body: str | None = None) -> bool:
    """
    Envia e-mails utilizando as credenciais SMTP definidas via env.
    Quando configurações não estiverem disponíveis, retorna False sem enviar.
    """
    settings = get_settings()
    if not (
        settings.smtp_host
        and settings.smtp_user
        and settings.smtp_password
        and settings.smtp_from
        and settings.smtp_port
    ):
        print("[email] Configuracao SMTP ausente; ignorando envio.")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    plain = text_body or html_body
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    port = settings.smtp_port or 465
    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_host, port, context=context) as server:
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(settings.smtp_host, port) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, [to_email], msg.as_string())
        return True
    except Exception as exc:
        print(f"[email] Falha ao enviar para {to_email}: {exc}")
        return False
