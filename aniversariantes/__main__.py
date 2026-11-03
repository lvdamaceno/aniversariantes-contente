"""Envia o PDF dos aniversariantes do mês para o RH.

Uso:
    python -m aniversariantes                 # envia os aniversariantes do mês atual (agendado todo dia 1º)
    python -m aniversariantes --mes 12 --ano 2026 --sem-email   # só gera o PDF
    python -m aniversariantes --exemplo --sem-email             # PDF com dados fictícios (sem Sankhya)
    python -m aniversariantes --para voce@empresa.com.br         # teste: envia o PDF real só para esse e-mail
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .email_sender import enviar_email
from .pdf import MESES, gerar_pdf
from .sankhya import (
    SITUACOES_ATIVAS_PADRAO,
    Funcionario,
    SankhyaClient,
    aniversariantes_do_mes,
    foto_arquivo_local,
)

RAIZ = Path(__file__).resolve().parent.parent
log = logging.getLogger("aniversariantes")


def env(nome: str, padrao: str | None = None, obrigatorio: bool = False) -> str:
    valor = os.getenv(nome, padrao)
    if obrigatorio and not valor:
        sys.exit(f"Variável de ambiente obrigatória ausente: {nome}")
    return valor or ""


def dados_exemplo(mes: int) -> list[Funcionario]:
    nomes = [
        (3, "Ana Paula Ferreira", "Analista Financeiro", "Financeiro"),
        (7, "Bruno Henrique Costa", "Vendedor", "Comercial"),
        (12, "Carla Souza Lima", "Coordenadora", "Recursos Humanos"),
        (12, "Diego Martins", "Auxiliar de Logística", "Logística"),
        (18, "Eduarda Ribeiro Alves", "Designer", "Marketing"),
        (21, "Fernando Oliveira", "Desenvolvedor", "TI"),
        (25, "Gabriela Nunes", "Assistente Administrativa", "Administrativo"),
        (29, "Heitor dos Santos Pereira", "Supervisor de Loja", "Varejo"),
    ]
    return [
        Funcionario(1, i, n, date(1990, mes, d), dep, cargo, "Casa Contente")
        for i, (d, n, cargo, dep) in enumerate(nomes, start=1)
    ]


def main() -> int:
    load_dotenv(RAIZ / ".env")
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    for ruidoso in ("fontTools", "weasyprint"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mes", type=int)
    ap.add_argument("--ano", type=int)
    ap.add_argument("--sem-email", action="store_true", help="apenas gera o PDF")
    ap.add_argument("--para", action="append", metavar="EMAIL", help="envia somente para este e-mail (teste); pode repetir")
    ap.add_argument("--exemplo", action="store_true", help="usa dados fictícios (não acessa a Sankhya)")
    args = ap.parse_args()

    hoje = datetime.now(ZoneInfo(env("TZ_EMPRESA", "America/Sao_Paulo"))).date()
    ano = args.ano or hoje.year
    mes = args.mes or hoje.month

    empresa = env("NOME_EMPRESA", "Casa Contente")
    mostrar_fotos = env("MOSTRAR_FOTOS", "true").lower() in {"1", "true", "sim", "s", "yes"}

    if args.exemplo:
        aniversariantes = dados_exemplo(mes)
    else:
        cliente = SankhyaClient(
            base_url=env("SANKHYA_BASE_URL", "https://api.sankhya.com.br"),
            client_id=env("SANKHYA_CLIENT_ID", obrigatorio=True),
            client_secret=env("SANKHYA_CLIENT_SECRET", obrigatorio=True),
            x_token=env("SANKHYA_X_TOKEN", obrigatorio=True),
        )
        cod_emp = env("SANKHYA_CODIGO_EMPRESA")
        situacoes = {s.strip() for s in env("SITUACOES_ATIVAS").split(",") if s.strip()} or SITUACOES_ATIVAS_PADRAO
        funcionarios = cliente.listar_funcionarios(int(cod_emp) if cod_emp else None)
        aniversariantes = aniversariantes_do_mes(funcionarios, mes, situacoes)

        if mostrar_fotos:
            pasta_fotos = RAIZ / "fotos"
            campo = env("SANKHYA_CAMPO_FOTO", "IMAGEM")
            com_foto = 0
            for f in aniversariantes:
                f.foto = foto_arquivo_local(pasta_fotos, f.codigo_empresa, f.codigo) or cliente.buscar_foto(
                    f.codigo_empresa, f.codigo, campo
                )
                com_foto += bool(f.foto)
            log.info("Fotos encontradas: %d de %d", com_foto, len(aniversariantes))

    log.info("%d aniversariante(s) ativos em %s/%s", len(aniversariantes), mes, ano)

    mes_nome = MESES[mes - 1]
    pdf = gerar_pdf(
        aniversariantes,
        ano,
        mes,
        RAIZ / "saida" / f"aniversariantes-{ano}-{mes:02d}.pdf",
        empresa,
        mostrar_fotos,
    )
    log.info("PDF gerado: %s", pdf)

    if args.sem_email:
        return 0

    if args.para:
        destinatarios = [d.strip() for d in args.para if d.strip()]
        log.info("Modo teste: enviando apenas para %s", ", ".join(destinatarios))
    else:
        destinatarios = [d.strip() for d in env("EMAIL_DESTINATARIOS", obrigatorio=True).split(",") if d.strip()]
    mes_txt = f"{mes_nome.lower()} de {ano}"
    paragrafos = [
        "Bom dia, prezados.",
        f"Segue em anexo a relação dos aniversariantes do mês de {mes_txt}.",
        "Aproveitamos a oportunidade para celebrar mais um ano de vida dos nossos colegas "
        "e desejar a todos muita saúde, sucesso e felicidade.",
    ]

    message_id = enviar_email(
        regiao=env("AWS_REGION") or env("AWS_DEFAULT_REGION", "us-east-1"),
        remetente=env("EMAIL_REMETENTE", obrigatorio=True),
        configuration_set=env("SES_CONFIGURATION_SET") or None,
        destinatarios=destinatarios,
        assunto=f"Aniversariantes do mês de {mes_txt}",
        corpo_texto="\n\n".join(paragrafos),
        corpo_html="".join(f"<p>{p}</p>" for p in paragrafos),
        anexo=pdf,
    )
    log.info("E-mail enviado via SES para %s (MessageId %s)", ", ".join(destinatarios), message_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
