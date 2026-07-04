FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY app /app/app
COPY tests /app/tests
COPY README.md /app/README.md
COPY docs /app/docs
COPY .env.example /app/.env.example

EXPOSE 8000

CMD ["python", "-m", "app.cli.entrypoint"]
