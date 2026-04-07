ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.13-alpine3.23
FROM ${BUILD_FROM}

# Install Node.js (required by Claude Code CLI)
RUN apk add --no-cache nodejs npm

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy application source
COPY src/ /app/

# Copy rootfs overlay (s6 service definitions)
COPY rootfs /
