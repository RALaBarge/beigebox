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
COPY middleware/ middleware/
COPY config.yaml .
COPY scripts/ scripts/

# Create data directory
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "-m", "middleware.main"]
