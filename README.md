## BlockHost Backend (setup stage)

This repo is an early "control plane" for BlockHost (see `agent.md`).

### Local dev (Docker)

Bring up API + Postgres + Redis + a local Minecraft Bedrock server:

`docker compose up --build`

Then:
- API: `http://localhost:8000` (`/health`)
- Bedrock: `localhost:19132/udp`

### Notes

- If you pull backend changes that add new DB columns (e.g. `servers.mc_config`), restart the API container.
- Setup-stage: `src/blockhost_backend/main.py` applies a small `ALTER TABLE ... IF NOT EXISTS` on startup to keep local dev moving.
# Blockhost
