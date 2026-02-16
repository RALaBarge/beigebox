FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY beigebox/ beigebox/
COPY config.yaml .
COPY scripts/ scripts/
COPY pyproject.toml .
COPY LICENSE .

# Create data directory
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "-m", "beigebox", "dial"]
