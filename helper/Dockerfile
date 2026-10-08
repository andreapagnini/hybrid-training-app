FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_DIR=/srv/app
WORKDIR /srv/helper
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
# Test copy of the app from this branch (staged by deploy.sh).
COPY _app/ /srv/app/
RUN useradd --system --uid 10001 helper
USER helper
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --no-server-header
