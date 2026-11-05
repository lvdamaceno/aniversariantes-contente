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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime

import requests

log = logging.getLogger(__name__)

# Situações (dadosContratuais.situacao) segundo a API Sankhya:
# 0 Demitido | 1 Atividade normal | 2-7 Afastamentos | 8 Transferido | 9 Aposentadoria por invalidez
# Uma chamada à central pode esperar até 120 s na fila e mais 60 s no Sankhya.
TEMPO_LIMITE_CENTRAL = 200

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
    def __init__(self, base_url: str, client_id: str, client_secret: str, x_token: str, timeout: int = 60,
                 central_url: str = "", central_chave: str = ""):
        self.base_url = base_url.rstrip("/")
        # Conexão central (sankhya-central): com ela, as credenciais e o token ficam lá.
        self.central_url = central_url.rstrip("/")
        self.central_chave = central_chave
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

    def _pedir_get(self, path: str, params: dict) -> requests.Response:
        if self.central_url:
            return self.session.get(f"{self.central_url}/pessoal", headers={"x-proxy-chave": self.central_chave, "Accept": "application/json"},
                                    params={"caminho": path, **params}, timeout=TEMPO_LIMITE_CENTRAL)
        return self.session.get(f"{self.base_url}{path}", headers=self._headers(), params=params, timeout=self.timeout)

    def _get(self, path: str, params: dict) -> dict:
        for tentativa in range(3):
            resp = self._pedir_get(path, params)
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
    def _paginar(self, path: str, chave: str, params: dict) -> list[dict]:
        itens: list[dict] = []
        page = 0
        while True:
            data = self._get(path, {**params, "page": page})
            itens.extend(data.get(chave) or [])
            if str((data.get("pagination") or {}).get("hasMore", "false")).lower() != "true":
                return itens
            page += 1

    def listar_funcionarios(
        self, codigo_empresa: int | None = None, situacoes: set[str] | None = None
    ) -> list[dict]:
        """Funcionários com dados detalhados, no formato de /funcionarios-detalhados.

        Usa GET /v1/pessoal/funcionarios-detalhados (módulo Pessoal >= 5.126). Se o
        endpoint não existir no ambiente (404), lista os códigos em /v1/pessoal/funcionarios,
        filtra pela situação e busca o detalhe de cada um em
        /v1/pessoal/funcionarios/{codigo}/empresa/{empresa}.
        """
        params = {"codigoEmpresa": codigo_empresa} if codigo_empresa else {}
        try:
            todos = self._paginar("/v1/pessoal/funcionarios-detalhados", "funcionarios", params)
            log.info("%d funcionários retornados pela API (funcionarios-detalhados)", len(todos))
            return todos
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            log.info("Endpoint funcionarios-detalhados indisponível neste ambiente; usando lista + detalhe")

        codigos = self._paginar("/v1/pessoal/funcionarios", "codigos", params)
        if situacoes:
            codigos = [c for c in codigos if str(c.get("situacao", "")).strip() in situacoes]
        log.info("%d funcionários ativos na lista; buscando detalhes", len(codigos))

        def detalhar(item: dict) -> dict:
            emp, func = int(item["codigoEmpresa"]), int(item["codigoFuncionario"])
            d = self._get(f"/v1/pessoal/funcionarios/{func}/empresa/{emp}", {})
            contrato = d.setdefault("dadosContratuais", {})
            contrato.setdefault("lotacao", {
                "departamento": contrato.get("departamento"),
                "cargo": contrato.get("cargo"),
            })
            d["codigoEmpresa"], d["codigoFuncionario"] = emp, func
            return d

        if not self.central_url:
            self._headers()  # autentica antes de paralelizar
        with ThreadPoolExecutor(max_workers=5) as pool:
            return list(pool.map(detalhar, codigos))

    # ------------------------------------------------------------------ fotos
    def _executar_sql(self, sql: str) -> list[list]:
        """Consulta somente leitura via DbExplorerSP.executeQuery (Gateway)."""
        if self.central_url:
            resp = self.session.post(f"{self.central_url}/servico", headers={"x-proxy-chave": self.central_chave},
                                     json={"nome": "DbExplorerSP.executeQuery", "corpo": {"sql": sql}, "semCache": True},
                                     timeout=TEMPO_LIMITE_CENTRAL)
            if not resp.ok:
                raise RuntimeError(f"Conexão central ({resp.status_code}): {resp.text[:300]}")
            data = resp.json()["d"]
            if str(data.get("status")) != "1":
                raise RuntimeError(data.get("statusMessage") or "erro no DbExplorerSP.executeQuery")
            return (data.get("responseBody") or {}).get("rows") or []
        resp = self.session.post(
            f"{self.base_url}/gateway/v1/mge/service.sbr",
            params={"serviceName": "DbExplorerSP.executeQuery", "outputType": "json"},
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"serviceName": "DbExplorerSP.executeQuery", "requestBody": {"sql": sql}},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("status")) != "1":
            raise RuntimeError(data.get("statusMessage") or "erro no DbExplorerSP.executeQuery")
        return (data.get("responseBody") or {}).get("rows") or []

    def nomes_fantasia(self, codigos_empresa: set[int]) -> dict[int, str]:
        """{CODEMP: NOMEFANTASIA} da TSIEMP. Retorna {} em caso de falha (cai na razão social)."""
        codigos = sorted({int(c) for c in codigos_empresa if c is not None})
        if not codigos:
            return {}
        sql = f"SELECT CODEMP, NOMEFANTASIA FROM TSIEMP WHERE CODEMP IN ({', '.join(map(str, codigos))})"
        try:
            return {int(cod): " ".join(nome.split()) for cod, nome in self._executar_sql(sql) if nome and nome.strip()}
        except Exception as exc:  # noqa: BLE001 - usa a razão social como alternativa
            log.warning("Não foi possível ler o nome fantasia em TSIEMP: %s", exc)
            return {}

    def buscar_fotos(self, chaves: list[tuple[int, int]], lote: int = 10) -> dict[tuple[int, int], str]:
        """Fotos de TFPFUN.IMAGEM para os pares (CODEMP, CODFUNC). Retorna {chave: data URI}.

        A coluna é BLOB (tipo 'image' no SQL Server) e o loadRecords a devolve vazia,
        então o SQL Server converte para base64 e a consulta vai pelo DbExplorer.
        Foto é opcional: falhas são registradas em log e nunca interrompem o envio.
        """
        fotos: dict[tuple[int, int], str] = {}
        # int() garante que só números entram no SQL
        chaves = [(int(e), int(f)) for e, f in chaves]
        for inicio in range(0, len(chaves), lote):
            grupo = chaves[inicio : inicio + lote]
            filtro = " OR ".join(f"(CODEMP = {e} AND CODFUNC = {f})" for e, f in grupo)
            sql = (
                "SELECT T.CODEMP, T.CODFUNC, "
                "CAST('' AS XML).value('xs:base64Binary(sql:column(\"T.B\"))', 'VARCHAR(MAX)') AS B64 "
                "FROM (SELECT CODEMP, CODFUNC, CAST(IMAGEM AS VARBINARY(MAX)) AS B "
                f"FROM TFPFUN WHERE IMAGEM IS NOT NULL AND ({filtro})) T"
            )
            try:
                for emp, func, b64 in self._executar_sql(sql):
                    uri = _para_data_uri(b64)
                    if uri:
                        fotos[(int(emp), int(func))] = uri
            except Exception as exc:  # noqa: BLE001 - foto é opcional
                log.warning("Não foi possível buscar fotos (%d funcionário(s)): %s", len(grupo), exc)
        return fotos


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


_SUFIXOS_EMPRESA = {
    "ltda", "ltda.", "eireli", "me", "epp", "s/a", "s.a.", "s.a", "sa", "mei", "slu", "-",
}
_CONECTIVOS = {"da", "de", "do", "das", "dos", "e"}


def nome_empresa(razao_social: str) -> str:
    """Razão social sem sufixos societários (alternativa quando não há nome fantasia)."""
    palavras = razao_social.split()
    while palavras and palavras[-1].lower() in _SUFIXOS_EMPRESA:
        palavras.pop()
    return " ".join(
        p.lower() if p.lower() in _CONECTIVOS and i else p.capitalize() for i, p in enumerate(palavras)
    )


def _desc(obj: dict | None) -> str:
    return ((obj or {}).get("descricao") or "").strip()


def aniversariantes_do_mes(
    funcionarios: list[dict],
    mes: int,
    situacoes_ativas: set[str] = SITUACOES_ATIVAS_PADRAO,
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
                empresa=nome_empresa(((contrato.get("empresa") or {}).get("razaoSocial") or "").strip()),
            )
        )
    resultado.sort(key=lambda x: (x.nascimento.day, x.nome))
    return resultado
