from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


def enviar_email(
    *,
    host: str,
    port: int,
    usuario: str,
    senha: str,
    remetente: str,
    destinatarios: list[str],
    assunto: str,
    corpo_texto: str,
    corpo_html: str,
    anexo: Path,
) -> None:
    msg = EmailMessage()
    msg["From"] = remetente
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = assunto
    msg.set_content(corpo_texto)
    msg.add_alternative(corpo_html, subtype="html")
    msg.add_attachment(anexo.read_bytes(), maintype="application", subtype="pdf", filename=anexo.name)

    contexto = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=contexto, timeout=60) as smtp:
            if usuario:
                smtp.login(usuario, senha)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.starttls(context=contexto)
            if usuario:
                smtp.login(usuario, senha)
            smtp.send_message(msg)
