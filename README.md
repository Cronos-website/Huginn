# Huginn

> Agent-pilotable fleet management for Linux VMs — a central hub, lightweight Go
> workers, a web dashboard, and an MCP façade so an AI agent can drive the fleet.

Huginn lets you manage a fleet of Linux VMs from one place: enroll machines with a
one-line installer, approve them from a dashboard, run controlled actions (or
opt-in unrestricted commands), keep workers up to date, and audit everything. An
[MCP](https://modelcontextprotocol.io) server exposes the same capabilities to an
external AI agent (e.g. "Hermes") without duplicating any business logic.

> **Status:** core iteration (hub + Go worker + MCP). The React dashboard and
> Kubernetes manifests land in a follow-up iteration. This is a remote
> command-execution system — read [docs/security.md](docs/security.md) before
> exposing it.

## Architecture

```
                ┌─────────────┐         ┌──────────────┐
   Hermes ──MCP─▶│             │         │  Dashboard   │
                │     HUB     │◀──REST──│   (React)    │
   Dashboard ──▶│ FastAPI +   │         └──────────────┘
                │ PostgreSQL  │
                │  + MCP srv  │
                └──────┬──────┘
                       │ REST (per-worker token auth, TLS)
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
     ┌───────┐     ┌───────┐     ┌───────┐
     │Worker │     │Worker │     │Worker │   ← one per Linux VM (Go)
     │ (VM1) │     │ (VM2) │     │ (VM3) │
     └───────┘     └───────┘     └───────┘
```

- **Hub** (`/hub`) — FastAPI + PostgreSQL. The source of truth: inventory,
  enrollment, authz, audit, the target worker version, and task routing.
- **Worker** (`/worker`) — a single static Go binary installed natively on each VM
  via systemd. It long-polls the hub for tasks (NAT-friendly), runs them locally,
  and reports results. No runtime dependencies.
- **MCP server** (`/mcp`) — a thin façade over the hub's REST API exposing tools
  for an external agent. No direct worker access, no duplicated logic.
- **Dashboard** (`/dashboard`) — React SPA *(iteration 2)*.

By default the worker **pulls** work from the hub over a single outbound HTTPS
connection, so it works behind NAT. A push listener can be enabled per-VM when the
machine is directly routable.

## Quickstart (local)

```bash
git clone https://github.com/Cronos-website/Huginn.git
cd Huginn
cp .env.example .env          # then edit secrets
cd deploy && docker compose up --build
```

This starts PostgreSQL, the hub (`:8000`) and the MCP server (`:9000`). A first
admin user is bootstrapped from `HUGINN_BOOTSTRAP_ADMIN_*`.

Enroll a VM (after generating an enrollment token via the API/dashboard):

```bash
curl -sSL https://<hub>/install.sh | HUB_URL=https://<hub> TOKEN=<token> bash
```

The VM appears as **PENDING**; approve it before it can receive any command.

See [docs/](docs/) for [architecture](docs/architecture.md),
[enrollment](docs/enrollment.md), [security](docs/security.md),
[deployment](docs/deployment.md), and [MCP integration](docs/mcp.md).

## Repository layout

| Path | What |
|---|---|
| `hub/` | FastAPI hub (Python 3.12+, SQLAlchemy 2 async, Alembic) |
| `worker/` | Go worker daemon (static binary, systemd) |
| `mcp/` | MCP server (Python, FastMCP) — façade over the hub |
| `dashboard/` | React SPA *(iteration 2)* |
| `deploy/` | docker-compose + Kubernetes manifests |
| `scripts/` | `install.sh` one-liner installer |
| `docs/` | documentation |

## Security

Auth on every endpoint, timing-safe secret comparison, TLS enforced for
hub↔worker in prod, secrets hashed at rest, no shell construction on the worker
(separated argv, never `sh -c`), opt-in + audited unrestricted mode, rate limits
and body caps on execution, SSRF allowlist on update sources, and an append-only
hash-chained audit log. Details in [docs/security.md](docs/security.md). Report
vulnerabilities per [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under [Apache-2.0](LICENSE).
