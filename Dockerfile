FROM python:3.12-slim

# Bibliotecas de sistema exigidas pelo WeasyPrint
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aniversariantes ./aniversariantes
COPY templates ./templates
COPY fotos ./fotos

ENV PYTHONUNBUFFERED=1 TZ=America/Sao_Paulo
CMD ["python", "-m", "aniversariantes"]
