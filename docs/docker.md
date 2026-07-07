# Docker Deployment Guide

## Overview

The Deep Research API can be deployed with Docker Compose as the main API plus
an ArXiv MCP service.

## Quick Start

### 1. Create your `.env` file

Copy the example and fill in your API keys, `OPENAI_BASE_URL`, LLM model overrides, etc.:

```bash
cp .env.example .env
```

### 2. Build or pull the ArXiv MCP image

`docker-compose.yml` expects an image named `arxiv-mcp-server:latest` by
default. Build it from your ArXiv MCP server checkout, pull it from your image
registry, or set `ARXIV_MCP_IMAGE` in `.env` to another image name.

Example local build:

```bash
docker build -t arxiv-mcp-server:latest /path/to/arxiv-mcp-server
```

### 3. Start services

```bash
docker compose up -d
```

Access the API at: http://localhost:8080/docs

## Services

| Service | Port | Profile | Description |
|---------|------|---------|-------------|
| `api` | 8080 | default | Deep Research API |
| `mcp` | 8000 | default | Arxiv MCP Server |

## Environment Variables

All configuration lives in `.env` (loaded via `env_file` in docker-compose).
See `.env.example` for the full list.

## Build & Rebuild

```bash
docker compose build

# clean build
docker compose build --no-cache
```

## Logs & Debugging

```bash
# View all logs
docker compose logs -f

# View specific service
docker compose logs -f api

# Check service status
docker compose ps
```

## Stop & Cleanup

```bash
# Stop services
docker compose down

# Stop and remove volumes (clears data)
docker compose down -v

```
