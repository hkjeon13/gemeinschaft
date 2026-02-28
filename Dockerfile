FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY services ./services
COPY scripts ./scripts
COPY db ./db
COPY main.py ./

RUN python -m pip install --upgrade pip \
    && python -m pip install .

CMD ["python", "main.py"]
