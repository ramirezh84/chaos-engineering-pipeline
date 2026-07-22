# ALS — Infra Components by Environment (PROD vs PERF)

> Declares the deployed infrastructure components for **account-locator-service (ALS)** per
> environment. **Compute / scaling / deployment / ingress** rows are sourced from the **deploy repo**
> (`CORE-app-config`, the TFE/ECS deploy config). **Data tier / edge / dependency** rows are from the
> ALS context briefs (`out/als-prod-context.md`, `out/als-perf-context.md`, themselves pinned to
> `01-als-locr-infra` `412a85c…` + `01-als-locr-app` `ebd30d2…`). Compiled 2026-07-21.
>
> Scope: PROD + PERF (the two environments whose deploy configs are staged). The deploy repo also
> contains `dev / cert / qa / hotfix / migration / testIR` for ALS — not detailed here.

---

## A. Compute, scaling & deployment — from the deploy repo (authoritative)

| Component | PROD | PERF |
|---|---|---|
| AWS account | `111111111111` | `222222222222` |
| TFE org | `000000-000001-PRD` | `000000-000002-TEST` |
| Env label | `PROD` | `Perf` (app reports `TEST`, UAT-qualified) |
| Regions | us-east-1 (primary) + us-east-2 | us-east-1 (primary) + us-east-2 |
| ECS clusters | `ecs0001-piaclctre1-v1` / `ecs0001-piaclctre2-v2` | `ecs0001-fiaclctr-v1` / `ecs0001-fiaclctre2-v1` |
| Launch type | **FARGATE** (capacity provider, base 1) | **FARGATE** (capacity provider, base 1) |
| Task size | 2048 CPU / 4096 MiB | 2048 CPU / 4096 MiB |
| Replica baseline | 1 | 1 |
| **Autoscaling** | **min 3 / max 6**, CPU + memory target 50% | **min 3 / max 6**, CPU + memory target 50% |
| Deploy model | blue/green; min-healthy 100% / max 200% | blue/green; min-healthy 100% / max 200% |
| Repave window | **SUN 20:00** | **WED 12:30** |
| Health check | ALB `/actuator/keepalive`; grace 240s; container check 10s/5s/×3 | same |
| App port | 8080 (ALB target group HTTPS/HTTP1) | 8080 |
| Ingress host (use1) | `locator.corebank.apps.111111111111.us-east-1.prod.example.internal` | `locator.corebank.apps.222222222222.us-east-1.test.example.internal` |
| Ingress host (use2) | `…111111111111.us-east-2.prod.example.internal` | `…222222222222.us-east-2.test.example.internal` |
| Smoke URLs | `locator-smoke-use1/use2.corebank…prod…` | `locator-smoke-use1/use2.corebank…test…` |
| Data/cache TFE workspaces | `lctrdbglbl` + `glblcache` (`piaclctrgl`) | `lctrdbglbl` + `glblcache` (`fiaclctrgl`) |
| Extra workspaces | ALB | ALB + **NLB** + **S3** |
| Chaos tooling | — | `enable_gremlin` present (commented out) |
| ElastiCache egress | (token secret mounted) | port **6190** allowed |

## B. Data tier, edge & dependencies — from the context briefs (`01-als-locr-infra`)

| Component | PROD | PERF |
|---|---|---|
| AZs per region | **3** (PublicSubnet01/02/03) | **2** (PublicSubnet01/02) |
| Aurora PostgreSQL | 17.5, `db.r8g.large`, global cluster **`prod-global`**, db `locator_prod` | 17.5, `db.r8g.large`, global cluster **`perf-global`**, db `locator_perf`, IAM auth |
| DB writer / failover | single-writer **us-east-1**; active/passive writes, write-forwarding OFF; managed failover | single-writer **us-east-1**; `replica_count 1` / `secondary_replica_count 1` |
| DB backups | PITR + snap crons 22:00/23:00 UTC, 35-day snap retention; PI 7-day; deletion protection on | same crons/retention; deletion protection on |
| Hikari pool | connect-timeout 5000ms | max-pool 172, min-idle 10, connect-timeout 5000ms |
| ElastiCache Redis | `cache.m5.large`, global e1↔e2, auto-failover; **app caching enabled** | `cache.m5.large`, global, auto-failover; **app-side caching DISABLED** (`appfw.caching.redis.enabled: false`) |
| KMS | multi-region key (alias `locatorgbl`) | multi-region key `kmslctrglb` (secondary us-east-2) |
| S3 | bidirectional CRR e1↔e2, 70-day backups; `db2s3` `cron(0 6 ? * Tue-Sat)` | CRR, 70-day backups; same batch schedule |
| Kafka topics | `…locator_{created,updated}-c001` | `…locator_{created,updated}-perf-c007` |
| Edge path | Route53 (SIMPLE CNAME, not health-based) → apinlb → apigw (9 ADFS authorizers) → appnlb (TCP idle 360s) → appalb (idle 370s) → ECS | same components; ADFS authorizers via **UAT** IDP |
| IDP / ADFS | `idp-g2.idp.example.com` (`RS-000000-00001-mbus-PROD`) | **UAT** `idp-uat-g2…` (`RS-000000-00002-mbus-UAT`) |
| Vault KMS | `api-apps-gw01-na…/vaultkms` (`…VaultKMS-PROD`) | **UAT** `api-apps-gw01-uat-na…` (`…VaultKMS-UAT`) |
| Split.io | `cloudproxy.prod.example.internal:11443` | `cloudproxy.uat.example.internal:11443` |

## C. App-level resilience posture (same code both envs)
- **No circuit breaker anywhere.** resilience4j retry only on the 3 DB-inquiry ops (2 attempts / 500ms exp backoff).
- Kafka events published via **transactional outbox** (4 locator lifecycle topics).
- Server idempotency on inbound writes via `request-id` header (postgres store, ~15-min retention).

## What's identical vs different

**Identical (notable — perf is a faithful load-test replica of prod's resiliency shape):**
- Two-region footprint (us-east-1 + us-east-2), Fargate, 2048/4096 task size, **autoscale 3→6 (CPU+mem 50%)**,
  blue/green (100%/200%), global Aurora + global ElastiCache, transactional-outbox Kafka, no circuit breaker.

**Different:**
- Account / TFE org / DNS domain (`prod` vs `test`), cluster names (`piaclctr*` vs `fiaclctr*`).
- **AZs per region: prod 3, perf 2.**
- All external dependencies point at **PROD** vs **UAT** endpoints; Kafka topic suffix `-c001` vs `-perf-c007`.
- **App-side Redis caching disabled in perf**; repave window SUN 20:00 vs WED 12:30; perf adds NLB/S3 workspaces + a (commented) Gremlin chaos flag.
