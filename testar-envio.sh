#!/usr/bin/env bash
# Teste de ponta a ponta: busca os dados reais na Sankhya, gera o PDF
# e envia SOMENTE para o(s) e-mail(s) informado(s). O RH não recebe nada.
#
# Uso:
#   ./testar-envio.sh voce@suaempresa.com.br
#   ./testar-envio.sh voce@suaempresa.com.br --mes 12          # outro mês
#   ./testar-envio.sh voce@suaempresa.com.br --exemplo         # dados fictícios, sem Sankhya
set -euo pipefail

if [ $# -lt 1 ] || [[ "$1" != *@* ]]; then
  echo "Uso: $0 <email-de-teste> [--mes N] [--ano AAAA] [--exemplo]" >&2
  exit 1
fi

EMAIL="$1"; shift
cd "$(dirname "$0")"
exec ./run.sh --para "$EMAIL" "$@"
