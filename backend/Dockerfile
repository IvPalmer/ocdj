FROM python:3.13-slim

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=${GIT_SHA}

WORKDIR /app

# System deps:
#  - libchromaprint-tools (AcoustID fingerprinting) + ffmpeg (convert stage)
#  - Node + @anthropic-ai/claude-code: the Claude Agent SDK spawns the
#    `claude` CLI subprocess, so the CLI binary must be on PATH. Auth comes
#    from CLAUDE_CODE_OAUTH_TOKEN env (generated via `claude setup-token`
#    on a Max-authenticated Mac).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libchromaprint-tools \
    ffmpeg \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Set environment variables
ENV DJANGO_SETTINGS_MODULE=djtools_project.settings
ENV PYTHONUNBUFFERED=1
ENV GIT_SHA=${GIT_SHA}

# Create staticfiles directory and collect static files
RUN mkdir -p /app/staticfiles
RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8002

# Prod entrypoint: migrate → gunicorn. Dev compose still uses runserver.
COPY entrypoint.prod.sh /entrypoint.prod.sh
RUN chmod +x /entrypoint.prod.sh
CMD ["/entrypoint.prod.sh"]
