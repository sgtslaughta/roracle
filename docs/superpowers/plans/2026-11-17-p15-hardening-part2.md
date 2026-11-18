# P15 Security Hardening + Bug Fixes Implementation Plan — Part 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Continues [part 1](2026-11-17-p15-hardening.md). The header, Spec items, Decisions, Global Constraints and Review Focus there apply to every task here.

---

### Task 7: Deleted documents always leave Elasticsearch

**Files:**
- Modify: `backend/src/roracle/acl_sync.py` (`handle_acl_sync`; new `is_deleted`)
- Modify: `backend/src/roracle/search_index_acl.py` (new `delete_doc_chunks`; export via `search_index` like `sync_acl`)
- Modify: `backend/src/roracle/api/routes/documents_manage.py` (`delete_document` uses the helper)
- Test: `backend/tests/test_acl_sync_deleted.py`
- Docs: `docs/enterprise/security/data-retention.md` (deletion cascade: chunks removed now or by the retried `acl_sync` job)

**Interfaces:**
- Produces: `delete_doc_chunks(es: Elasticsearch, doc_id) -> None` (`delete_by_query` on `MUTATE_ALIAS`, `{"term": {"doc_id": str(doc_id)}}`, `refresh=True`, `conflicts="proceed"`; raises on failure); `is_deleted(sf: sessionmaker, doc_id: UUID) -> bool` (fresh short session; True only when the row exists with `deleted_at` set).
- `handle_acl_sync`: `if is_deleted(...)`: `delete_doc_chunks(...)` and return; else existing `sync_acl`. An ES error propagates, so the queue retries with backoff (existing job semantics). The delete route already enqueues `acl_sync` in the same transaction, so a failed immediate delete converges.

- [ ] **Step 1: Write the failing tests** (stub ES class recording `delete_by_query` / `update_by_query` calls; optional `fail=True` raises `ConnectionError`)

```python
def test_acl_sync_deletes_chunks_of_deleted_doc(db_session, session_factory, document):
    document.deleted_at = datetime.now(UTC); db_session.commit()
    es = StubES()
    handle_acl_sync(db_session, {"doc_id": str(document.id)}, es=es, session_factory=session_factory)
    assert es.deleted == [{"term": {"doc_id": str(document.id)}}]

def test_acl_sync_delete_failure_raises_for_retry(...):  # StubES(fail=True) -> pytest.raises(ConnectionError)

def test_live_doc_still_syncs_acl(...):                  # no delete_by_query, one update_by_query

def test_purged_doc_is_noop(...):                         # row gone -> no delete, no error
```

- [ ] **Step 2: Run** `uv run pytest tests/test_acl_sync_deleted.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the two helpers and the branch; switch `delete_document` to `delete_doc_chunks` (keep its try/except + log).
- [ ] **Step 4: Run** the new file plus `uv run pytest -q tests/test_acl_sync.py tests/test_document_manage.py` — Expected: PASS.
- [ ] **Step 5: Commit** `fix(search): retry chunk removal for deleted documents`.

---

### Task 8: Backend bug fixes (quota, admin retry, router session)

**Files:**
- Modify: `backend/src/roracle/api/routes/documents.py:146-154` (quota query)
- Modify: `backend/src/roracle/api/routes/admin_ops.py:103` (`_REQUEUE`)
- Modify: `backend/src/roracle/chat/global_answer.py:route_question` (release before the router LLM)
- Test: `backend/tests/test_p15_bugs.py`
- Docs: `docs/eval/2026-11-16-router.md` (M7 precondition met), `docs/enterprise/security/known-limits.md` quota paragraph is removed in Task 13

**Interfaces:**
- Quota: add `Document.deleted_at.is_(None)` to the `used` query.
- `_REQUEUE = {"status": "queued", "attempts": 0, "released": 0, "run_after": func.now(), "last_error": None}` (fencing uses `claim_token`, so resetting both is safe).
- `route_question`: before `query_router.classify`, `if eligible and settings.router_llm and query_router.heuristic(question) is None: release_before_io(session)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_quota_ignores_deleted(client, as_ana, app, settings):
    # override settings upload_quota_bytes = size of one test PDF + 1; upload A (201); DELETE A (204, JWT); upload B -> 201

def test_retry_resets_released(client, admin, db_session):
    j = Job(kind="x", payload={}, status="failed", attempts=3, released=5); db_session.add(j); db_session.commit()
    r = client.post(f"/admin/jobs/{j.id}/retry", headers=admin); assert r.status_code == 200
    db_session.refresh(j); assert (j.attempts, j.released) == (0, 0)
    # same for POST /admin/jobs/retry-failed

def test_router_llm_runs_without_db_transaction(settings):
    s = StubSession(info={RELEASE_BEFORE_IO: True})   # counts commit()
    llm = StubLLM(on_call=lambda: s.commits)          # records commits seen at call time
    cfg = settings.model_copy(update={"router_llm": True})
    route_question(s, None, llm, cfg, principal, LONG_AMBIGUOUS_QUESTION, True)
    assert llm.seen == [1]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_p15_bugs.py -v` — Expected: FAIL (413; released 5; seen [0]).
- [ ] **Step 3: Implement** the three one-line changes.
- [ ] **Step 4: Run** the new file plus `uv run pytest -q tests/test_admin_api.py tests/test_router_api.py tests/test_global_answer.py` — Expected: PASS.
- [ ] **Step 5: Commit** `fix: quota ignores deleted papers, retry resets refunds, router releases db`.

---

### Task 9: Helm monitoring (PodMonitor, PrometheusRule) and digest enforcement

**Files:**
- Create: `deploy/helm/roracle/templates/podmonitor.yaml`, `deploy/helm/roracle/templates/prometheusrule.yaml`
- Modify: `deploy/helm/roracle/templates/_helpers.tpl` (`roracle.workload` ports: add `{name: metrics, containerPort: <metricsPort>}` when `metricsPort` is set and differs from `port`)
- Modify: `deploy/helm/roracle/templates/validate.yaml` (digest guard)
- Modify: `deploy/helm/roracle/values.yaml`, `ci/full-values.yaml`, `ci/assert-render.sh`
- Docs: `docs/enterprise/observability.md`, `docs/enterprise/runbook-health-alerts.md` (one row per alert), regenerate `docs/reference/helm-values.md`

**Interfaces (values, exact):**
```yaml
observability:
  podMonitor: {enabled: false, interval: 30s, labels: {}}      # needs prometheus-operator CRDs
  prometheusRule: {enabled: false, labels: {}}
global:
  requireDigests: false   # true: render fails unless every image.digest (backend, web, postgres, elasticsearch, parsers, snapshot curl) is set
```
- PodMonitor: one object `<fullname>`, selector `app.kubernetes.io/instance` + `matchExpressions: component In [api, worker]`, `podMetricsEndpoints: [{port: http, path: /metrics}, {port: metrics, path: /metrics}]`, `interval` from values; rendered only when `observability.podMonitor.enabled` and `metrics.enabled`.
- PrometheusRule group `roracle` (alert names and expressions exact; `severity` label `warning` unless noted):
  - `RoracleApi5xxHigh`: `sum(rate(roracle_http_requests_total{status=~"5.."}[5m])) / clamp_min(sum(rate(roracle_http_requests_total[5m])), 1e-9) > 0.05` for 10m
  - `RoracleAuthFailuresBurst`: `sum(rate(roracle_http_requests_total{status="401"}[5m])) * 60 > 30` for 5m
  - `RoracleRateLimitedBurst`: `sum(rate(roracle_http_requests_total{status="429"}[5m])) * 60 > 60` for 5m
  - `RoracleForbiddenBurst`: `sum(rate(roracle_http_requests_total{status="403"}[5m])) * 60 > 30` for 10m
  - `RoracleJobsFailing`: `sum(increase(roracle_job_runs_total{outcome="failed"}[15m])) > 5` for 0m
  - `RoracleQueueBacklog`: `max(roracle_jobs_oldest_queued_seconds) > 1800` for 10m
  - `RoracleAuditWriteFailures`: `sum(increase(roracle_audit_write_failures_total[10m])) > 0` for 0m, severity `critical`
- Validate message: `global.requireDigests: <path> has no digest`.

- [ ] **Step 1: Write the failing assertions** in `ci/assert-render.sh` (full-values sets both monitors enabled): PodMonitor exists with the two port names; worker container has a port named `metrics` = `worker.healthPort`; PrometheusRule has exactly the seven alert names above.
- [ ] **Step 2: Run** `cd deploy/helm/roracle && sh ci/assert-render.sh` — Expected: FAIL "PodMonitor missing".
- [ ] **Step 3: Implement** templates, port, values, validate guard.
- [ ] **Step 4: Run** `helm lint --strict . -f ci/min-values.yaml && helm lint --strict . -f ci/full-values.yaml && sh ci/assert-render.sh`, then `helm template r . -f ci/min-values.yaml --set global.requireDigests=true` — Expected: lint PASS, assert PASS, last command FAILS with `global.requireDigests: image.backend has no digest`. If `promtool` is available: `helm template ... | yq 'select(.kind=="PrometheusRule").spec' > /tmp/r.yaml && promtool check rules /tmp/r.yaml`.
- [ ] **Step 5: Regenerate references, docs, commit** `feat(helm): podmonitor, alert rules and digest enforcement`.

---

### Task 10: Supply chain in CI (SBOM, optional signing) and Pod Security labels

**Files:**
- Modify: `.gitlab-ci.yml` (`build`, `build-web`: kaniko `--digest-file`; new jobs `sbom`, `sign`)
- Modify: `deploy/terraform/modules/platform/main.tf` (namespace labels), `deploy/terraform/modules/platform/variables.tf`, root `deploy/terraform/variables.tf` + pass-through in `main.tf`
- Docs: `docs/enterprise/security/network.md` (section 7: what CI now does), `docs/enterprise/airgap-mirror-manifest.md` (syft, cosign images), `docs/enterprise/deployment-terraform.md` (`pod_security`)

**Interfaces:**
- CI variables (documented, placeholders only): `SYFT_IMAGE` (mirrored `anchore/syft`, pinned tag), `COSIGN_IMAGE` (mirrored `sigstore/cosign`, pinned), `COSIGN_KEY` (file variable), `COSIGN_PASSWORD` (masked).
- `sbom` job: stage `build`, `rules: - if: $SYFT_IMAGE`, image `$SYFT_IMAGE` (entrypoint `[""]`), script `syft scan dir:backend -o cyclonedx-json=sbom-backend.cdx.json` and `syft scan dir:frontend -o cyclonedx-json=sbom-frontend.cdx.json`, artifacts both files, `expire_in: 1 year`.
- `build`/`build-web`: add `--digest-file "$CI_PROJECT_DIR/backend.digest"` / `web.digest`; artifacts those files.
- `sign` job: stage `build`, `needs: [build, build-web]`, `rules: - if: $COSIGN_IMAGE && $COSIGN_KEY && $CI_COMMIT_TAG`, script `cosign sign --yes --key "$COSIGN_KEY" --tlog-upload=false "$CI_REGISTRY_IMAGE/backend@$(cat backend.digest)"` and the same for web (registry login via `cosign login`).
- Terraform: `variable "pod_security" { type = object({ enforce = optional(string, ""), warn = optional(string, "restricted"), audit = optional(string, "restricted") }) default = {} }` with validation each ∈ `["", "privileged", "baseline", "restricted"]`; app namespace labels merge `pod-security.kubernetes.io/<mode> = <level>` for non-empty levels. Default enforces nothing (no workload can break); `warn`/`audit` surface violations.

- [ ] **Step 1: Write the failing check**: `python3 -c 'import yaml,sys; d=yaml.safe_load(open(".gitlab-ci.yml")); assert {"sbom","sign"} <= d.keys(), "jobs missing"; assert "--digest-file" in str(d["build"]["script"])'`.
- [ ] **Step 2: Run** it — Expected: FAIL "jobs missing".
- [ ] **Step 3: Implement** CI jobs and terraform variable.
- [ ] **Step 4: Run** the check again, `cd deploy/terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate` — Expected: PASS. If a GitLab instance is reachable, also `glab ci lint`.
- [ ] **Step 5: Docs + commit** `ci: cyclonedx sboms, optional cosign signing, pod security labels`.

---

### Task 11: ECK 3.3.2 and Elasticsearch 9.5.6 pins

**Files:**
- Modify: `deploy/terraform/environments/{example,prod.example,kind,kind-ceph}.tfvars` (`charts.eck.version`, `operator_images.eck.tag` → `"3.3.2"`)
- Modify: `deploy/terraform/test/kind-e2e.sh:54,66,68` (ECK chart/image 3.3.2; ES image `9.5.6`)
- Docs: remove the ECK/8.15.3 warnings in `docs/enterprise/{deployment-terraform,upgrades,deployment-kind-rehearsal,airgap-mirror-manifest,deployment-guide}.md`; add to `upgrades.md`: "ECK refuses a 9.x version on a cluster below 8.18.0; upgrade the cluster to ≥ 8.18 first or create a new one."

- [ ] **Step 1: Failing check**: `grep -rnE '2\.16\.0|8\.15\.3' deploy docs/enterprise` — Expected: matches (the failing state).
- [ ] **Step 2: Edit** the pins and docs.
- [ ] **Step 3: Verify**: the grep returns nothing; `bash -n deploy/terraform/test/kind-e2e.sh`; `cd deploy/terraform && terraform fmt -check -recursive`; `scripts/docs/check.sh` — Expected: PASS. Before committing, confirm 3.3.2 (or a newer 3.x) is in the site mirror; if not, record a `Ruling:` with the version chosen.
- [ ] **Step 4: Commit** `fix(deploy): pin ECK 3.3.2 and mirror Elasticsearch 9.5.6`.

---

### Task 12: Frontend bug fixes

**Files:**
- Modify: `frontend/src/features/reader/ManageMenu.tsx:23` (toast)
- Modify: `frontend/src/features/reader/ReaderPane.tsx:77`, `frontend/src/features/library/SearchPage.tsx:38,76` (handoff copy; heading)
- Create: `frontend/src/features/library/heading.ts` (`resultsHeading`)
- Modify: `frontend/src/features/admin/ConnectorForm.tsx:17` and the stylesheet that defines `.foot` (find with `grep -rn "\.foot" frontend/src --include=*.css`)
- Create: `frontend/src/test/scheduler.ts`; Modify: `frontend/src/test/setup.ts`
- Tests: `frontend/src/features/library/heading.test.ts`, `frontend/src/test/scheduler.test.ts`, extend `features/agents/handoffs.test.tsx`, `features/admin/admin.test.tsx`

**Interfaces / exact copy:**
- Toast: `"Document deleted."`
- Document handoff summary: `"Hands this document to an agent. The agent reads it, and any notes on it, with its own access."`
- Collection handoff summary: `"Hands this collection to an agent: its documents as of now."`
- `resultsHeading(q: string): string` — `t = q.trim()`; if `t` matches `/^"[^"]*"$/` use the inside; if the result still contains `"` return `Results for ${t}`; else `Results for “${t}”`. Empty → `"Library"`.
- `guardedScheduler(cb: () => void): void` — `setTimeout(() => { if (typeof window !== "undefined") cb(); }, 0)`; `setup.ts` calls `notifyManager.setScheduler(guardedScheduler)` (same macrotask timing as react-query's default, so the 16-failure regression from `queueMicrotask` does not recur).
- Connector dialog: `DialogContent className="w-[min(560px,calc(100vw-32px))] max-h-[calc(100dvh-32px)] overflow-y-auto"`; `.agent-form .foot { position: sticky; bottom: 0; background: var(--<existing surface token>); }` so Save/Cancel stay visible.

- [ ] **Step 1: Write the failing tests**

```ts
it.each([["multi-agent", "Results for “multi-agent”"], ['"multi-agent"', "Results for “multi-agent”"],
         ['a "b" c', 'Results for a "b" c'], ["", "Library"]])("heading %s", (q, want) => expect(resultsHeading(q)).toBe(want));

it("skips callbacks after teardown", () => {
  vi.useFakeTimers(); const cb = vi.fn();
  guardedScheduler(cb); vi.stubGlobal("window", undefined); vi.runAllTimers(); vi.unstubAllGlobals();
  expect(cb).not.toHaveBeenCalled();
  guardedScheduler(cb); vi.runAllTimers(); expect(cb).toHaveBeenCalledOnce(); vi.useRealTimers();
});
// admin.test.tsx: open "New connector"; dialog element has classes max-h-[calc(100dvh-32px)] and overflow-y-auto
// handoffs.test.tsx / reader: summary text equals the exact copy above; ManageMenu delete toast is "Document deleted."
```

- [ ] **Step 2: Run** `cd frontend && pnpm exec vitest run src/features/library src/test src/features/admin src/features/agents src/features/reader` — Expected: FAIL.
- [ ] **Step 3: Implement** the copy, helper, scheduler and dialog changes.
- [ ] **Step 4: Run** `pnpm exec tsc --noEmit && pnpm exec vitest run` three times in a row — Expected: all PASS, exit 0 each time. Manually (run skill or `pnpm dev` + browser at 1440×900): New connector dialog shows Save without page scroll.
- [ ] **Step 5: Commit** `fix(web): honest delete and handoff copy, heading quotes, dialog overflow, test teardown flake`.

---

### Task 13: Security docs, known limits, release 0.4.0

**Files:**
- Modify: `docs/enterprise/security/known-limits.md` (rewrite), `docs/enterprise/security.md` (sections 5/6 summary lines), `docs/enterprise/security/threat-model.md` (prompt injection, agents, audit evasion rows), `docs/enterprise/security/access-control.md`, `docs/enterprise/security/audit.md` (final pass for consistency with Tasks 1–6)
- Modify: `VERSION`, `deploy/helm/roracle/Chart.yaml` (`version`, `appVersion`), `backend/pyproject.toml`, `backend/uv.lock` (`cd backend && uv lock`), `frontend/package.json` → `0.4.0`
- Modify: `docs/enterprise/upgrades.md` (0.3 → 0.4 notes: migration 0021 backfill, `manage` scope, `/mcp` OAuth clients now gated as agents, new Helm values off by default)

**Known-limits content rule:** every bullet left is an accepted limit with a one-clause reason, matching the "Accepted, documented" list in part 1 Decisions, plus these residuals: handoff user messages/brief/goal/notes are free text; prompt quoting cannot stop a model from being persuaded by quoted data; 401s are counted and alerted, not audited per row; rate-limit denial rows are throttled to one per principal per minute; audit replay rows store the user id in `detail` (not `user_id`); no vulnerability-scan stage, no STIG baseline, images are not minimal; PSA enforce is opt-in. Every fixed item is removed (not marked "fixed").

- [ ] **Step 1: Failing check**: `grep -nE 'p\.via == "pat"|unquoted|2\.16\.0|not audited at all|PodMonitor' docs/enterprise/security/known-limits.md` — Expected: matches.
- [ ] **Step 2: Rewrite** the docs as above; bump the five version files and run `uv lock`.
- [ ] **Step 3: Verify**: the grep returns nothing; `scripts/check-version.sh` prints `version ok: 0.4.0`; `scripts/docs/check.sh` PASS; full backend CI command and frontend CI command (Global Constraints) PASS; `cd backend && uv run python ../scripts/docs/gen_reference.py --check` PASS.
- [ ] **Step 4: Commit** docs as `docs(security): shrink known limits to accepted items`, then the bump as `chore(release): 0.4.0`.
