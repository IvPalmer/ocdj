FROM python:3.13-slim

WORKDIR /app

# System deps for audio processing (chromaprint for AcoustID)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libchromaprint-tools \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Set environment variables
ENV DJANGO_SETTINGS_MODULE=djtools_project.settings
ENV PYTHONUNBUFFERED=1

# Create staticfiles directory and collect static files
RUN mkdir -p /app/staticfiles
RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8002

# Dev: runserver, Prod: gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8002", "djtools_project.wsgi:application"]
