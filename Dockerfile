FROM python:3.9-slim

WORKDIR /app

# Install git and curl for GitHub operations
RUN apt-get update && \
    apt-get install -y git curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the operator code
COPY operator/ .

# Set the entrypoint with clusterwide flag (correct flag for older kopf versions)
ENTRYPOINT ["kopf", "run", "--standalone", "--verbose", "--all-namespaces", "operator.py"]
