# P1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed-in user can upload a file; bytes are stored once in MinIO, the document is recorded in Postgres with owner and visibility, an `ingest` job is queued in the same transaction, and `GET /me/uploads` shows it — all built, linted and tested in GitLab CI.

**Architecture:** One Python package `roracle` (FastAPI API + job worker + Alembic migrations) per ADR-0009. Postgres is the source of truth and the job queue (ADR-0001, ADR-0003). Keycloak JWTs are the only identity (ADR-0004). Sync SQLAlchemy 2 sessions; FastAPI runs sync endpoints in its threadpool.

**Tech Stack:** Python ≥ 3.12, uv, FastAPI, Uvicorn, SQLAlchemy 2 + psycopg 3, Alembic, pydantic-settings, PyJWT[crypto], httpx, boto3, python-multipart; pytest, ruff. Postgres 16, MinIO, Keycloak (dev only).

**Spec:** `docs/spec.md` (§2, §3, §4 steps 1–2, §12) and ADRs 0001, 0003, 0004, 0008, 0009, 0013.

## Global Constraints

- Package name `roracle`; product name in user-facing text `rOracle`.
- Env prefix `ROR_` for all settings.
- Visibility values exactly: `private`, `shared`, `org_unit`, `everyone`; upload default `everyone`.
- Max upload size default `500 * 1024 * 1024` bytes (`ROR_MAX_UPLOAD_BYTES`).
- Blob object key: `blobs/{sha256[:2]}/{sha256}` — never derived from the filename.
- Job statuses exactly: `queued`, `running`, `done`, `failed`; max attempts `5`; retry delay `10 * 2**attempts` seconds.
- Admin role name: `roracle-admin` (Keycloak realm role, `realm_access.roles`).
- JWT: RS256 only; audience `roracle` (`ROR_OIDC_AUDIENCE`); groups from the `groups` claim (Keycloak group paths like `/Mathematics/Spectral Theory`).
- Product version comes from `/VERSION` (initial `0.1.0`); env `ROR_VERSION` overrides it.
- Conventional Commits; work on a feature branch, never `main`. Files under ~500 lines.
- All dependencies resolve from mirrors: `UV_INDEX_URL` for Python, `REGISTRY_MIRROR` prefix for images.

## Review Focus

1. Duplicate bytes of a document the uploader **cannot** see → response identical to a fresh upload (no 409, no hint) — Task 6.
2. Upload larger than the limit → 413, no object written to MinIO, no DB rows — Task 5 and Task 6.
3. Empty (0-byte) file → 400 `empty file` — Task 6.
4. The same bytes uploaded by two users → one blob row with `refcount = 2`, one object — Task 6.
5. Signing-key rotation: token with unknown `kid` → JWKS re-fetched once; still unknown → 401. Expired token and wrong audience → 401 — Task 3.

---

## File structure

```
VERSION                                  0.1.0
.gitlab-ci.yml                           lint / test / build
deploy/dev/compose.yaml                  postgres, minio, keycloak (dev + local tests)
deploy/dev/keycloak/realm-roracle.json   realm "roracle", client "roracle-web", roles, test user
backend/pyproject.toml                   deps, ruff, pytest config
backend/Dockerfile                       one image; CMD selects api | worker | migrate
backend/alembic.ini, backend/migrations/ env.py + versions/0001_foundation.py
backend/src/roracle/__init__.py          __version__
backend/src/roracle/__main__.py          `python -m roracle api|worker|migrate`
backend/src/roracle/config.py            Settings
backend/src/roracle/db.py                engine, session factory, get_session dependency
backend/src/roracle/models.py            ORM tables
backend/src/roracle/auth.py              JWKS cache, Principal, current_principal dependency
backend/src/roracle/access.py            visible_documents(principal) SQL clause
backend/src/roracle/storage.py           BlobStore (S3/MinIO, streaming hash, size limit)
backend/src/roracle/queue.py             enqueue / claim / complete / fail
backend/src/roracle/worker.py            run_once, run_forever
backend/src/roracle/api/app.py           create_app()
backend/src/roracle/api/routes/{health,me,documents}.py
backend/tests/conftest.py                settings, db, app client, token factory, s3 fixtures
backend/tests/test_*.py
```

---

### Task 1: Backend skeleton, version, health endpoint

**Files:**
- Create: `VERSION`, `backend/pyproject.toml`, `backend/src/roracle/__init__.py`, `backend/src/roracle/api/app.py`, `backend/src/roracle/api/routes/health.py`
- Test: `backend/tests/test_health.py`

**Interfaces:**
- Produces: `roracle.__version__: str`; `roracle.api.app.create_app(settings: Settings | None = None) -> FastAPI` (Settings arrives in Task 2; in this task accept `None` only); `GET /healthz`.

- [ ] **Step 1: Write `VERSION` containing `0.1.0` and `backend/pyproject.toml`** with the stack above, `[tool.ruff] line-length = 110`, `[tool.pytest.ini_options] testpaths = ["tests"]`, src layout (`packages = [{ include = "roracle", from = "src" }]` or hatch equivalent).

- [ ] **Step 2: Write the failing test**

```python
def test_healthz_reports_ok_and_repo_version(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": (REPO_ROOT / "VERSION").read_text().strip()}
```
(`client` = `TestClient(create_app())` fixture in `tests/conftest.py`; `REPO_ROOT = Path(__file__).parents[2]`.)

- [ ] **Step 3: Run** `cd backend && uv run pytest tests/test_health.py -v` → FAIL (import error).
- [ ] **Step 4: Implement** `__version__`: `os.environ.get("ROR_VERSION")`, else `VERSION` at repo root (`Path(__file__).resolve().parents[3] / "VERSION"`) if it exists, else `"0.0.0+dev"`. `create_app` includes the health router.
- [ ] **Step 5: Run** the test → PASS; `uv run ruff check . && uv run ruff format --check .` → clean.
- [ ] **Step 6: Commit** `feat(backend): scaffold roracle package with health endpoint`

---

### Task 2: Settings, dev stack, database schema and migrations

**Files:**
- Create: `backend/src/roracle/config.py`, `backend/src/roracle/db.py`, `backend/src/roracle/models.py`, `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions/0001_foundation.py`, `deploy/dev/compose.yaml`, `deploy/dev/keycloak/realm-roracle.json`
- Test: `backend/tests/test_schema.py`; fixtures in `backend/tests/conftest.py`

**Interfaces:**
- Produces:
  - `Settings` (pydantic-settings, prefix `ROR_`): `database_url: str`, `s3_endpoint: str`, `s3_bucket: str = "roracle"`, `s3_access_key: str`, `s3_secret_key: str`, `oidc_issuer: str`, `oidc_audience: str = "roracle"`, `oidc_jwks_url: str`, `max_upload_bytes: int = 524288000`; `get_settings() -> Settings` (lru_cached).
  - `db.make_session_factory(url: str) -> sessionmaker[Session]`; dependency `db.get_session() -> Iterator[Session]` (commit on success, rollback on error).
  - ORM models: `User(id: UUID, sub: str unique, username: str, email: str | None, created_at)`, `Blob(sha256: str(64) PK, size: int, refcount: int = 1, created_at)`, `Document(id: UUID, owner_id → users, title: str, org_unit: str, visibility: str, created_at, deleted_at: datetime | None)`, `DocumentVersion(id: UUID, document_id → documents, version: int, blob_sha256 → blobs, filename: str, content_type: str, size: int, created_at; UNIQUE(document_id, version))`, `DocumentShare(document_id, principal_kind ∈ {'user','group'}, principal: str; PK all three)`, `Job(id: bigint, kind: str, payload: JSONB, status: str, attempts: int = 0, run_after: timestamptz = now(), last_error: str | None, created_at, updated_at; INDEX(status, run_after))`.
  - CHECK constraints: `documents.visibility IN ('private','shared','org_unit','everyone')`, `jobs.status IN ('queued','running','done','failed')`.
- Test fixtures (used by every later task): `session_factory`, `settings` (from env; tests use database `roracle_test`), `db_session` (alembic upgrade head once per session; TRUNCATE all tables between tests), `app` / `client` (`create_app(settings)` with `get_session` overridden to the test session factory).

- [ ] **Step 1: Write `deploy/dev/compose.yaml`**: `postgres` (`${REGISTRY_MIRROR}postgres:16`, port 5432, creates `roracle` and `roracle_test` via init script), `minio` (ports 9000/9001, bucket created by a `minio/mc` one-shot), `keycloak` (port 8080, `--import-realm` from `deploy/dev/keycloak/`). Realm `roracle`: client `roracle-web` (public, PKCE), audience mapper adding `roracle`, groups mapper (full path) to claim `groups`, realm role `roracle-admin`, user `researcher` / `researcher` in group `/Mathematics/Spectral Theory`.
- [ ] **Step 2: Write the failing tests**

```python
def test_migrations_create_all_tables(db_session):
    names = set(inspect(db_session.get_bind()).get_table_names())
    assert {"users", "blobs", "documents", "document_versions", "document_shares", "jobs"} <= names

def test_visibility_is_constrained(db_session, user):
    db_session.add(Document(owner_id=user.id, title="t", org_unit="/", visibility="public"))
    with pytest.raises(IntegrityError):
        db_session.flush()

def test_version_numbers_are_unique_per_document(db_session, document, blob):
    db_session.add_all([DocumentVersion(document_id=document.id, version=1, blob_sha256=blob.sha256,
                                        filename="a", content_type="x", size=1) for _ in range(2)])
    with pytest.raises(IntegrityError):
        db_session.flush()
```
- [ ] **Step 3: Run** `docker compose -f deploy/dev/compose.yaml up -d postgres minio` then `uv run pytest tests/test_schema.py -v` → FAIL.
- [ ] **Step 4: Implement** settings, `db.py`, models, and migration `0001_foundation` (hand-written to match the models; `alembic check` must report no drift).
- [ ] **Step 5: Run** tests → PASS; `uv run alembic downgrade base && uv run alembic upgrade head` → no errors.
- [ ] **Step 6: Commit** `feat(backend): add settings, dev stack and foundation schema`

---

### Task 3: Authentication — JWT validation, principal, user upsert

**Files:**
- Create: `backend/src/roracle/auth.py`, `backend/src/roracle/api/routes/me.py`
- Test: `backend/tests/test_auth.py`; add `make_token` + `jwks` fixtures to `conftest.py`

**Interfaces:**
- Consumes: `Settings`, `get_session`, `User`.
- Produces:
  - `@dataclass(frozen=True) Principal(user_id: UUID, sub: str, username: str, groups: tuple[str, ...], is_admin: bool)`
  - `class JwksCache(url: str, fetch: Callable[[str], dict] = httpx_fetch)` with `key_for(kid: str) -> jwt.PyJWK` (fetch on first use; on unknown `kid` refetch **once**, then raise `KeyError`).
  - Dependency `get_jwks() -> JwksCache` (app-scoped singleton; tests override).
  - Dependency `current_principal(authorization header, session, jwks, settings) -> Principal`: Bearer token, RS256, verifies `exp`, `iss == settings.oidc_issuer`, `aud == settings.oidc_audience`; upserts `User` by `sub` (updates username/email); `is_admin = "roracle-admin" in realm_access.roles`. Any failure → `401 {"detail": "invalid token"}` with `WWW-Authenticate: Bearer`.
  - `GET /me` → `{"id", "username", "groups", "is_admin"}`.
- Test helper: `make_token(sub="u1", username="ana", groups=(), roles=(), exp_in=300, aud="roracle", kid="k1") -> str` signed with a test RSA key whose JWK the `jwks` fixture serves.

- [ ] **Step 1: Write the failing tests**

```python
def test_me_returns_principal_and_creates_user(client, make_token, db_session):
    r = client.get("/me", headers=bearer(make_token(groups=("/Math",), roles=("roracle-admin",))))
    assert r.status_code == 200 and r.json()["groups"] == ["/Math"] and r.json()["is_admin"] is True
    assert db_session.scalar(select(func.count()).select_from(User)) == 1

def test_missing_token_is_401(client):           assert client.get("/me").status_code == 401
def test_expired_token_is_401(client, make_token): assert client.get("/me", headers=bearer(make_token(exp_in=-1))).status_code == 401
def test_wrong_audience_is_401(client, make_token): assert client.get("/me", headers=bearer(make_token(aud="other"))).status_code == 401

def test_unknown_kid_refetches_jwks_once(jwks_with_counter, client, make_token):
    assert client.get("/me", headers=bearer(make_token(kid="rotated"))).status_code == 401
    assert jwks_with_counter.fetches == 2   # initial + one refetch, no more

def test_second_login_updates_username_not_duplicates(client, make_token, db_session): ...
    # same sub, username "ana" then "ana.r" → 1 row, username "ana.r"
```
- [ ] **Step 2: Run** `uv run pytest tests/test_auth.py -v` → FAIL.
- [ ] **Step 3: Implement** `auth.py` and `me.py` per Interfaces (upsert with `INSERT … ON CONFLICT (sub) DO UPDATE`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(auth): validate Keycloak JWTs and upsert users`

---

### Task 4: Job queue and worker

**Files:**
- Create: `backend/src/roracle/queue.py`, `backend/src/roracle/worker.py`, `backend/src/roracle/__main__.py`
- Test: `backend/tests/test_queue.py`

**Interfaces:**
- Consumes: `Job`, session factory.
- Produces:
  - `enqueue(session: Session, kind: str, payload: dict) -> int` — INSERT only; no commit (joins caller's transaction).
  - `claim(session: Session, kinds: Collection[str]) -> Job | None` — claims one due job of the given kinds, sets `status='running'`, `attempts += 1`.
  - `complete(session, job_id: int) -> None`; `fail(session, job: Job, error: str) -> None` — `failed` if `attempts >= 5`, else back to `queued` with `run_after = now() + 10 * 2**attempts s`; stores `last_error` (truncate to 2000 chars).
  - `Handler = Callable[[Session, dict], None]`; `run_once(session_factory, handlers: dict[str, Handler]) -> bool` (True if a job ran; each job in its own transaction; handler exception → `fail`); `run_forever(session_factory, handlers, idle_sleep: float = 1.0)`.
  - `python -m roracle api` (uvicorn `roracle.api.app:create_app --factory`, host `0.0.0.0`, port `8000`), `worker` (`run_forever` with `HANDLERS = {}` for now; P2 registers `ingest`), `migrate` (`alembic upgrade head`).

Claim query (the one non-obvious piece):
```sql
SELECT * FROM jobs
WHERE status = 'queued' AND run_after <= now() AND kind = ANY(:kinds)
ORDER BY run_after, id
FOR UPDATE SKIP LOCKED
LIMIT 1
```

- [ ] **Step 1: Write the failing tests**

```python
def test_enqueue_is_part_of_callers_transaction(session_factory):
    with session_factory() as s:
        enqueue(s, "ingest", {"v": 1}); s.rollback()
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 0

def test_two_workers_never_claim_the_same_job(session_factory):
    with session_factory() as s: enqueue(s, "ingest", {}); s.commit()
    a, b = session_factory(), session_factory()
    assert claim(a, ["ingest"]) is not None and claim(b, ["ingest"]) is None   # a still holds the row lock

def test_claim_ignores_kinds_without_handler(session_factory): ...   # kind "other" stays queued
def test_failure_backs_off_then_fails_after_five_attempts(session_factory): ...
    # after attempt 1: status queued, run_after ≈ now+20s; after attempt 5: status failed, last_error kept
def test_run_once_marks_success_done(session_factory): ...
```
- [ ] **Step 2: Run** `uv run pytest tests/test_queue.py -v` → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(queue): add Postgres job queue and worker loop`

---

### Task 5: Blob store

**Files:**
- Create: `backend/src/roracle/storage.py`
- Test: `backend/tests/test_storage.py` (uses MinIO from the dev stack / CI service; bucket per test run)

**Interfaces:**
- Consumes: `Settings`.
- Produces:
  - `class TooLarge(Exception)`; `class EmptyFile(Exception)`
  - `@dataclass StagedFile(path: Path, sha256: str, size: int)`
  - `stage(stream: BinaryIO, max_bytes: int, chunk: int = 1 << 20) -> StagedFile` — copies to a temp file while hashing; raises `TooLarge` as soon as `size > max_bytes` (deletes temp file); raises `EmptyFile` when size is 0.
  - `class BlobStore(client, bucket: str)`: `key(sha256) -> str` (= `blobs/{sha[:2]}/{sha}`), `put_if_absent(staged: StagedFile, content_type: str) -> bool` (HEAD first; True if uploaded), `open(sha256) -> BinaryIO`.
  - `blob_store_from(settings) -> BlobStore` (boto3 S3 client, path-style addressing).
- Test fixtures added to `conftest.py`: `store` (BlobStore on a per-run test bucket), `s3` (helper with `exists(key: str) -> bool` and `keys() -> list[str]` for that bucket).

- [ ] **Step 1: Write the failing tests**

```python
def test_stage_hashes_and_counts(tmp_path):
    s = stage(io.BytesIO(b"hello"), max_bytes=10)
    assert s.sha256 == hashlib.sha256(b"hello").hexdigest() and s.size == 5

def test_stage_rejects_oversize_without_reading_everything():
    stream = CountingStream(total=50 * 1024 * 1024)          # yields zeros, counts bytes read
    with pytest.raises(TooLarge): stage(stream, max_bytes=1024 * 1024)
    assert stream.read_bytes <= 2 * 1024 * 1024

def test_stage_rejects_empty():
    with pytest.raises(EmptyFile): stage(io.BytesIO(b""), max_bytes=10)

def test_key_is_content_addressed(store):
    assert store.key("ab" + "0" * 62) == "blobs/ab/ab" + "0" * 62

def test_put_if_absent_uploads_once(store, staged):
    assert store.put_if_absent(staged, "application/pdf") is True
    assert store.put_if_absent(staged, "application/pdf") is False
    assert store.open(staged.sha256).read() == staged.path.read_bytes()
```
- [ ] **Step 2: Run** `uv run pytest tests/test_storage.py -v` → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(storage): add content-addressed blob store`

---

### Task 6: Upload endpoint with exact dedup and job enqueue

**Files:**
- Create: `backend/src/roracle/access.py`, `backend/src/roracle/api/routes/documents.py`
- Test: `backend/tests/test_access.py`, `backend/tests/test_upload.py`

**Interfaces:**
- Consumes: `current_principal`, `get_session`, `stage`, `BlobStore`, `enqueue`, models.
- Produces:
  - `visible_documents(p: Principal) -> ColumnElement[bool]` over `Document`: not deleted AND (`visibility='everyone'` OR `owner_id = p.user_id` OR share row for (`user`, `str(p.user_id)`) OR share row for (`group`, any of `p.groups`) OR (`visibility='org_unit'` AND `org_unit` equals or is an ancestor path of one of `p.groups`: `g == org_unit or g.startswith(org_unit.rstrip('/') + '/')`)). Admins see everything. P3 mirrors this rule for Elastic.
  - Dependency `get_blob_store() -> BlobStore` (tests override with the MinIO test bucket).
  - `POST /documents` (multipart: `file`, `title` optional → defaults to filename, `visibility` default `everyone`, `force: bool = false`):
    - `stage(file.file, settings.max_upload_bytes)`; `EmptyFile` → 400 `{"detail": "empty file"}`; `TooLarge` → 413 `{"detail": "file exceeds <N> MB limit"}`.
    - If a non-deleted version with this sha256 belongs to a document matching `visible_documents(p)` and not `force` → 409 `{"detail": {"code": "duplicate", "document_id": "<id>"}}`.
    - Else: `put_if_absent`; upsert blob (`INSERT … ON CONFLICT (sha256) DO UPDATE SET refcount = blobs.refcount + 1`); create `Document` (owner = principal, `org_unit = p.groups[0] if p.groups else "/"`) and `DocumentVersion(version=1)`; `enqueue(session, "ingest", {"version_id": str(version.id)})`; all in one transaction.
    - 201 `{"document_id", "version_id", "sha256"}`.
  - The original filename is stored only in `document_versions.filename`.

- [ ] **Step 1: Write the failing tests** (`test_access.py`: one test per OR-branch of `visible_documents`, plus `org_unit` "/Math" visible to group "/Math/Algebra" but not "/Mathematics"; deleted documents never visible.)

```python
def test_upload_creates_document_version_blob_and_job(client, as_ana, s3):
    r = client.post("/documents", headers=as_ana, files={"file": ("paper.pdf", b"%PDF-1", "application/pdf")})
    assert r.status_code == 201
    body = r.json()
    job = db_session.scalar(select(Job))
    assert job.kind == "ingest" and job.payload == {"version_id": body["version_id"]}
    assert s3.exists(f"blobs/{body['sha256'][:2]}/{body['sha256']}")

def test_duplicate_of_visible_document_is_409_unless_forced(client, as_ana): ...
    # same bytes twice by ana → 409 with document_id of the first; force=true → 201, blob refcount 2

def test_duplicate_of_invisible_document_does_not_leak(client, as_ana, as_bob):
    client.post("/documents", headers=as_ana, data={"visibility": "private"}, files={"file": ("a.pdf", b"secret", "application/pdf")})
    r = client.post("/documents", headers=as_bob, files={"file": ("b.pdf", b"secret", "application/pdf")})
    assert r.status_code == 201 and set(r.json()) == {"document_id", "version_id", "sha256"}

def test_same_bytes_two_users_one_blob_refcount_two(...): ...
def test_empty_file_is_400(...): ...
def test_oversize_is_413_and_writes_nothing(client, as_ana, settings_small_limit, s3, db_session): ...
    # no objects in bucket, no rows in documents/blobs/jobs
def test_traversal_filename_is_only_metadata(...): ...
    # filename "../../etc/passwd" stored verbatim in document_versions.filename; object key is the hash path
def test_invalid_visibility_is_422(...): ...
```
- [ ] **Step 2: Run** `uv run pytest tests/test_access.py tests/test_upload.py -v` → FAIL.
- [ ] **Step 3: Implement** per Interfaces. Order: stage → dedup check → `put_if_absent` → DB writes → commit. Mark the known gap in code: `# ponytail: object written before DB commit can orphan on DB failure; a P2 GC job deletes blob objects without a blobs row`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(documents): upload with content-addressed dedup and ingest job`

---

### Task 7: My uploads

**Files:**
- Modify: `backend/src/roracle/api/routes/me.py`
- Test: `backend/tests/test_my_uploads.py`

**Interfaces:**
- Consumes: models, `current_principal`.
- Produces: `GET /me/uploads?limit=50&offset=0` → `{"items": [{"document_id", "version_id", "title", "filename", "size", "visibility", "status", "error", "uploaded_at"}], "total"}` for documents **owned by** the principal (not deleted), newest first. `status`/`error` come from the latest job whose `payload->>'version_id'` equals the version id (`queued` if none). `limit` ≤ 200.

- [ ] **Step 1: Write the failing tests**

```python
def test_lists_only_my_uploads_newest_first(client, as_ana, as_bob, upload): ...
    # ana uploads a then b, bob uploads c → ana sees [b, a], total 2
def test_status_reflects_latest_job(client, as_ana, upload, db_session): ...
    # job set to failed with last_error "OCR timed out" → status "failed", error "OCR timed out"
def test_limit_is_capped_at_200(client, as_ana): assert client.get("/me/uploads?limit=1000", headers=as_ana).status_code == 422
```
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(me): list my uploads with ingest status`

---

### Task 8: Container image, GitLab CI, developer README

**Files:**
- Create: `backend/Dockerfile`, `.gitlab-ci.yml`, `backend/README.md`
- Modify: `.gitignore` (add `.venv/`, `.pytest_cache/`, `.ruff_cache/`)

**Interfaces:**
- Produces: image `$CI_REGISTRY_IMAGE/backend:<short-sha>` (and `:vX.Y.Z` on tags); `CMD ["python", "-m", "roracle", "api"]`, overridable with `worker` / `migrate`.

- [ ] **Step 1: Write `backend/Dockerfile`**: `ARG REGISTRY_MIRROR`; `FROM ${REGISTRY_MIRROR}python:3.12-slim`; install uv from the mirror (`pip install --index-url $UV_INDEX_URL uv`); `uv sync --frozen --no-dev`; copy `src/`, `migrations/`, `alembic.ini`, `../VERSION`→`/app/VERSION` (build context = repo root, `-f backend/Dockerfile`); set `ROR_VERSION` from `VERSION` at build time; non-root user `10001`; `EXPOSE 8000`.
- [ ] **Step 2: Write `.gitlab-ci.yml`** with stages `lint`, `test`, `build`:
  - `lint`: `uv run ruff check . && uv run ruff format --check .` in `backend/`.
  - `test`: services `${REGISTRY_MIRROR}postgres:16` (alias `postgres`) and `${REGISTRY_MIRROR}minio/minio` (alias `minio`, command `server /data`); variables `ROR_DATABASE_URL`, `ROR_S3_*`, `ROR_OIDC_*` (dummy; tests override JWKS); create `roracle_test` DB; `uv run pytest -q --junitxml=report.xml`; publish JUnit report.
  - `build`: kaniko from `${REGISTRY_MIRROR}`; push on `main` and tags only; merge requests build without push.
  - `workflow:rules`: run for merge requests, `main`, and tags.
- [ ] **Step 3: Verify locally**: `docker build -f backend/Dockerfile .` succeeds; `docker run --rm <img> python -m roracle --help` lists `api worker migrate`; `python -c "import yaml,sys; yaml.safe_load(open('.gitlab-ci.yml'))"` passes; full `uv run pytest` and ruff are green.
- [ ] **Step 4: Write `backend/README.md`**: start dev stack, run migrations, run API + worker, run tests, get a token from the dev Keycloak (`researcher`/`researcher`).
- [ ] **Step 5: Commit** `ci: add backend image and GitLab pipeline`

---

## Done when

- `uv run pytest` and ruff pass locally and in GitLab CI; image builds.
- Manual check: with the dev stack up, a token for `researcher` can `POST /documents` a PDF, re-upload returns 409, `GET /me/uploads` lists it as `queued`, and `python -m roracle worker` runs idle without errors.
- Next plan: **P2 Ingestion** registers the `ingest` handler and adds per-stage status to `/me/uploads`.
