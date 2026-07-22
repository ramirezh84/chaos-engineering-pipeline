# Chaos Engineering Code & Infrastructure Scanner — Claude Code Runbook

**Target runtime:** Claude Code, Opus 4.8, 1M context, running on a VDI against a root folder
containing your service repos.
**Companion file:** `Chaos_Engineering_Scenario_Catalog_v2.xlsx` (the workbook already built —
this runbook produces new sheets that get appended to a *copy* of it).
**Read this whole file before running anything.** It is written to be pasted into Claude Code
section by section, or handed to Claude Code with the instruction "execute this runbook end to
end, phase by phase, checkpointing to disk as you go."

---

## 0. Purpose & operating principles

You are extracting **evidence**, not writing an assessment from memory or convention. The
output of this runbook is only useful to the chaos engineering team if every claim in it can be
traced to a file and a line. That is the entire point of doing this from code instead of from
diagrams and interviews.

### The accuracy contract (non-negotiable)

Every fact you record has exactly one of these statuses. Never invent a sixth.

| Status | Meaning |
|---|---|
| `CONFIRMED` | Found directly in code/config, with file + line + verbatim snippet attached. |
| `NOT_FOUND` | Searched using the listed patterns; genuinely absent. List the patterns you used. |
| `NOT_CONFIGURED` | No explicit value set; a library/framework default would apply. **Do not state what that default is** unless you can cite the exact library version's documented behavior — otherwise leave the value blank and mark it for human verification. Naming the library+version you found in the Dependency Census is enough; do not guess the number. |
| `AMBIGUOUS` | Multiple candidate values exist (e.g., a Maven property inherited from a parent POM you can't fully resolve statically, or a Spring profile-conditional bean). Record **every** candidate and its source. Never pick one. |
| `EXTERNAL_CONFIG` | The key/reference exists in code, but its value is resolved at runtime from Secrets Manager, Parameter Store, or a vault (external-vault-style pattern). Record the reference pattern, never attempt to guess the resolved value. |
| `NOT_APPLICABLE` | The category doesn't apply to this repo (e.g., a Kafka consumer audit on a repo with no Kafka consumers). |

**If you catch yourself about to write a plausible-sounding number that you did not read
directly from a file — stop.** That is exactly the failure mode this contract exists to prevent.
Mark it `NOT_CONFIGURED` or `AMBIGUOUS` instead and move on. An honest gap is worth more than a
confident guess; the chaos team will build experiments on top of what you write here.

### Evidence object shape

Every extracted fact, in every phase, uses this shape (JSON):

```json
{
  "value": "<the value, or null if not found>",
  "status": "CONFIRMED",
  "file": "src/main/resources/application-prod.yml",
  "line": 42,
  "snippet": "connectionTimeout: 3000",
  "source_note": "optional — e.g. 'inherited from parent pom property db.timeout'"
}
```

### ultrathink usage

Claude Code's extended-thinking budget ("ultrathink") should be invoked explicitly at these
specific junctures — not everywhere, since that would burn budget on mechanical extraction work
that doesn't need it:

- Before classifying an `AMBIGUOUS` finding (is it really ambiguous, or did you just not look
  hard enough at the property inheritance chain?).
- Before computing retry-amplification call graphs (Phase 2 Category 4) — this requires tracing
  caller→callee edges across repos, which benefits from deliberate reasoning.
- Before writing any row in the Anti-Pattern Sweep — confirm the surrounding method actually
  exhibits the pattern rather than pattern-matching on the grep hit alone.
- Before drafting `standards.md` in Phase 6 — synthesizing 34 repos' findings into a coherent
  set of rules is exactly the kind of task that benefits from deep reasoning over breadth.

When you reach these points, explicitly think step by step with your full reasoning budget
before writing the output.

---

## 1. Environment & preflight

### 1.1 Expected layout

```
<root>/
  svc-a-app/
  svc-a-infra/
  svc-d-app/
  svc-d-infra/
  ...
  17-dal-coda-app/
  17-dal-coda-infra/
  scan-output/            <- you create this
  spec.md                 <- this file
  Chaos_Engineering_Scenario_Catalog_v2.xlsx   <- the companion workbook
```

Folder naming convention: `<num>-<acronym>-<apg>-<app|infra>`. This is self-describing — do
**not** ask for or build a separate manual repo-to-service mapping file. Parse it.

### 1.2 Preflight checks (run once, record results, do not proceed past a failed check silently)

Run these and write results to `scan-output/preflight.json`:

```bash
# from <root>
python3 --version
command -v mvn && mvn --version || echo "MAVEN_NOT_AVAILABLE"
command -v java && java --version || echo "JAVA_NOT_AVAILABLE"
command -v terraform && terraform --version || echo "TERRAFORM_NOT_AVAILABLE"
command -v cdk && cdk --version || echo "CDK_NOT_AVAILABLE"
command -v git && git --version
command -v rg && rg --version || echo "RIPGREP_NOT_AVAILABLE — fall back to grep"
python3 -c "import openpyxl; print('openpyxl', openpyxl.__version__)" || echo "OPENPYXL_NOT_AVAILABLE — pip install openpyxl"
python3 -c "import pandas; print('pandas', pandas.__version__)" || echo "PANDAS_NOT_AVAILABLE — pip install pandas"
```

If `mvn`/`java` are unavailable: Phase 1's Maven resolution falls back to static POM parsing,
and any version/property that can only be resolved by actually running Maven gets marked
`AMBIGUOUS` rather than guessed. This is expected to happen for some repos — do not treat it as
a blocker, just be honest about it in the output.

If `openpyxl`/`pandas` are unavailable, try `pip install --user openpyxl pandas` before falling
back to a CSV-only consolidation (Phase 5 has a CSV fallback path — see there).

### 1.3 Repo discovery

```bash
cd <root>
ls -d */ | grep -E '^[0-9]+-[a-z0-9]+-[a-z0-9]+-(app|infra)/$'
```

Parse each matching folder name into `{num, acronym, apg, kind}` and pair `app`/`infra` by
matching `{num, acronym, apg}`. Write `scan-output/repo-registry.json`:

```json
[
  {
    "num": "01",
    "acronym": "app-a",
    "apg": "grp-a",
    "app_repo": "svc-a-app",
    "infra_repo": "svc-a-infra",
    "app_path": "/abs/path/svc-a-app",
    "infra_path": "/abs/path/svc-a-infra",
    "app_present": true,
    "infra_present": true
  }
]
```

**If a repo has no pair** (app exists, infra doesn't, or vice versa — this happened with at
least one service in the original diagram-based inventory), record `app_present`/`infra_present`
as `false` for the missing side. Do not skip the pairing entry — a missing infra repo for a
known app repo is itself a fact worth surfacing, not a reason to drop the row.

**If a folder doesn't match the naming convention at all**, list it separately in
`repo-registry.json` under an `"unmatched"` array with its raw folder name, and flag it in the
final report rather than silently ignoring or silently forcing a match.
## 2. Phase 0 — Sync & Provenance

Every fact this runbook produces must trace to an exact commit. Do this before anything else,
and re-do it (or at least re-check it) if the run spans more than a day.

For each repo in `repo-registry.json` (both app and infra paths):

```bash
cd <repo_path>
git status --porcelain          # check for a dirty working tree FIRST
git rev-parse HEAD              # sha before pull
git rev-parse --abbrev-ref HEAD # current branch
```

**Decision rule — do not force anything:**
- If `git status --porcelain` is non-empty (dirty tree): **do not** `git checkout`/`git stash`/
  `git reset` automatically. Record `pull_status: "DIRTY_WORKTREE"`, note what's uncommitted, and
  scan whatever is currently checked out. Discarding someone's local changes without asking is
  not this runbook's call to make.
- If the branch isn't `main`: record `pull_status: "NOT_ON_MAIN"`, note the current branch, and
  proceed with whatever is checked out rather than silently switching branches.
- Otherwise: `git pull origin main`, record the resulting SHA.

Write `scan-output/provenance.json` (one entry per repo, both app and infra):

```json
{
  "repo": "svc-a-app",
  "path": "/abs/path/svc-a-app",
  "branch": "main",
  "sha_before_pull": "abc123...",
  "sha_after_pull": "def456...",
  "pulled_at_utc": "2026-07-21T18:00:00Z",
  "pull_status": "OK",
  "notes": ""
}
```

`pull_status` vocabulary: `OK | DIRTY_WORKTREE | NOT_ON_MAIN | FAILED | DETACHED_HEAD`.

This file is what lets the chaos team (and you, later) answer "was this finding still true when
we ran the experiment?" — treat it as load-bearing, not a formality.

---

## 3. Phase 1 — Repo Census

Fast, cheap pass per **app** repo (not infra yet — that's Phase 3) to establish what you're
about to scan in depth. This runs before the parallel deep-extraction phase so that phase's
subagent prompts can be pre-loaded with accurate structural facts instead of re-discovering them.

For each app repo:

1. **Module tree.** Read the root/parent `pom.xml`'s `<modules>` block. For each module, read its
   `pom.xml` for `artifactId`, `packaging`. This is a multi-module Maven project — do not assume
   a single `pom.xml` tells the whole story.

2. **Dependency version resolution.** Two paths:
   - **Preferred (if `mvn`+`java` available):** `mvn help:effective-pom -f pom.xml` from the repo
     root, capturing fully-resolved versions for every module. This is `CONFIRMED`-grade —
     Maven has actually done the property/BOM resolution for you.
   - **Fallback (static):** Parse `<properties>` and `<dependencyManagement>` in the parent POM,
     then each module's `<dependencies>`. If a version is a property reference
     (`${spring-boot.version}`) trace it to its `<properties>` definition. If it's inherited
     from a parent POM or BOM import you cannot resolve statically (e.g., an external corporate
     parent BOM not present in this repo), mark it `AMBIGUOUS` and record the property name and
     every place it's referenced.

   Target dependencies to resolve (per module, since versions can differ by module):
   `spring-boot-starter*`, `resilience4j-*`, `spring-cloud-starter-openfeign` (or plain
   `feign-core`), `HikariCP` (often transitive via spring-boot-starter-jdbc — note if not a
   direct dependency), `spring-kafka` / `kafka-clients`, `aws-sdk-java` v1 vs `software.amazon.awssdk` v2,
   any `aws-jdbc-wrapper` / `postgres-iam` driver artifact, a Redis client (`lettuce-core` /
   `jedis`).

3. **Spring profile inventory.** `find . -name "application*.yml" -o -name "application*.yaml" -o -name "application*.properties"`
   across all modules. List every profile-suffixed file found (`application-perf.yml`,
   `application-prod.yml`, etc.) and every `@Profile(...)` annotation in code.

Write `scan-output/<app_repo>/census.json`:

```json
{
  "repo": "svc-a-app",
  "sha": "def456...",
  "build_tool": "maven",
  "modules": [
    {"path": "app-a-api", "artifactId": "app-a-api", "packaging": "jar"},
    {"path": "app-a-core", "artifactId": "app-a-core", "packaging": "jar"}
  ],
  "parent_pom": {"path": "pom.xml", "groupId": "...", "artifactId": "...", "version": "..."},
  "resolution_method": "mvn_effective_pom",
  "dependency_versions": {
    "spring-boot": {"value": "3.2.4", "status": "CONFIRMED", "source": "mvn help:effective-pom"},
    "resilience4j": {"value": "2.1.0", "status": "CONFIRMED", "source": "mvn help:effective-pom"},
    "hikaricp": {"value": null, "status": "NOT_FOUND", "source": "no direct or transitive declaration found — likely spring-boot-starter-jdbc managed; version not independently pinned"},
    "kafka-clients": {"value": "3.6.1", "status": "CONFIRMED", "source": "mvn help:effective-pom"}
  },
  "spring_profiles_detected": ["default", "perf", "prod"],
  "config_files": [
    "app-a-api/src/main/resources/application.yml",
    "app-a-api/src/main/resources/application-perf.yml",
    "app-a-api/src/main/resources/application-prod.yml"
  ]
}
```

This file becomes the input manifest each Phase 2 subagent is handed — it should not need to
rediscover the module structure from scratch.
## 4. Phase 2 — Parallel Application-Repo Extraction

### 4.1 Architecture

One subagent per **app** repo, covering **all** scan categories from the App Scan Instructions
tab in a single pass (do not spawn one subagent per category — a subagent with Opus 4.8's 1M
context can hold a repo's entire relevant surface area at once, and one thorough pass per repo
minimizes coordination overhead across a 17-repo fleet).

Run in **batches of 5–6 parallel subagents** (Task tool calls issued together in one message).
With 17 app repos, that's 3 batches. Adjust the batch size down if the environment shows signs
of throttling; there is no correctness reason to keep it at exactly 5–6.

**Before spawning each batch**, check `scan-output/<repo>/findings.json` — if it already exists
and is valid JSON, skip that repo (this is what makes the run resumable after an interruption).

### 4.2 Subagent prompt template (Task tool)

Use this verbatim, filling in `{repo_path}`, `{repo_name}`, and the contents of that repo's
`census.json`:

```
You are performing a code-evidence scan of exactly one repository: {repo_name} at {repo_path}.

Your census for this repo (already established, do not re-derive):
{contents of scan-output/{repo_name}/census.json}

Read the App Scan Instructions methodology below and apply it to THIS repo only. Do not scan
any other repo. Do not write to any file outside scan-output/{repo_name}/findings.json.

--- METHODOLOGY (verbatim from the App Scan Instructions tab) ---
[paste the 8 category rows from the App Scan Instructions tab here: Dependency & Version
Census, Resilience Config, Timeout Chain, Retry Amplification Inputs, Kafka Consumer/Producer
Config, Idempotency Evidence, Anti-Pattern Sweep, Spring Profile Inventory — plus the
non-negotiable rules and evidence-object shape from Section 0 of this runbook]
--- END METHODOLOGY ---

Accuracy contract: every fact needs {value, status, file, line, snippet}. Status is exactly one
of CONFIRMED | NOT_FOUND | NOT_CONFIGURED | AMBIGUOUS | EXTERNAL_CONFIG | NOT_APPLICABLE. If you
are not certain a value is correct because you read it directly from a file, mark it AMBIGUOUS
or NOT_CONFIGURED instead of guessing. This is the single most important rule in this task.

For the Anti-Pattern Sweep and Idempotency Evidence categories specifically: ultrathink before
flagging anything. Confirm by reading the surrounding method, not just the grep hit, that the
pattern is actually present.

Write your complete findings to scan-output/{repo_name}/findings.json using the schema below.
Do not summarize in your response — write the file, then report back only a one-line count of
findings per category and any blocking issues you hit (e.g., a repo that won't parse, a module
you couldn't resolve).

--- OUTPUT SCHEMA ---
{
  "repo": "{repo_name}",
  "sha": "<from provenance.json>",
  "scanned_at_utc": "<timestamp>",
  "dependency_versions": { /* same shape as census.json dependency_versions, but complete */ },
  "resilience_config": [
    {
      "client_or_method": "string",
      "breaker_name": "string or null",
      "annotation_location": {"file": "", "line": 0, "snippet": ""},
      "failure_rate_threshold": {evidence object},
      "slow_call_rate_threshold": {evidence object},
      "slow_call_duration_threshold": {evidence object},
      "wait_duration_in_open_state": {evidence object},
      "permitted_calls_half_open": {evidence object},
      "has_fallback_method": {evidence object, value is true/false},
      "retry": {
        "max_attempts": {evidence object},
        "wait_duration": {evidence object},
        "has_jitter": {evidence object}
      },
      "bulkhead": {"max_concurrent_calls": {evidence object}} 
    }
  ],
  "timeout_chain": [
    {"layer": "hikari|feign|resttemplate|webclient|timelimiter|kafka-consumer|kafka-producer",
     "key": "string", "evidence": {evidence object}, "unit": "ms|s"}
  ],
  "http_clients_without_timeout": [
    {"class_or_bean": "string", "file": "", "line": "", "snippet": ""}
  ],
  "retry_amplification_edges": [
    {"caller": "string", "callee": "string", "retry_count": 0, "backoff_type": "fixed|exponential|none",
     "evidence": {evidence object}}
  ],
  "kafka_consumers": [
    {"listener_method": "", "topic": "", "group_id": "",
     "auto_offset_reset": {evidence object},
     "enable_auto_commit": {evidence object},
     "max_poll_interval_ms": {evidence object},
     "error_handler_present": {evidence object},
     "dlq_configured": {evidence object},
     "concurrency": {evidence object}}
  ],
  "kafka_producers": [
    {"producer_bean_or_template": "", "acks": {evidence object}, "enable_idempotence": {evidence object},
     "retries": {evidence object}}
  ],
  "idempotency_evidence": [
    {"handler_or_writer": "", "evidence_type": "upsert|on_conflict|dedup_table|event_id_check|none_found",
     "file": "", "line": "", "snippet": "", "suspicion_level": "low|medium|high"}
  ],
  "anti_patterns": [
    {"pattern_id": "EXC_SWALLOW|UNBOUNDED_ASYNC|HARDCODED_SLEEP|NO_TIMEOUT_CLIENT|BLOCKING_IN_LISTENER|NO_TX_BOUNDARY|<new>",
     "description": "", "file": "", "line": "", "snippet": "", "severity_hint": "low|medium|high"}
  ],
  "spring_profiles": {
    "perf": {"config_files": ["..."], "raw_keys": { "key.path": {evidence object} }},
    "prod": {"config_files": ["..."], "raw_keys": { "key.path": {evidence object} }}
  },
  "blocking_issues": ["free text — anything that stopped you from completing a category"]
}
--- END SCHEMA ---

Populate spring_profiles.perf.raw_keys and spring_profiles.prod.raw_keys with the EFFECTIVE
value per profile (base application.yml merged with the profile-specific override file) for
every key you found anywhere in resilience_config, timeout_chain, kafka_consumers, and
kafka_producers above — this is what lets the consolidation step diff perf vs prod later. Do not
just diff the profile files against each other yourself; report both effective value sets and let
the consolidation script do the diff.
```

### 4.3 Handling a repo that doesn't fit the pattern

Some repos may have no Kafka usage, no Feign clients, or no profile files at all (e.g., a
stateless edge/BFF service). Mark the corresponding category `NOT_APPLICABLE` with a one-line
note on what you checked for — do not omit the category key from the JSON, and do not leave it
as an empty array without a status note distinguishing "checked, has none" from "not checked."

### 4.4 A repo with no dedicated documentation / thin repo

If a repo's structure is minimal or unusual (per the workbook's Service Inventory, at least one
service had no diagram at all before this scan), scan it exactly the same way — code-derived
facts are exactly what should fill that gap. Do not lower the rigor bar because the prior
documentation was thin; if anything, flag in `blocking_issues` that this repo previously had no
architecture documentation, so the chaos team knows this scan is the first source of truth for it.
## 5. Phase 3 — Parallel Infrastructure-Repo Extraction

Same architecture as Phase 2: one subagent per **infra** repo, batches of 5–6, resumable via
checking for an existing `scan-output/<infra_repo>/infra_findings.json` before spawning.

### 5.1 Subagent prompt template

```
You are performing an infrastructure-evidence scan of exactly one repository: {infra_repo_name}
at {infra_repo_path}.

Step 1 — detect the IaC tool actually used in THIS repo. Check for (in this order): *.tf files,
cdk.json, a SAM/CloudFormation template.yaml with AWSTemplateFormatVersion, serverless.yml. Do
not assume Terraform or any other tool — confirm by file presence.

Step 2 — apply the Infra Scan Instructions methodology below, using the extraction approach that
matches the tool you detected.

--- METHODOLOGY (verbatim from the Infra Scan Instructions tab) ---
[paste the Step-1 tool-detection table and Step-2 component-category table from the Infra Scan
Instructions tab, plus the non-negotiable rules]
--- END METHODOLOGY ---

Same accuracy contract as always: CONFIRMED | NOT_FOUND | NOT_CONFIGURED | AMBIGUOUS |
EXTERNAL_CONFIG | NOT_APPLICABLE. A Terraform variable whose value comes from a .tfvars file you
don't have, or from a value only known at `terraform plan` time, is AMBIGUOUS — record the
variable name and every candidate default you can find, never pick one.

If `terraform`, `cdk`, or an equivalent CLI is available and you can run it read-only (`terraform
plan` with no apply, `cdk synth`) to fully resolve values, prefer that over static parsing and
mark the result CONFIRMED with resolution_method noted. Never run anything that could modify
real infrastructure (no apply, no deploy) — this is a read-only evidence-gathering pass.

Write scan-output/{infra_repo_name}/infra_findings.json using the schema below.

--- OUTPUT SCHEMA ---
{
  "repo": "{infra_repo_name}",
  "sha": "<from provenance.json>",
  "scanned_at_utc": "<timestamp>",
  "iac_tool": "terraform|cdk|cloudformation|sam|serverless|unknown",
  "resolution_method": "static_parse|plan_output|synth_output|unresolvable",
  "resources": [
    {
      "component_category": "Compute|Data store|Cache|Messaging / broker|Edge / ingress|Async / eventing|Secrets & config|Network boundary|DR / region posture",
      "resource_type": "e.g. aws_ecs_service, AWS::RDS::DBCluster, aws_elasticache_replication_group",
      "resource_id_or_name": "string",
      "attributes": {
        "<attribute name>": {evidence object}
      },
      "file": "", "line_or_block": ""
    }
  ],
  "blocking_issues": ["free text"]
}
--- END SCHEMA ---

Priority attributes per category (extract these even if you extract nothing else in that
category — these are the ones the chaos catalog depends on):
- Compute: launch_type, desired_count, deployment_minimum_healthy_percent, cpu, memory, any
  container environment variable that looks like it overrides an app-side timeout/pool/retry
  setting (flag these explicitly — they're the app-vs-infra divergence risk).
- Data store: engine, engine_version, instance_class, multi_az, whether an RDS Proxy resource
  exists AND which services' security groups reference it (this confirms or refutes proxy-vs-
  direct connection per service — the single highest-value fact in this category), Aurora Global
  Database region membership and failover priority.
- Cache: engine, node_type, multi_az, num_cache_clusters/shards, auth_token_enabled, whether the
  cluster is referenced by more than one service (shared vs per-service).
- Messaging/broker: broker type and version, encryption_in_transit, client_auth mechanism,
  whether the network path is on-prem (Direct Connect/Transit Gateway) or in-VPC or SaaS
  (PrivateLink).
- Edge/ingress: idle_timeout (ALB) or the equivalent NLB attribute, deregistration_delay, API
  Gateway throttle burst_limit/rate_limit and integration timeout, Route 53 routing_policy and
  TTL.
- Async/eventing: SQS visibility_timeout and redrive_policy.maxReceiveCount, EventBridge
  schedule_expression, Lambda reserved_concurrent_executions.
- Secrets & config: rotation_enabled, IAM principals granted access, KMS key policy grantees.
- DR/region posture: Aurora Global Database secondary region(s), Route 53 failover record set,
  any DR-orchestration resource (state machine, alarm-triggered automation) and what it targets.
```

---

## 6. Phase 4 — Profile Diff Resolution

This runs after Phase 2 completes for a given app repo (can run per-repo as soon as that repo's
`findings.json` lands — does not need to wait for all 17).

For each app repo's `findings.json.spring_profiles`:

1. Take the union of every key present in `perf.raw_keys` and `prod.raw_keys`.
2. For each key, compare the two effective values.
3. Classify:
   - **`expected`**: hostnames, endpoints, ARNs, credential references, log levels, feature-flag
     keys pointing at different environments' flag services.
   - **`suspicious`**: anything that changes runtime BEHAVIOR rather than just its target —
     timeouts, pool sizes (Hikari max pool, Kafka concurrency), retry counts, breaker thresholds,
     batch/chunk sizes, thread pool sizes, scheduled-job cron/rate expressions.
   - **`missing_in_one`**: key present in one profile's raw_keys and absent from the other. Do
     **not** assume this falls back cleanly to a base `application.yml` value — note it as a gap
     to verify, unless you can show the base file's value directly (in which case cite that as
     the effective value for the profile missing an override, with status CONFIRMED and a note
     that it comes from the base file, not the profile file).

Write `scan-output/<app_repo>/profile-diff.json`:

```json
{
  "repo": "svc-a-app",
  "diffs": [
    {
      "key": "resilience4j.circuitbreaker.instances.extClient.slowCallDurationThreshold",
      "perf_value": {evidence object},
      "prod_value": {evidence object},
      "classification": "suspicious",
      "note": "differs by 3x between environments — recheck which is intentional"
    }
  ]
}
```

This file is what resolves every `PERF Validity: Medium/Low` flag already sitting in the
Scenario Catalog tab — the consolidation step (Phase 5) links each suspicious diff back to the
scenario(s) it affects.
## 7. Phase 5 — Consolidation

Run this **after** Phases 2–4 have produced their JSON files for all (or as many as completed)
repos. It never modifies the source workbook — it reads it and writes a new file.

Save the script below as `consolidate.py` next to `spec.md`.

```python
#!/usr/bin/env python3
"""
consolidate.py — merge scan-output/**/*.json into the chaos engineering workbook.

Usage:
    python3 consolidate.py --workbook Chaos_Engineering_Scenario_Catalog_v2.xlsx \
                            --scan-output ./scan-output \
                            --out Chaos_Engineering_Scenario_Catalog_v3_scanned.xlsx

Never overwrites the input workbook. Always writes to --out. Safe to re-run as more
scan-output/*/findings.json files land — it always reads whatever is currently on disk.
"""
import argparse, json, os, sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

ARIAL = "Arial"; NAVY = "1F3864"
thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
MISSING = {"value": None, "status": "MISSING_FROM_SCAN_OUTPUT", "file": "", "line": "", "snippet": ""}

def ev(d, *path):
    """Safely walk a nested evidence dict; return a MISSING stub if anything along the path is
    absent. Never fabricates a value — a gap in the subagent's JSON is reported as a gap."""
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return dict(MISSING)
        cur = cur[p]
    if isinstance(cur, dict) and "status" in cur:
        return cur
    return {"value": cur, "status": "UNVERIFIED_SHAPE", "file": "", "line": "", "snippet": ""}

def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            print(f"WARNING: {path} is not valid JSON — skipping", file=sys.stderr)
            return None

def hdr(ws, row, headers, widths=None):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = Font(name=ARIAL, size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BORDER
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

def body(ws, row, values):
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.font = Font(name=ARIAL, size=9)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.border = BORDER

def evtxt(e):
    if e is None:
        return "MISSING_FROM_SCAN_OUTPUT"
    val = e.get("value")
    status = e.get("status", "?")
    file = e.get("file", "")
    line = e.get("line", "")
    loc = f" ({file}:{line})" if file else ""
    return f"{val if val is not None else '—'} [{status}]{loc}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", required=True)
    ap.add_argument("--scan-output", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    wb = openpyxl.load_workbook(args.workbook)
    registry = load_json(os.path.join(args.scan_output, "repo-registry.json")) or []

    inv_ws = wb["Service Inventory"]
    acr_to_service = {}
    for r in range(2, inv_ws.max_row + 1):
        service = inv_ws.cell(row=r, column=2).value
        acr = inv_ws.cell(row=r, column=3).value
        if acr:
            acr_to_service[acr.lower()] = service

    def service_name(acronym):
        return acr_to_service.get((acronym or "").lower(), f"UNKNOWN ({acronym})")

    # remove any previously-generated scan sheets so re-runs don't duplicate rows
    for name in ["Scan Provenance", "Resilience Truth Table", "Timeout Chain",
                 "Kafka Consumer Audit", "Idempotency Suspicion Ranking",
                 "Anti-Pattern Findings", "Profile Mismatch Register",
                 "Infra Component Register"]:
        if name in wb.sheetnames:
            del wb[name]

    # ---------------- Scan Provenance ----------------
    ws = wb.create_sheet("Scan Provenance")
    hdr(ws, 1, ["Repo", "Path", "Branch", "SHA (after pull)", "Pulled At (UTC)",
                "Pull Status", "Notes"], [26, 40, 12, 14, 20, 16, 30])
    prov = load_json(os.path.join(args.scan_output, "provenance.json")) or []
    r = 2
    for p in prov:
        body(ws, r, [p.get("repo"), p.get("path"), p.get("branch"), p.get("sha_after_pull"),
                     p.get("pulled_at_utc"), p.get("pull_status"), p.get("notes")])
        r += 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:G{max(r-1,1)}"

    # ---------------- Resilience Truth Table ----------------
    ws = wb.create_sheet("Resilience Truth Table")
    hdr(ws, 1, ["Service", "Repo", "Client / Method", "Breaker Name",
                "Failure Rate Threshold", "Slow-Call Rate Threshold", "Slow-Call Duration Threshold",
                "Wait Duration (Open)", "Half-Open Permits", "Has Fallback?",
                "Retry Max Attempts", "Retry Has Jitter?", "Bulkhead Max Concurrent",
                "Suggested Scenario Targets (heuristic — review)"],
        [16,20,22,16,20,20,22,16,14,12,12,12,16,26])
    r = 2
    for repo_entry in registry:
        if not repo_entry.get("app_present"):
            continue
        findings = load_json(os.path.join(args.scan_output, repo_entry["app_repo"], "findings.json"))
        if not findings:
            continue
        svc = service_name(repo_entry["acronym"])
        for rc in findings.get("resilience_config", []):
            suggestions = []
            if ev(rc, "slow_call_rate_threshold").get("status") in ("NOT_CONFIGURED", "NOT_FOUND"):
                suggestions.append("CE-APP-02")
            if ev(rc, "failure_rate_threshold").get("status") in ("NOT_CONFIGURED", "NOT_FOUND"):
                suggestions.append("CE-APP-01")
            if ev(rc, "has_fallback_method").get("value") is False:
                suggestions.append("CE-APP-01")
            if ev(rc, "retry", "has_jitter").get("status") in ("NOT_CONFIGURED", "NOT_FOUND"):
                suggestions.append("CE-APP-03")
            body(ws, r, [
                svc, repo_entry["app_repo"], rc.get("client_or_method"), rc.get("breaker_name"),
                evtxt(ev(rc, "failure_rate_threshold")),
                evtxt(ev(rc, "slow_call_rate_threshold")),
                evtxt(ev(rc, "slow_call_duration_threshold")),
                evtxt(ev(rc, "wait_duration_in_open_state")),
                evtxt(ev(rc, "permitted_calls_half_open")),
                evtxt(ev(rc, "has_fallback_method")),
                evtxt(ev(rc, "retry", "max_attempts")),
                evtxt(ev(rc, "retry", "has_jitter")),
                evtxt(ev(rc, "bulkhead", "max_concurrent_calls")),
                ", ".join(sorted(set(suggestions))) or "—",
            ])
            r += 1
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:N{max(r-1,1)}"

    # ---------------- Timeout Chain (merged app + infra) ----------------
    ws = wb.create_sheet("Timeout Chain")
    hdr(ws, 1, ["Service", "Repo", "Layer", "Key", "Value", "Status", "File", "Line"],
        [16, 20, 18, 30, 14, 16, 34, 8])
    r = 2
    for repo_entry in registry:
        acr = repo_entry["acronym"]
        svc = service_name(acr)
        if repo_entry.get("app_present"):
            findings = load_json(os.path.join(args.scan_output, repo_entry["app_repo"], "findings.json"))
            if findings:
                for t in findings.get("timeout_chain", []):
                    e = t.get("evidence", {})
                    body(ws, r, [svc, repo_entry["app_repo"], t.get("layer"), t.get("key"),
                                 e.get("value"), e.get("status"), e.get("file"), e.get("line")])
                    r += 1
        if repo_entry.get("infra_present"):
            infra = load_json(os.path.join(args.scan_output, repo_entry["infra_repo"], "infra_findings.json"))
            if infra:
                for res in infra.get("resources", []):
                    if res.get("component_category") == "Edge / ingress":
                        for attr_name, e in res.get("attributes", {}).items():
                            if attr_name in ("idle_timeout", "deregistration_delay",
                                             "integration_timeout", "throttle_burst_limit",
                                             "throttle_rate_limit"):
                                body(ws, r, [svc, repo_entry["infra_repo"],
                                             f"infra:{res.get('resource_type')}",
                                             attr_name, e.get("value"), e.get("status"),
                                             e.get("file"), e.get("line")])
                                r += 1
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:H{max(r-1,1)}"

    # ---------------- Kafka Consumer Audit ----------------
    ws = wb.create_sheet("Kafka Consumer Audit")
    hdr(ws, 1, ["Service", "Repo", "Listener Method", "Topic", "Group ID",
                "auto.offset.reset", "enable.auto.commit", "max.poll.interval.ms",
                "Error Handler?", "DLQ Configured?", "Suggested Scenario Targets"],
        [16, 20, 22, 20, 18, 16, 16, 16, 14, 14, 22])
    r = 2
    for repo_entry in registry:
        if not repo_entry.get("app_present"):
            continue
        findings = load_json(os.path.join(args.scan_output, repo_entry["app_repo"], "findings.json"))
        if not findings:
            continue
        svc = service_name(repo_entry["acronym"])
        for kc in findings.get("kafka_consumers", []):
            suggestions = []
            offset_reset = ev(kc, "auto_offset_reset")
            if str(offset_reset.get("value")).lower() == "latest":
                suggestions.append("CE-KFK-04")  # candidate silent-message-loss risk — human review
            if ev(kc, "error_handler_present").get("value") is False:
                suggestions.append("CE-KFK-04")
            if ev(kc, "dlq_configured").get("status") in ("NOT_CONFIGURED", "NOT_FOUND"):
                suggestions.append("CE-KFK-04")
                suggestions.append("CE-PIPE-05")
            body(ws, r, [
                svc, repo_entry["app_repo"], kc.get("listener_method"), kc.get("topic"),
                kc.get("group_id"), evtxt(offset_reset), evtxt(ev(kc, "enable_auto_commit")),
                evtxt(ev(kc, "max_poll_interval_ms")), evtxt(ev(kc, "error_handler_present")),
                evtxt(ev(kc, "dlq_configured")), ", ".join(sorted(set(suggestions))) or "—",
            ])
            r += 1
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:K{max(r-1,1)}"

    # ---------------- Idempotency Suspicion Ranking ----------------
    ws = wb.create_sheet("Idempotency Suspicion Ranking")
    hdr(ws, 1, ["Service", "Repo", "Handler / Writer", "Evidence Type", "Suspicion Level",
                "File", "Line", "Snippet"], [16, 20, 26, 18, 14, 30, 8, 34])
    r = 2
    rows_to_sort = []
    for repo_entry in registry:
        if not repo_entry.get("app_present"):
            continue
        findings = load_json(os.path.join(args.scan_output, repo_entry["app_repo"], "findings.json"))
        if not findings:
            continue
        svc = service_name(repo_entry["acronym"])
        for ie in findings.get("idempotency_evidence", []):
            level_rank = {"high": 0, "medium": 1, "low": 2}.get(ie.get("suspicion_level"), 3)
            rows_to_sort.append((level_rank, [svc, repo_entry["app_repo"],
                                               ie.get("handler_or_writer"), ie.get("evidence_type"),
                                               ie.get("suspicion_level"), ie.get("file"),
                                               ie.get("line"), ie.get("snippet")]))
    for _, row in sorted(rows_to_sort, key=lambda x: x[0]):
        body(ws, r, row)
        r += 1
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:H{max(r-1,1)}"

    # ---------------- Anti-Pattern Findings ----------------
    ws = wb.create_sheet("Anti-Pattern Findings")
    hdr(ws, 1, ["Service", "Repo", "Pattern", "Description", "Severity Hint", "File", "Line", "Snippet"],
        [16, 20, 20, 34, 12, 30, 8, 34])
    r = 2
    for repo_entry in registry:
        if not repo_entry.get("app_present"):
            continue
        findings = load_json(os.path.join(args.scan_output, repo_entry["app_repo"], "findings.json"))
        if not findings:
            continue
        svc = service_name(repo_entry["acronym"])
        for ap in findings.get("anti_patterns", []):
            body(ws, r, [svc, repo_entry["app_repo"], ap.get("pattern_id"), ap.get("description"),
                         ap.get("severity_hint"), ap.get("file"), ap.get("line"), ap.get("snippet")])
            r += 1
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:H{max(r-1,1)}"

    # ---------------- Profile Mismatch Register ----------------
    ws = wb.create_sheet("Profile Mismatch Register")
    hdr(ws, 1, ["Service", "Repo", "Config Key", "PERF Value", "PROD Value", "Classification", "Note"],
        [16, 20, 32, 22, 22, 14, 34])
    r = 2
    for repo_entry in registry:
        if not repo_entry.get("app_present"):
            continue
        diff = load_json(os.path.join(args.scan_output, repo_entry["app_repo"], "profile-diff.json"))
        if not diff:
            continue
        svc = service_name(repo_entry["acronym"])
        for d in diff.get("diffs", []):
            body(ws, r, [svc, repo_entry["app_repo"], d.get("key"),
                         evtxt(d.get("perf_value")), evtxt(d.get("prod_value")),
                         d.get("classification"), d.get("note")])
            r += 1
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:G{max(r-1,1)}"

    # ---------------- Infra Component Register ----------------
    ws = wb.create_sheet("Infra Component Register")
    hdr(ws, 1, ["Service", "Repo", "IaC Tool", "Component Category", "Resource Type",
                "Resource ID/Name", "Key Attributes (evidence)", "File"],
        [16, 20, 12, 20, 22, 22, 44, 30])
    r = 2
    for repo_entry in registry:
        if not repo_entry.get("infra_present"):
            continue
        infra = load_json(os.path.join(args.scan_output, repo_entry["infra_repo"], "infra_findings.json"))
        if not infra:
            continue
        svc = service_name(repo_entry["acronym"])
        for res in infra.get("resources", []):
            attr_txt = "; ".join(f"{k}={evtxt(v)}" for k, v in res.get("attributes", {}).items())
            body(ws, r, [svc, repo_entry["infra_repo"], infra.get("iac_tool"),
                         res.get("component_category"), res.get("resource_type"),
                         res.get("resource_id_or_name"), attr_txt, res.get("file")])
            r += 1
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:H{max(r-1,1)}"

    wb.save(args.out)
    print(f"Wrote {args.out}")
    print("Open this workbook once in Excel (or run it through recalc.py if LibreOffice is "
          "available) to refresh the live formulas in the original tabs (Likelihood Tier, "
          "Risk Rating, Wave Plan counts, Coverage Matrix, Priority Model distributions) before "
          "final handoff to the chaos team.")

if __name__ == "__main__":
    main()
```

### 7.1 Running it

```bash
python3 consolidate.py \
  --workbook Chaos_Engineering_Scenario_Catalog_v2.xlsx \
  --scan-output ./scan-output \
  --out Chaos_Engineering_Scenario_Catalog_v3_scanned.xlsx
```

Safe to re-run any time — it deletes and rebuilds only the scan-derived sheets, never touching
README, Service Inventory, Scenario Catalog, Priority Model, Wave Plan, Coverage Matrix, Open
Items, or the two instruction tabs.

### 7.2 CSV fallback (if `openpyxl` truly cannot be installed on the VDI)

Change the script's final section to write each sheet's rows to
`scan-output/consolidated/<sheet-name>.csv` via the standard library `csv` module instead of
`openpyxl`, and hand those CSVs back alongside `scan-output/` for merging outside the VDI. Every
extraction phase before this one is unaffected either way — only the final assembly step changes.
## 8. Phase 6 — Standards Seed

Once `consolidate.py` has run, synthesize a `standards.md` from what it produced. This is the
artifact that stops this from being a one-time exercise — every future service should be able to
start from these rules instead of triggering another full scan-and-discover cycle.

**Prompt to run this phase (main context, not a subagent — this benefits from seeing everything
at once):**

```
ultrathink: Read Resilience Truth Table, Timeout Chain, Kafka Consumer Audit, Idempotency
Suspicion Ranking, Anti-Pattern Findings, and Profile Mismatch Register from
Chaos_Engineering_Scenario_Catalog_v3_scanned.xlsx in full.

Write standards.md: one rule per recurring gap you observed across multiple services (not
per-service advice — patterns, not individual findings). Each rule needs:
  - A name and one-line statement (e.g., "Every Feign/REST client must declare both
    failure-rate AND slow-call-rate breaker thresholds").
  - The evidence basis: how many services/repos exhibited the gap this rule closes (cite the
    sheet and rows, not a vague "many services").
  - A minimal code/config example of compliance.
  - Which scan category (App or Infra Scan Instructions tab) would catch a future violation.

Do NOT include a rule with only one supporting instance unless its severity is high enough to
warrant a standard regardless of frequency (e.g., a single found instance of an unbounded async
executor is still worth a standard). State the instance count honestly either way.

Also produce standards-enforcement/ as a set of rule SKETCHES only (not wired into any CI
pipeline) — for each standard, a short ArchUnit test skeleton (Java) or Semgrep rule skeleton
(YAML) that could enforce it, clearly marked DRAFT — NOT YET INTEGRATED. Enrique decides whether
and how these get wired into CI; this phase only drafts them.
```

---

## 9. Phase 7 — Journey Graph Ingestion (extensibility layer)

This phase is independent of Phases 0–6 and can run whenever journey graph files exist,
regardless of how many repos have been scanned so far. It exists because journeys and services
will keep being added — this phase must never assume a fixed count of either.

### 9.1 What a journey file is

A journey file is any JSON document, anywhere under `--root`, that has top-level `journey`,
`nodes`, and `edges` keys. **Discovery is content-based, not path- or filename-based** —
deliberately, so it survives your repo layout evolving, journeys living in different folders or
repos over time, and naming conventions drifting. Do not hardcode a folder path or a filename
pattern (e.g. `*-COMPLETE.json`) as the discovery mechanism; sniff file content instead.

Each journey file carries, per edge, a `grade` (`green` | `yellow` | `red` | `pending-sme-input`)
already computed from `caller_grade`/`callee_grade`. **This runbook consumes that grade — it
does not attempt to recompute it.** The exact rule that produces `grade` from the two sides is
whatever your existing journey-mapping process uses; document it once (see 9.4) but do not
duplicate or second-guess it here.

### 9.2 Join key: canonical service id, not repo folder name

Journey nodes reference services by an id like `"app-a"` — the same
string each service's own app-scan `findings.json` should declare at its top level as
`"service"`. **Join on that field.** Do not join on the repo-registry acronym
(`app-a`) as the primary key — it's a folder-naming convenience, not the identity the journey
graph actually uses. Fall back to a weak acronym-substring match only when no canonical id is
found, and flag every such fallback explicitly (`WEAK_ACRONYM_FALLBACK:<acronym>`) — never treat
it as equivalent to a real match.

If a service's `findings.json` doesn't yet declare a `"service"` field (true for the schema
originally specified in Phase 2 of this runbook — see 9.3), add it: it should be the exact
string other services' journey files use to reference this one.

### 9.3 Schema reconciliation note

The `findings.json` shape specified in Section 4.2 of this runbook was a reasonable starting
design, but if you already have a working scanner producing cards shaped like the
`app-a.json` example (top-level `service`, `scanned_from`, `resiliency`,
`circuit_breaker`, `destinations`, `outbound`/`inbound` with `resolved`/`resolve: narrative`
pairs), **that real schema is canonical — adapt Section 4.2's subagent prompt and output schema
to match it** rather than maintaining two incompatible shapes. The accuracy-contract principles
(every fact has evidence, `resolve: narrative` instead of a guessed consumer, no invented
defaults) carry over unchanged; only the field names and nesting should follow whatever your
working scanner already produces.

### 9.4 Grading rubric documentation (recommended, not blocking)

Because Phase 7 consumes `grade` rather than recomputing it, documenting the rubric isn't a
hard dependency for this phase to run. It's still worth doing once, so the rubric is explicit
and consistent as more journeys get graded by different people over time. Run this in your
Claude Code session against wherever the grading logic actually lives:

```
Find the code or logic that computes the `grade` field on journey edges from
`caller_grade`/`callee_grade`. Document it as an explicit decision table: every combination of
(caller known/unknown, caller grade, callee known/unknown, callee grade) -> resulting grade.
Cite the specific rule, not an example. Save it as grading-rubric.md next to the journey files.
```

### 9.5 Open vocabulary for `via` (interaction family)

`via` values seen so far: `http`, `kafka`, `event`, `ui-flow`, and their `rollback-*`
counterparts. This vocabulary is **not closed** — batch-triggered journeys (S3 object-drop,
EventBridge schedule) haven't been documented yet but are anticipated. `journey_ingest.py`
below maps every `via` value through a table; anything not in the table is surfaced as
`UNMAPPED:<value>` rather than silently miscategorized or dropped. Extend the table as new
`via` values appear — do not treat an unmapped value as an error to fix by guessing a mapping.

### 9.6 Priority scoring

Each edge's chaos-relevant priority is `criticality_weight × data_class_weight × grade_weight`
(same multiplicative pattern as the existing catalog's Likelihood × Severity). Defaults —
recalibrate against real incident history over time, same caveat as everywhere else in this
runbook:

| Criticality | Weight | | Data class | Weight | | Grade | Weight |
|---|---|---|---|---|---|---|---|
| P0 | 4 | | money-movement | 3 | | red | 3 |
| P1 | 3 | | state-mutating | 2 | | yellow / pending-sme-input | 2 |
| P2 | 2 | | read-only | 1 | | green | 1 |
| P3 | 1 | | | | | | |

**Grade determines the action, not just the score** — these are different jobs:
- **`red`**: this is usually already a confirmed defect, not a hypothesis. File it. A chaos
  scenario here is a *regression guard* for after the fix, not a discovery experiment.
- **`yellow` / `pending-sme-input`**: the real action is scanning or SME assessment to resolve
  the unknown — not scheduling a chaos experiment against an edge you don't understand yet.
- **`green`**: a candidate for periodic confirmatory chaos testing (trust, but re-verify) at a
  cadence proportional to criticality — this is where scheduled chaos experiments belong.

### 9.7 Script

```python
#!/usr/bin/env python3
"""
journey_ingest.py — discover and ingest user-journey graph files, join them against
per-service scan output, and compute a chaos-engineering priority score per edge.

Discovery is CONTENT-based, not path- or filename-based: any *.json file anywhere
under --root that has top-level "journey", "nodes", and "edges" keys is treated as
a journey file. This is deliberately future-proof — new journeys can live anywhere,
be named anything, and still get picked up without touching this script.

Usage:
    python3 journey_ingest.py --root <root> --scan-output ./scan-output \
                               --out scan-output/journey-registry.json
"""
import argparse, json, os, glob


def is_journey_file(obj):
    return isinstance(obj, dict) and "journey" in obj and "edges" in obj and "nodes" in obj


def load_json_safe(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


CRITICALITY_WEIGHT = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
DATA_CLASS_WEIGHT = {"money-movement": 3, "state-mutating": 2, "read-only": 1}
GRADE_WEIGHT = {"red": 3, "yellow": 2, "pending-sme-input": 2, "green": 1}

VIA_TO_FAMILY = {
    "http": "sync-api",
    "kafka": "messaging-async",
    "event": "unclear-internal-handoff",
    "ui-flow": "user-interaction",
    "rollback-http": "sync-api-compensation",
    "rollback-kafka": "messaging-async-compensation",
    "rollback-event": "unclear-internal-handoff-compensation",
    "s3-event": "batch-file-triggered",
    "eventbridge-schedule": "batch-scheduled",
}


def via_family(via):
    return VIA_TO_FAMILY.get(via, f"UNMAPPED:{via}")


def action_for_grade(grade):
    if grade == "red":
        return "CONFIRMED_ISSUE — file as a defect; add a regression-guard chaos scenario"
    if grade in ("pending-sme-input", "yellow"):
        return "NEEDS_ASSESSMENT — prioritize scanning/SME review before scheduling a chaos scenario"
    if grade == "green":
        return "CONFIRMATORY — periodic chaos re-validation at a cadence set by criticality"
    return "UNKNOWN_GRADE_VALUE — review grading rubric"


def priority_score(criticality, data_class, grade):
    cw = CRITICALITY_WEIGHT.get(criticality)
    dw = DATA_CLASS_WEIGHT.get(data_class)
    gw = GRADE_WEIGHT.get(grade)
    missing = [name for name, v in [("criticality", cw), ("data_class", dw), ("grade", gw)] if v is None]
    if missing:
        return None, missing
    return cw * dw * gw, []


def find_journey_files(root):
    found = []
    for path in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        obj = load_json_safe(path)
        if is_journey_file(obj):
            found.append((path, obj))
    return found


def load_service_registry(scan_output_dir):
    registry = load_json_safe(os.path.join(scan_output_dir, "repo-registry.json")) or []
    service_index = {}
    acronym_fallback = {}
    for entry in registry:
        if not entry.get("app_present"):
            continue
        findings_path = os.path.join(scan_output_dir, entry["app_repo"], "findings.json")
        findings = load_json_safe(findings_path)
        canonical_id = findings.get("service") if findings else None
        if canonical_id:
            service_index[canonical_id] = {"repo_entry": entry, "findings": findings}
        acronym_fallback[entry["acronym"]] = entry
    return service_index, acronym_fallback


def resolve_node_service(node_id, service_index, acronym_fallback):
    if node_id in service_index:
        return service_index[node_id], "canonical_id_match"
    for acr, entry in acronym_fallback.items():
        if acr and acr in node_id.replace("-", ""):
            return {"repo_entry": entry, "findings": None}, f"WEAK_ACRONYM_FALLBACK:{acr}"
    return None, "NO_MATCH"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--scan-output", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    journey_files = find_journey_files(args.root)
    service_index, acronym_fallback = load_service_registry(args.scan_output)

    journeys_out = []
    ranked_edges = []
    coverage_gaps = []
    unmapped_vias = set()

    for path, jf in journey_files:
        journey_id = jf.get("journey")
        criticality = jf.get("criticality")
        top_profile = jf.get("profile", {})

        declared_scanned = set(jf.get("scanned_services", []))
        node_scanned = {n["id"] for n in jf.get("nodes", []) if n.get("scanned")}
        if declared_scanned != node_scanned:
            coverage_gaps.append({
                "journey": journey_id, "type": "SCANNED_LIST_MISMATCH",
                "declared_scanned_services": sorted(declared_scanned),
                "nodes_marked_scanned": sorted(node_scanned),
            })

        for node in jf.get("nodes", []):
            resolved, match_type = resolve_node_service(node["id"], service_index, acronym_fallback)
            if resolved is None:
                coverage_gaps.append({
                    "journey": journey_id, "type": "NODE_NOT_IN_ANY_APP_SCAN",
                    "node_id": node["id"], "node_scanned_flag": node.get("scanned"),
                })
            elif match_type != "canonical_id_match":
                coverage_gaps.append({
                    "journey": journey_id, "type": "WEAK_MATCH_ONLY",
                    "node_id": node["id"], "match_type": match_type,
                })

        for edge in jf.get("edges", []):
            via = edge.get("via")
            family = via_family(via)
            if family.startswith("UNMAPPED:"):
                unmapped_vias.add(via)

            grade = edge.get("grade")
            data_class = edge.get("data_class", top_profile.get("data_class"))
            edge_criticality = edge.get("criticality", criticality)

            score, missing = priority_score(edge_criticality, data_class, grade)

            ranked_edges.append({
                "journey": journey_id, "step": edge.get("step"),
                "from": edge.get("from"), "to": edge.get("to"),
                "via": via, "interaction_family": family,
                "grade": grade, "criticality": edge_criticality,
                "data_class": data_class,
                "volume": edge.get("volume", top_profile.get("volume")),
                "priority_score": score, "score_missing_inputs": missing,
                "action": action_for_grade(grade) if grade else "UNKNOWN_GRADE_VALUE — grade field absent",
                "intent": edge.get("intent"),
            })

        journeys_out.append({
            "journey": journey_id, "file": path, "criticality": criticality,
            "profile": top_profile, "node_count": len(jf.get("nodes", [])),
            "edge_count": len(jf.get("edges", [])),
            "unresolved_count": len(jf.get("unresolved", [])),
        })

    ranked_edges.sort(key=lambda r: (r["priority_score"] is None, -(r["priority_score"] or 0)))

    out = {
        "journeys": journeys_out,
        "edges_ranked_by_priority": ranked_edges,
        "coverage_gaps": coverage_gaps,
        "unmapped_via_values": sorted(unmapped_vias),
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Discovered {len(journey_files)} journey file(s), {len(ranked_edges)} edges scored.")
    print(f"Coverage gaps: {len(coverage_gaps)}. Unmapped via values: {sorted(unmapped_vias)}")


if __name__ == "__main__":
    main()
```

### 9.8 New scenario family surfaced by journey ingestion: sagas / compensation paths

Any journey with `rollback-*` edges (a compensating-transaction / saga pattern) needs a scenario
family this runbook's original 90-scenario catalog didn't cover: what happens when a
compensating step itself fails mid-rollback. Add a `CE-SAGA-*` family:

- **CE-SAGA-01 — compensation step failure**: fail one `rollback-*` edge's target service
  during an active rollback. Hypothesis: the saga either retries the compensation to completion
  or surfaces a clear, actionable stuck-state alert — it never silently leaves orphaned state
  across the other already-compensated or not-yet-compensated services.
- **CE-SAGA-02 — partial rollback visibility**: after a forced partial-rollback failure, confirm
  an operator can see exactly which of the N compensating steps completed and which didn't,
  from monitoring alone, without reading code.

Run `journey_ingest.py`'s output through this check: any journey with one or more `via` values
starting with `rollback-` gets at least these two scenarios instantiated using that journey's
actual compensation chain.

### 9.9 Merging into the workbook — via the package generator

The production path for turning journey + scan-card JSONs into deliverables is
`chaos_package_generator.py` (shipped alongside this spec). It is a template-expansion
engine: a library of scenario templates keyed by target type (node-infra, node-app,
edge-downstream, edge-upstream, saga-chain, governance-flag) expands against the graph,
instantiating each template with the target's REAL evidence values (coded timeouts, retry
counts, fallback names, replica counts, idempotency windows). Templates fire only when
their evidence precondition holds — no evidence, no scenario; the item lands in Coverage
Gaps instead. Red-graded edges emit CONFIRMED_DEFECT regression-guard rows rather than
discovery hypotheses; pending-sme-input edges are routed to gaps, not guessed at.

```bash
python3 chaos_package_generator.py \
  --root <dir(s) containing journey JSONs — content-sniffed, any name/location> \
  --scan-cards <dir(s) containing per-service scan cards> \
  --infra-md <dir(s) containing <app>-infra-components-by-env.md files> \
  --components components.json \
  --out chaos_package.xlsx \
  --html chaos_dashboard.html
```

Infra declaration precedence per service: `<app>-infra-components-by-env.md` (evidence-grade,
per-environment, SHA-pinned — preferred) > `components.json` (hand-declared fallback) >
nothing (loud NO_COMPONENT_DECLARATION coverage gap; no scenarios invented). The infra-md
parser also extracts real values (idle timeouts, autoscaling, AZ counts, DNS record type)
into scenario text, emits ENV_DELTA_PERF_VALIDITY gaps for PROD-vs-PERF behavior differences
(e.g. caching disabled in PERF), and raises CONFLICTING_EVIDENCE gaps when two sources
disagree (e.g. journey replica_count vs deploy-repo autoscaling floor) instead of silently
picking one. `--html` additionally writes a single-file, dependency-free interactive
dashboard (risk spectrum strip, expandable evidence, filters, gaps tab) for review and
leadership walkthroughs; the xlsx remains the chaos team's writable working copy.

Output package: Executive Summary (focus areas ranked by summed priority, with reasons),
Scenario Catalog (Layer / Target / Scenario / Hypothesis / evidence-cited Why / Tooling /
Journeys / computed L×S / tier / rating / wave / status), Assertion Matrix (one injection,
one journey-specific pass condition per journey it touches), Coverage Gaps. Deduplication
is by (template, target): a service appearing in many journeys gets ONE scenario row whose
Journeys column accumulates, plus one assertion row per journey.

Extending the system = adding templates to `generate()` in the script, not editing
spreadsheets. New evidence fields in future scan cards / journey schemas become new
template preconditions. Scoring weights (criticality/data-class bumps) live in one place
at the top of the script — recalibrate there.

---

## 10. Orchestrator master prompt

Paste this into Claude Code to kick off the whole run. It instructs Claude Code to treat the
rest of this file as its working instructions.

```
Read spec.md in this directory in full before doing anything else.

Execute it phase by phase, in order: Section 1 (preflight + repo discovery), Section 2
(Phase 0 — sync & provenance), Section 3 (Phase 1 — census), Section 4 (Phase 2 — parallel
app-repo extraction), Section 5 (Phase 3 — parallel infra-repo extraction), Section 6
(Phase 4 — profile diff), Section 7 (Phase 5 — consolidation), Section 8 (Phase 6 — standards),
Section 9 (Phase 7 — journey graph ingestion).

Phase 7 (Section 9) is independent of Phases 0–6 and can run in parallel with them, or any time
afterward, or on a schedule of its own as new journey files appear — it does not need every repo
scanned first. Run `journey_ingest.py` whenever new or updated journey files exist, then fold
its output into the workbook per Section 9.9.

Rules for this run:
- Follow the accuracy contract in Section 0 exactly. When in doubt between writing a plausible
  value and marking something AMBIGUOUS/NOT_CONFIGURED, always choose the latter.
- Checkpoint everything to scan-output/ as you go. Before spawning a subagent for any repo,
  check whether its output file already exists and is valid JSON — if so, skip it and move on.
  This run may be interrupted and resumed.
- Batch parallel subagents (Task tool) in groups of 5–6. Report progress after each batch:
  which repos completed, which hit blocking_issues, which are still pending.
- Use ultrathink at the specific points named in Section 0 and Section 8 — not everywhere.
- Do not run any command that could modify real infrastructure or push any commit — this is a
  read-only evidence-gathering exercise. git pull is the only write-adjacent operation, and only
  onto local branches per the dirty-worktree rule in Section 2.
- After Phase 5 (consolidation) completes, stop and report a summary before proceeding to
  Phase 6: total repos scanned, total blocking_issues across all repos, total findings per
  category, and any repo that couldn't be scanned at all. Wait for confirmation before
  synthesizing standards.md, since that step should reflect a run Enrique has sanity-checked.

Begin with Section 1.
```

---

## 11. Directory / file layout summary

```
<root>/
  spec.md
  consolidate.py
  chaos_package_generator.py                       (Phase 7 package generator: xlsx + html)
  journey_ingest.py                                (lightweight journey ranking utility)
  components.json                                  (per-service infra fallback declaration)
  <app>-infra-components-by-env.md                 (per-service infra declarations, one per app)
  chaos_package.xlsx / chaos_dashboard.html        (outputs of the package generator)
  Chaos_Engineering_Scenario_Catalog_v2.xlsx      (input, never modified)
  Chaos_Engineering_Scenario_Catalog_v3_scanned.xlsx  (output of Phase 5)
  standards.md                                     (output of Phase 6)
  standards-enforcement/
    <rule-name>.archunit.sketch.java
    <rule-name>.semgrep.sketch.yml
  scan-output/
    preflight.json
    repo-registry.json
    provenance.json
    svc-a-app/
      census.json
      findings.json
      profile-diff.json
    svc-a-infra/
      infra_findings.json
    svc-d-app/
      ...
    ... (one folder per repo)
  svc-a-app/          (your existing repos, untouched except git pull)
  svc-a-infra/
  ...
```

---

## 12. Validation & QA checklist (before handing the scanned workbook back)

Run through this before treating `Chaos_Engineering_Scenario_Catalog_v3_scanned.xlsx` as ready
for the chaos team:

- [ ] `scan-output/provenance.json` has an entry for every repo in `repo-registry.json`, and
      every `pull_status` is either `OK` or has a clear reason it isn't (dirty tree, wrong
      branch, failure) — no silent gaps.
- [ ] Every app repo has a `findings.json`; every infra repo has an `infra_findings.json` — or a
      documented reason it doesn't (e.g., no infra repo exists for that service).
- [ ] Spot-check 3–4 `CONFIRMED` entries per category against the actual source file — confirm
      the file:line really says what the sheet claims. This is the single highest-value QA step;
      do not skip it because the volume feels large.
- [ ] Count `AMBIGUOUS` and `NOT_CONFIGURED` entries — if either is unexpectedly low across 17
      services, that's more likely under-reporting than universally clean config. Re-check a
      sample.
- [ ] Every row in Resilience Truth Table / Kafka Consumer Audit with a non-empty "Suggested
      Scenario Targets" column has been reviewed by a human before being treated as a scheduling
      decision — these are heuristic suggestions, not findings.
- [ ] Profile Mismatch Register's `suspicious` rows have been cross-checked against the
      Scenario Catalog's existing `PERF Validity` flags — every Medium/Low flag should now be
      traceable to a specific row here, or flagged as still-unexplained.
- [ ] The original tabs' live formulas (Priority Model distributions, Wave Plan counts, Coverage
      Matrix, Likelihood Tier / Risk Rating columns in Scenario Catalog) show real numbers, not
      blanks — meaning the workbook has been opened in Excel (or recalculated) at least once
      since Phase 5 ran.

---

## 13. Known limitations (mirrors and extends the workbook's Open Items tab)

- **Static analysis sees declared behavior, not runtime reality.** Spring profile activation,
  environment-variable overrides at the ECS task-definition level, and feature-flag-gated code
  paths can all make PROD/PERF runtime behavior diverge from what's checked into the repo. The
  Timeout Chain sheet's cross-reference between app config and infra env-var overrides catches
  some of this; it does not catch everything. Treat every `CONFIRMED` fact as "confirmed in the
  repo," not "confirmed in the running system," unless the evidence explicitly came from a live
  `mvn help:effective-pom` / `terraform plan` / `cdk synth` resolution.
- **Maven property resolution without `mvn` installed is best-effort.** Any version or config
  value that depends on an external parent BOM not present in these repos will be `AMBIGUOUS` —
  expect a non-trivial number of these on the first run if Maven/JDK isn't available on the VDI.
- **The idempotency and anti-pattern categories are suspicion rankings, not proofs.** Static
  analysis cannot prove a handler is idempotent or that an anti-pattern causes a real incident —
  it ranks where to look first. The chaos experiments remain the actual proof.
- **The "Suggested Scenario Targets" heuristic is intentionally simple** (a handful of
  status-based rules) so its logic is auditable at a glance. Expand it over time as patterns
  emerge, but keep each rule traceable to a specific, named status condition — resist the urge to
  make it a black-box score.
- **This run is read-only against infrastructure.** It does not execute `terraform apply`,
  `cdk deploy`, or equivalent — only read-only resolution commands (`plan`, `synth`, static
  parse). If those tools aren't available or read-only execution isn't permitted in this VDI,
  every infra fact falls back to static IaC-file parsing, which is weaker for anything gated
  behind an unresolved variable.
