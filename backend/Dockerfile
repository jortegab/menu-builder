FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Schema migrations (2026-09-07, replacing create_all). alembic.ini lands
# at /srv/alembic.ini and migrations/ beside app/, which is exactly the
# layout migrations/env.py resolves `app` from -- so inside the container
# `alembic upgrade head` needs neither -c nor a cd. This is also why both
# live under backend/ rather than at the repo root: backend/ is the build
# context, so nothing above it can be copied in.
COPY alembic.ini .
COPY migrations ./migrations

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
