# Pinned to a minor version. "python:3.12" would silently move under you and
# turn a deploy into an unplanned upgrade.
FROM python:3.12-slim

# Python writes .pyc files and buffers stdout by default. Neither helps in a
# container, and buffering means logs appear late or not at all on a crash.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, in their own layer. Docker caches each instruction, so
# editing application code does not reinstall every package.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY static/ static/

# Run as a non-root user. If the process is ever compromised, it should not
# own the filesystem it is running on.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
# By number, so the platform can tell it is not root without a passwd file.
USER 1000

EXPOSE 8080

# Shell form, so ${PORT} is expanded. Cloud Run sets PORT and health-checks
# that exact port; a hardcoded one fails to start.
#
# exec replaces the shell with uvicorn, so uvicorn is PID 1 and receives
# Cloud Run's SIGTERM itself: it finishes in-flight requests and background
# lead emails, then exits. Without it the shell gets the signal, ignores it,
# and everything is killed (exit 137) mid-request.
#
# Workers: one. Cloud Run scales by adding instances, so a second worker
# inside one container competes for the same CPU rather than adding capacity.
# hadolint ignore=DL3025
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1