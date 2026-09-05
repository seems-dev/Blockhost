# Spend & ops hardening plan

Follow-up after re-enabling `ensure_active_subscription_for_start` (paywall on start).

## Done

- [x] **P0 — Subscription gate on start**  
  Unpaid / suspended servers get HTTP 402 and do not start. Grace-period subscriptions still count as active for starts (by design).
- [x] **1.1** Delete S3 migration zip after successful migrate + on `DELETE /api/servers/{id}`  
  (still add AWS lifecycle on `migrations/` in console — ops step)
- [x] **1.2** S3 vars documented in `.env.example`; migrate/rebalance fail fast if S3 missing
- [x] **1.3** Per-server cooldown (`MIGRATION_COOLDOWN_SECONDS`) + hourly cap (`MAX_MIGRATIONS_PER_HOUR`)
- [x] **2.1** Plan `player_limit` clamps `max_players` on start / config / properties
- [x] **2.2** `MAX_SERVERS_PER_USER` enforced on create
- [x] **Post-migrate orphan cleanup** — after successful migrate, purge world dir on source agent; `DELETE /api/servers/{id}` also purges agent disk + S3

---

## Phase 1 — Stop silent cloud spend (high, next)

| # | Problem | Fix | Effort |
|---|---------|-----|--------|
| 1.1 | S3 `migrations/{server_id}/world.zip` never deleted | ~~Delete object on server delete + after successful migrate~~. Add AWS lifecycle rule on `migrations/` prefix (e.g. expire 14–30 days) as safety net. | S |
| 1.2 | S3 env not in `.env.example` / prod checks | ~~Document + fail-fast~~ | S |
| 1.3 | Rebalancer can migrate forever under load | ~~Cooldownoldown + hourly cap~~ | S |
| 1.4 | Proxy/agent EC2 always on | Keep rebalancer auto-shutdown of idle cloud nodes; alert if allocated RAM ≪ provisioned capacity for days. | M |

**Exit criteria:** deleting a server removes its S3 key; lifecycle rule active; migrate cooldown enforced.

---

## Phase 2 — Tie product limits to plans (high)

| # | Problem | Fix | Effort |
|---|---------|-----|--------|
| 2.1 | `player_limit` on plan unused | ~~Cap max_players~~ | S |
| 2.2 | No max servers per user | ~~`max_servers_per_user`~~ | S |
| 2.3 | Storage check incomplete | Ensure upload/backup/start all call `assert_world_size_within_plan`; wire or remove unused `premium_world_limit_gb`. | M |
| 2.4 | Create without paying is fine; start is gated | Keep create free; surface 402 in Flutter with deep-link to Plans (parse `detail.message` — partial: message parse done). | S |
| 2.5 | Placement ignores new server’s plan RAM | ~~Pass `required_ram_mb` on start/migrate~~ | S |

**Exit criteria:** unpaid cannot start; plan player/storage caps enforced; node pick respects plan size.

---

## Phase 3 — Backup durability vs cost (medium)

| # | Problem | Fix | Effort |
|---|---------|-----|--------|
| 3.1 | User backups only on node disk | Optional S3 backend for user backups (`BackupStorageBackend.s3`) with retention + delete on backup purge. | L |
| 3.2 | Docs say B2/S3; code is local-only | Update architecture doc after 3.1, or clearly mark “local only until phase 3”. | S |
| 3.3 | Large full-zip every migrate | Later: incremental / rsync-style agent transfer to cut S3 PUT/GET (keep S3 as fallback). | L |

**Exit criteria:** paid plans can opt into off-node backups with retention; orphaned backup objects cleaned.

---

## Phase 4 — Product / ledger cleanup (lower)

| # | Problem | Fix | Effort |
|---|---------|-----|--------|
| 4.1 | Stale `subscription_tier` / Google-auth paths | Remove or map to Razorpay plans; stop dead inventory concepts. | M |
| 4.2 | Blockcoins don’t gate anything | Either spend for discounts / free days, or hide from UI until productized. | M |
| 4.3 | Grace burns full RAM for 3 days | Optional: grace uses reduced limits (e.g. 50% RAM) while still allowing start. | S |
| 4.4 | Flutter 402 UX | Dedicated “Renew plan” sheet on start failure. | S |

---

## Suggested order of work

1. ~~Subscription gate~~ + ~~Phase 1.1–1.3 / 2.1 / 2.2 / 2.5~~ (this change). Redeploy API/worker.  
2. **Next:** Phase 2.3–2.4 + Phase 4.4 (storage checks + renew UX).  
3. **Ops:** S3 lifecycle rule on `migrations/`.  
4. **Later:** Phase 3 S3 user backups; Phase 4 blockcoins / grace tuning.

## Deploy note (control EC2)

After pulling this change:

```bash
cd /opt/blockhost/deploy
sudo docker compose -f docker-compose.prod.yml up -d --build api worker
```

Also set on control + agents: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BACKUP_BUCKET_NAME`.

Verify unpaid start returns **402** with `subscription_required`. Paid / grace-period start still works.
