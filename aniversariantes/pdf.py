from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from .sankhya import Funcionario

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def gerar_pdf(
    aniversariantes: list[Funcionario], ano: int, mes: int, destino: Path, empresa: str, mostrar_fotos: bool = True
) -> Path:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("aniversariantes.html")

    itens = []
    for f in aniversariantes:
        itens.append({"f": f, "data": f.nascimento.strftime("%d/%m")})

    html = tpl.render(
        itens=itens,
        mes_nome=MESES[mes - 1],
        ano=ano,
        empresa=empresa,
        total=len(itens),
        mostrar_fotos=mostrar_fotos,
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(TEMPLATES)).write_pdf(destino)
    return destino
