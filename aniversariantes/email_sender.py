"""Envio de e-mail com anexo via Amazon SES (API v2).

Credenciais AWS vêm da cadeia padrão do boto3 (AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY / AWS_REGION nas variáveis de ambiente).
"""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import boto3
from botocore.config import Config


def enviar_email(
    *,
    regiao: str,
    remetente: str,
    destinatarios: list[str],
    assunto: str,
    corpo_texto: str,
    corpo_html: str,
    anexo: Path,
    configuration_set: str | None = None,
) -> str:
    """Envia o e-mail e retorna o MessageId do SES."""
    msg = EmailMessage()
    msg["From"] = remetente
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = assunto
    msg.set_content(corpo_texto)
    msg.add_alternative(corpo_html, subtype="html")
    msg.add_attachment(anexo.read_bytes(), maintype="application", subtype="pdf", filename=anexo.name)

    ses = boto3.client(
        "sesv2",
        region_name=regiao,
        config=Config(retries={"mode": "standard", "total_max_attempts": 4}, connect_timeout=10, read_timeout=60),
    )
    params = {
        "FromEmailAddress": remetente,
        "Destination": {"ToAddresses": destinatarios},
        "Content": {"Raw": {"Data": msg.as_bytes()}},
    }
    if configuration_set:
        params["ConfigurationSetName"] = configuration_set
    resposta = ses.send_email(**params)
    return resposta["MessageId"]
