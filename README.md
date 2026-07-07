# SPADE Deep Research

SPADE Deep Research is a research API that coordinates multiple SPADE agents to
search the web, inspect arXiv papers, optionally query a local document
collection, and synthesize a cited report.

## Requirements

- Python 3.12.
- `uv` for dependency management.
- OpenAI-compatible chat and embedding credentials.
- A Tavily API key.
- An arXiv MCP server available at `ARXIV_MCP_URL`.

Optional:

- ChromaDB data for local knowledge-base retrieval.
- Docker and Docker Compose for containerized local runs.

## Quickstart

Clone the repository and install dependencies:

```bash
git clone https://github.com/olafmeneses/spade_deep_research.git
cd spade_deep_research
uv sync
```

Create your environment file and fill in the variables:

```bash
cp .env.example .env
```

Start your arXiv MCP service in a separate shell. The command depends on how you
installed that service; this API expects an MCP endpoint at the URL configured by
`ARXIV_MCP_URL`.

Run the API:

```bash
uv run uvicorn src.api:app --reload --port 8080
```

Open the interactive docs at http://localhost:8080/docs.

## Docker

See [docs/docker.md](docs/docker.md) for details.

## Benchmarks

See [docs/benchmarks.md](docs/benchmarks.md) for RACE/DeepResearch Bench setup,
environment variables, result locations, and dashboard generation. Public
full-100 run artifacts live under `bench/results/full100`.

## Tests

Run the default unit, API, and tool tests:

```bash
uv run --extra dev pytest
```
