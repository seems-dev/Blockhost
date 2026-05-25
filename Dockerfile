FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (psycopg[binary] does not require build tools, keep minimal)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "uv_build>=0.11.7,<0.12.0"

COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "blockhost_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
