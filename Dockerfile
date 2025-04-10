FROM python:3.9-slim

WORKDIR /app

# Install git and curl for GitHub operations
# Add apt-utils and build essentials for ARM compatibility
RUN apt-get update && \
    apt-get install -y git curl apt-utils build-essential python3-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Upgrade pip first for better dependency handling
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements
COPY requirements.txt .
# Install dependencies with platform-specific options
RUN pip install --no-cache-dir wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy the operator code
COPY operator/ .

# Set the entrypoint with clusterwide flag (correct flag for older kopf versions)
ENTRYPOINT ["kopf", "run", "--standalone", "--verbose", "--all-namespaces", "operator.py"]
