FROM --platform=linux/arm64 python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV NODE_VERSION=20.x
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    git \
    unzip \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js and npm
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION} | bash - \
    && apt-get update \
    && apt-get install -y nodejs \
    && npm install -g npm@10.2.4 \
    && echo "Node.js version: $(node --version)" \
    && echo "npm version: $(npm --version)"

# Install TypeScript, AWS CDK CLI, and npx
RUN npm install -g typescript@5.3.3 aws-cdk \
    && echo "TypeScript version: $(tsc --version)" \
    && echo "AWS CDK version: $(cdk --version)" \
    && echo "npx version: $(npx --version)"

# Install uv (Python package installer)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s $HOME/.local/bin/uv /bin/uv \
    && ln -s $HOME/.local/bin/uvx /bin/uvx \
    && uv --version \
    && uvx --version

# Install AWS CLI v2 for ARM64
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf aws awscliv2.zip \
    && echo "AWS CLI version: $(aws --version)"

# Create app directory
WORKDIR /app

RUN mkdir /app/gh_issues

# Copy Python requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && echo "Python version: $(python --version)" \
    && echo "pip version: $(pip --version)"

RUN pip install mcp-server-aidd

# Verify all tools are available in PATH
RUN which python node npm tsc cdk npx uv aws uvx mcp-server-aidd

# Copy application code
COPY index.py process_definitions.py converse_agent.py converse_tools.py mcp_client.py ./

Add agents /app/agents/

# Set the entrypoint
ENTRYPOINT ["python", "index.py"]