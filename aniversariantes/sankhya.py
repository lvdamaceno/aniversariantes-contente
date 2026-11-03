"""Cliente da API oficial Sankhya (API Gateway).

Autenticação: OAuth 2.0 Client Credentials + header X-Token
(https://developer.sankhya.com.br/reference/post_authenticate).
O fluxo legado (appkey/usuário/senha em /login) foi descontinuado.
"""

from __future__ import annotations

import base64
import binascii
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime

import requests

log = logging.getLogger(__name__)

# Situações (dadosContratuais.situacao) segundo a API Sankhya:
# 0 Demitido | 1 Atividade normal | 2-7 Afastamentos | 8 Transferido | 9 Aposentadoria por invalidez
SITUACOES_ATIVAS_PADRAO = {"1", "2", "3", "4", "5", "6", "7", "9"}


@dataclass
class Funcionario:
    codigo_empresa: int
    codigo: int
    nome: str
    nascimento: date
    departamento: str
    cargo: str
    empresa: str
    foto: str | None = None  # data URI (data:image/...;base64,...)

    @property
    def iniciais(self) -> str:
        partes = [p for p in self.nome.split() if p.lower() not in {"da", "de", "do", "das", "dos", "e"}]
        if not partes:
            return "?"
        return (partes[0][0] + (partes[-1][0] if len(partes) > 1 else "")).upper()


class SankhyaClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, x_token: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.x_token = x_token
        self.timeout = timeout
        self._token: str | None = None
        self._token_expira_em = 0.0
        self.session = requests.Session()

    # ------------------------------------------------------------------ auth
    def _autenticar(self) -> None:
        resp = self.session.post(
            f"{self.base_url}/authenticate",
            headers={"X-Token": self.x_token, "Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Falha na autenticação Sankhya ({resp.status_code}): {resp.text[:500]}")
        payload = resp.json()
        self._token = payload["access_token"]
        # renova 30s antes de expirar (token costuma durar 300s)
        self._token_expira_em = time.time() + int(payload.get("expires_in", 300)) - 30
        log.info("Autenticado na Sankhya (token válido por %ss)", payload.get("expires_in"))

    def _headers(self) -> dict:
        if not self._token or time.time() >= self._token_expira_em:
            self._autenticar()
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict) -> dict:
        for tentativa in range(3):
            resp = self.session.get(f"{self.base_url}{path}", headers=self._headers(), params=params, timeout=self.timeout)
            if resp.status_code == 401 and tentativa == 0:
                self._token = None  # token expirado/revogado: reautentica
                continue
            if resp.status_code >= 500 and tentativa < 2:
                time.sleep(2 ** tentativa * 3)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Falha ao consultar {path}")

    # ------------------------------------------------------------ funcionários
    def listar_funcionarios(self, codigo_empresa: int | None = None) -> list[dict]:
        """GET /v1/pessoal/funcionarios-detalhados com paginação."""
        todos: list[dict] = []
        page = 0
        while True:
            params = {"page": page}
            if codigo_empresa:
                params["codigoEmpresa"] = codigo_empresa
            data = self._get("/v1/pessoal/funcionarios-detalhados", params)
            todos.extend(data.get("funcionarios") or [])
            has_more = str((data.get("pagination") or {}).get("hasMore", "false")).lower() == "true"
            if not has_more:
                break
            page += 1
        log.info("%d funcionários retornados pela API", len(todos))
        return todos

    # ------------------------------------------------------------------ fotos
    def buscar_foto(self, codigo_empresa: int, codigo_funcionario: int, campo: str = "IMAGEM") -> str | None:
        """Tenta obter a foto do funcionário (TFPFUN) via CRUDServiceProvider.loadRecords no Gateway.

        A API REST de Pessoal não expõe foto, então isso é "melhor esforço":
        retorna um data URI ou None, nunca lança exceção.
        """
        body = {
            "serviceName": "CRUDServiceProvider.loadRecords",
            "requestBody": {
                "dataSet": {
                    "rootEntity": "Funcionario",
                    "ignoreCalculatedFields": "true",
                    "useFileBasedPagination": "true",
                    "includePresentationFields": "N",
                    "offsetPage": "0",
                    "criteria": {
                        "expression": {"$": "this.CODEMP = ? AND this.CODFUNC = ?"},
                        "parameter": [
                            {"$": str(codigo_empresa), "type": "I"},
                            {"$": str(codigo_funcionario), "type": "I"},
                        ],
                    },
                    "entity": {"fieldset": {"list": campo}},
                }
            },
        }
        try:
            resp = self.session.post(
                f"{self.base_url}/gateway/v1/mge/service.sbr",
                params={"serviceName": "CRUDServiceProvider.loadRecords", "outputType": "json"},
                headers={**self._headers(), "Content-Type": "application/json"},
                json=body,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("status")) != "1":
                log.debug("loadRecords foto %s/%s: %s", codigo_empresa, codigo_funcionario, data.get("statusMessage"))
                return None
            entity = ((data.get("responseBody") or {}).get("entities") or {}).get("entity")
            if isinstance(entity, list):
                entity = entity[0] if entity else None
            valor = ((entity or {}).get("f0") or {}).get("$")
            return _para_data_uri(valor)
        except Exception as exc:  # noqa: BLE001 - foto é opcional
            log.debug("Foto indisponível para %s/%s: %s", codigo_empresa, codigo_funcionario, exc)
            return None


def _mime_por_bytes(raw: bytes) -> str | None:
    if raw.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw.startswith(b"BM"):
        return "image/bmp"
    return None


def _para_data_uri(valor) -> str | None:
    if not valor or not isinstance(valor, str):
        return None
    valor = valor.strip()
    if valor.startswith("data:image"):
        return valor
    raw = None
    try:
        raw = base64.b64decode(valor, validate=True)
    except (binascii.Error, ValueError):
        try:
            raw = bytes.fromhex(valor)
        except ValueError:
            return None
    mime = _mime_por_bytes(raw)
    if not mime:
        return None
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def foto_arquivo_local(pasta, codigo_empresa: int, codigo_funcionario: int) -> str | None:
    """Foto manual em fotos/{CODEMP}_{CODFUNC}.jpg|png (tem prioridade sobre a da Sankhya)."""
    from pathlib import Path

    for ext in ("jpg", "jpeg", "png"):
        arq = Path(pasta) / f"{codigo_empresa}_{codigo_funcionario}.{ext}"
        if arq.exists():
            raw = arq.read_bytes()
            mime = _mime_por_bytes(raw) or "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    return None


def _parse_data(valor: str | None) -> date | None:
    if not valor:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(valor[:19] if "T" in valor else valor, fmt).date()
        except ValueError:
            continue
    return None


def _nome_proprio(nome: str) -> str:
    minusculas = {"da", "de", "do", "das", "dos", "e"}
    return " ".join(p if p in minusculas else p.capitalize() for p in nome.lower().split())


def _desc(obj: dict | None) -> str:
    return ((obj or {}).get("descricao") or "").strip()


def aniversariantes_do_mes(
    funcionarios: list[dict], mes: int, situacoes_ativas: set[str] = SITUACOES_ATIVAS_PADRAO
) -> list[Funcionario]:
    resultado: list[Funcionario] = []
    vistos: set[tuple[int, int]] = set()
    for f in funcionarios:
        contrato = f.get("dadosContratuais") or {}
        afast = f.get("afastamento") or {}
        situacao = str(contrato.get("situacao", "")).strip()
        if situacao not in situacoes_ativas or afast.get("dataDesligamento"):
            continue
        nascimento = _parse_data((f.get("dadosPessoais") or {}).get("nascimento"))
        if not nascimento or nascimento.month != mes:
            continue
        chave = (f.get("codigoEmpresa"), f.get("codigoFuncionario"))
        if chave in vistos:
            continue
        vistos.add(chave)
        lotacao = contrato.get("lotacao") or {}
        nome = (f.get("nomeSocial") or f.get("nome") or "").strip()
        resultado.append(
            Funcionario(
                codigo_empresa=f.get("codigoEmpresa"),
                codigo=f.get("codigoFuncionario"),
                nome=_nome_proprio(nome),
                nascimento=nascimento,
                departamento=_desc(lotacao.get("departamento")).title(),
                cargo=_desc(lotacao.get("cargo")).title(),
                empresa=((contrato.get("empresa") or {}).get("razaoSocial") or "").strip(),
            )
        )
    resultado.sort(key=lambda x: (x.nascimento.day, x.nome))
    return resultado
