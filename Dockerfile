FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies required for git cloning, psutil, and compiling pip wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    build-essential \
    libffi-dev \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install core hoster bot dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy hoster bot source code
COPY config.py db_manager.py security_scanner.py process_manager.py main.py ./

# Ensure isolated workspaces directory exists
RUN mkdir -p /app/workspaces

CMD ["python", "main.py"]
