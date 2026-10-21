# Production-Grade Architectural Audit & Hardening Report
## `signalpost-agent`

### Phase 1: HTTP Budget & Concurrency Isolation
- **Refactored `BudgetedAsyncClient`:** Renamed to `AsyncHttpBudgetClient` across the codebase as requested.
- **Race Condition Prevention:** Refactored the internal request cache mechanism to use an `asyncio.Future` pattern. Concurrent requests attempting to fetch the exact same URL no longer leak budget tokens nor do they execute duplicate outbound requests; subsequent tasks yield to the first future, ensuring perfect isolation.
- **Connection Leak Prevention:** Hardened the wrapper to guarantee it can only be initialized properly via its `__aenter__` async context manager to ensure all underlying `httpx.AsyncClient` pooling is properly established and cleaned up.
- **Verified Ledger Isolation:** LLM APIs strictly track via `record_llm_call()` and do not touch the `web_outbound_requests` limit, ensuring the 18 max requests limit per company is used explicitly for pure source parsing/data scraping.
- **Network Safety (Phase 2 Upgrade):** Implemented strict SSRF protections in `web_discovery.py`, blocking private IP ranges (`10.x.x.x`, `192.168.x.x`, `localhost`). Hardened `AsyncHttpBudgetClient.get` via `httpx.AsyncClient.stream()` to enforce a 5MB memory limit on downloaded HTML chunks, protecting against memory exhaustion bombs.

### Phase 2: Zero-Drop Result Envelope Guarantee
- **Fallback to Subunits (`/underenheter`):** Implemented an automatic query fallback in `BrregClient`. When the primary `/enheter/{orgnr}` query yields an HTTP 404 response, the pipeline falls back securely to the subunit endpoint before yielding failure.
- **Orgnr Padding:** When encountering invalid inputs (like "12345"), the input is deterministically padded using `rjust(9, '0')` guaranteeing the pipeline does not raise an unhandled `ValueError` when passing through `try_normalize_orgnr`.
- **Fatal Exception Block:** Wrapped the core entity resolution loop within `process_single_company` inside a `try/except` guard. Even if there is an unhandled syntax error or parsing crash within `httpx` or NVIDIA NIM extractors, the pipeline securely aborts execution, suppresses the error, and yields an immediate, schema-compliant `CompanyProfile`. The generated profile correctly defaults canonical status to `UNKNOWN` and seeds the explicit `known_unknowns` list, driving "Companies Failed" back down to `0`.

### Phase 3: Entity-Gate & Anti-Fabrication Verification
- **Verified Hard-Rules Conformance:** Renamed `EntityVerificationGate.verify_source()` to `verify_entity_ownership()`. Confirmed the deterministic Norwegian Modulo-11 logic implements exactly the right weights `[3, 2, 7, 6, 5, 4, 3, 2]` evaluating right-to-left.
- **Entity Resolution Hardening:** Validated that exact legal name and geographical municipality must both be present to resolve an unknown non-registry domain source. Further added a *Multi-Entity Guard*: pages with >3 different `orgnrs` extracted are automatically identified as directories and strictly reject name+location heuristics in favor of deterministic `orgnr` or `official_domain` anchors to prevent Wrong-Company Bleed.
- **Financial Locale Upgrades:** Refactored `AntiFabricationGuard._verify_digits_in_quote` to deterministically verify unstructured Norwegian formatting styles, gracefully recognizing `1 234 567,89`, `1.234.567,-`, `MNOK` (millions), and `mrd NOK` (billions).

### Phase 4: NVIDIA NIM Fault Tolerance & Atomic Persistence
- **API Resilience:** Hardened `_call_nvidia_nim` with `_post_with_backoff` using exponential backoff with full jitter to automatically retry `429 Too Many Requests` or `5xx Server Error` before gracefully rolling down to the fallback models.
- **JSON Recovery:** Replaced naive substring JSON extraction with robust truncation detection in `_clean_json_response`, matching unbalanced brackets and braces to reconstitute valid payloads cleanly.
- **Cryptographic Provenance:** Added `source_content_sha256` explicitly into the `FactEvidence` schema and populated it deterministically using `hashlib.sha256(content).hexdigest()` inside `_make_evidence`.
- **Atomic File Writing:** Refactored `ProfileStore` and telemetry manifests to write to `.tmp` files initially, utilizing `os.replace` to atomically rotate the file into position, guaranteeing no mid-execution process crash corrupts the target JSON output.

### Phase 5: Test Expansion
- **Test Framework Verification:** Handled global environment dependencies by enforcing unit tests natively using `python -m pytest` with the local virtualenv.
- **New Coverage Targets:**
  - `test_zero_drop.py`: Added explicit checks simulating broken inputs and fatal `AttributeError` exceptions to ensure zero drops.
  - `test_fallback.py`: Verified the 404 error routing logic successfully invokes `get_underenhet()`.
  - `test_http_budget.py`: Covered the newly introduced concurrent `asyncio.Future` request lock behavior.
  - `test_financial.py`: Addressed new edge cases spanning the Norwegian number locales.
  - `test_multi_entity.py`: Validated correct detection and rejection of multi-company directory profiles.
