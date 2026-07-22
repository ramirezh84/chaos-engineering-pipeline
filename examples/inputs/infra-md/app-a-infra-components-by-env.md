# APP-A — Infra Components by Environment (PROD vs PERF)

> Declares the deployed infrastructure components for **app-a (APP-A)** per
> environment. **Compute / scaling / deployment / ingress** rows are sourced from the **deploy repo**
> (`deploy-config`, the TFE/ECS deploy config). **Data tier / edge / dependency** rows are from the
> APP-A context briefs (`out/app-a-prod-context.md`, `out/app-a-perf-context.md`, themselves pinned to
> `svc-a-infra` `0000000…` + `svc-a-app` `0000000…`). Compiled 2026-07-21.
>
> Scope: PROD + PERF (the two environments whose deploy configs are staged). The deploy repo also
> contains `dev / cert / qa / hotfix / migration / testIR` for APP-A — not detailed here.

---

## A. Compute, scaling & deployment — from the deploy repo (authoritative)

| Component | PROD | PERF |
|---|---|---|
| AWS account | `111111111111` | `222222222222` |
| TFE org | `org-0000-PRD` | `org-0000-TEST` |
| Env label | `PROD` | `Perf` (app reports `TEST`, UAT-qualified) |
| Regions | us-east-1 (primary) + us-east-2 | us-east-1 (primary) + us-east-2 |
| ECS clusters | `ecs-cluster-prod-1` / `ecs-cluster-prod-2` | `ecs-cluster-perf-1` / `ecs-cluster-perf-2` |
| Launch type | **FARGATE** (capacity provider, base 1) | **FARGATE** (capacity provider, base 1) |
| Task size | 2048 CPU / 4096 MiB | 2048 CPU / 4096 MiB |
| Replica baseline | 1 | 1 |
| **Autoscaling** | **min 3 / max 6**, CPU + memory target 50% | **min 3 / max 6**, CPU + memory target 50% |
| Deploy model | blue/green; min-healthy 100% / max 200% | blue/green; min-healthy 100% / max 200% |
| Repave window | **SUN 20:00** | **WED 12:30** |
| Health check | ALB `/actuator/keepalive`; grace 240s; container check 10s/5s/×3 | same |
| App port | 8080 (ALB target group HTTPS/HTTP1) | 8080 |
| Ingress host (use1) | `app-a.use1.prod.example.com` | `app-a.use1.test.example.com` |
| Ingress host (use2) | `…host.prod.example.com` | `…host.prod.example.com` |
| Smoke URLs | `lookup-smoke-use1/use2.platform…prod…` | `lookup-smoke-use1/use2.platform…test…` |
| Data/cache TFE workspaces | `dbglbl` + `glblcache` (`clusterpgl`) | `dbglbl` + `glblcache` (`clusterfgl`) |
| Extra workspaces | ALB | ALB + **NLB** + **S3** |
| Chaos tooling | — | `enable_chaos` present (commented out) |
| ElastiCache egress | (token secret mounted) | port **6190** allowed |

## B. Data tier, edge & dependencies — from the context briefs (`svc-a-infra`)

| Component | PROD | PERF |
|---|---|---|
| AZs per region | **3** (PublicSubnet01/02/03) | **2** (PublicSubnet01/02) |
| Aurora PostgreSQL | 17.5, `db.r8g.large`, global cluster **`prod-global`**, db `lookup_prod` | 17.5, `db.r8g.large`, global cluster **`perf-global`**, db `lookup_perf`, IAM auth |
| DB writer / failover | single-writer **us-east-1**; active/passive writes, write-forwarding OFF; managed failover | single-writer **us-east-1**; `replica_count 1` / `secondary_replica_count 1` |
| DB backups | PITR + snap crons 22:00/23:00 UTC, 35-day snap retention; PI 7-day; deletion protection on | same crons/retention; deletion protection on |
| Hikari pool | connect-timeout 5000ms | max-pool 172, min-idle 10, connect-timeout 5000ms |
| ElastiCache Redis | `cache.m5.large`, global e1↔e2, auto-failover; **app caching enabled** | `cache.m5.large`, global, auto-failover; **app-side caching DISABLED** (`app.caching.redis.enabled: false`) |
| KMS | multi-region key (alias `appglb`) | multi-region key `kmsglb` (secondary us-east-2) |
| S3 | bidirectional CRR e1↔e2, 70-day backups; `db2s3` `cron(0 6 ? * Tue-Sat)` | CRR, 70-day backups; same batch schedule |
| Kafka topics | `…app-a.lookup_{created,updated}.v1` | `…app-a.lookup_{created,updated}.perf.v1` |
| Edge path | Route53 (SIMPLE CNAME, not health-based) → apinlb → apigw (9 SSO authorizers) → appnlb (TCP idle 360s) → appalb (idle 370s) → ECS | same components; SSO authorizers via **UAT** IdP |
| IdP / SSO | `idp.example.com` (`resource-REDACTED`) | **UAT** `idp-uat…` (`resource-REDACTED`) |
| external KMS | `ext-kms-gw01…/external-kmskms` (`…External-KMSKMS-PROD`) | **UAT** `ext-kms-gw01-uat…` (`…External-KMSKMS-UAT`) |
| feature-flag service | `proxy.prod.example.com:11443` | `proxy.uat.example.com:11443` |

## C. App-level resilience posture (same code both envs)
- **No circuit breaker anywhere.** resilience4j retry only on the 3 DB-inquiry ops (2 attempts / 500ms exp backoff).
- Kafka events published via **transactional outbox** (4 lookup lifecycle topics).
- Server idempotency on inbound writes via `request-id` header (postgres store, ~15-min retention).

## What's identical vs different

**Identical (notable — perf is a faithful load-test replica of prod's resiliency shape):**
- Two-region footprint (us-east-1 + us-east-2), Fargate, 2048/4096 task size, **autoscale 3→6 (CPU+mem 50%)**,
  blue/green (100%/200%), global Aurora + global ElastiCache, transactional-outbox Kafka, no circuit breaker.

**Different:**
- Account / TFE org / DNS domain (`prod` vs `test`), cluster names (`clusterp*` vs `clusterf*`).
- **AZs per region: prod 3, perf 2.**
- All external dependencies point at **PROD** vs **UAT** endpoints; Kafka topic suffix `.v1` vs `.perf.v1`.
- **App-side Redis caching disabled in perf**; repave window SUN 20:00 vs WED 12:30; perf adds NLB/S3 workspaces + a (commented) chaos-tool chaos flag.
