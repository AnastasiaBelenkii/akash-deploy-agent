# Codebase Knowledge

Cumulative knowledge captured during development. This file helps future sessions understand project context, patterns, and gotchas.

## Project Overview

**Project:** Solo hackathon project building tools for Akash Network deployment
**Language:** Python (stdlib only)
**Context:** Fast-paced hackathon, "working > pretty" principle applies

---

## 2025-02-25: Akash Network Deploy CLI

### What Was Built

`deploy.py` - CLI tool for deploying Docker containers to Akash Network via their Console Managed Wallet API.

**Key Commands:**
- `python deploy.py deploy <image> --port <port>` - Deploy image
- `python deploy.py list` - List deployments
- `python deploy.py close <dseq>` - Close deployment

**Environment Variable:**
- `AKASH_API_KEY` - Required for authentication

### API Patterns Learned

**Akash Console API (Base: `https://console-api.akash.network`):**

| Endpoint | Method | Purpose | Response Key Path |
|----------|--------|---------|-------------------|
| `/v1/deployments` | POST | Create deployment | `data.dseq`, `data.manifest` |
| `/v1/bids?dseq={dseq}` | GET | Get bids | `data[]` (array of bids) |
| `/v1/leases` | POST | Accept bid/create lease | `data.leases[0].status.services["web"].uris[0]` |
| `/v1/deployments` | GET | List deployments | `data.deployments[]` |
| `/v1/deployments/{dseq}` | DELETE | Close deployment | `data.success` |

**Headers Required:**
- `Content-Type: application/json`
- `x-api-key: <API_KEY>`

### Important Gotchas

1. **User-Agent Required:** Akash API blocks Python's default User-Agent. Must include `User-Agent` header (e.g., `"deploy.py/1.0"` or `"curl/7.81.0"`).

2. **URL Location:** The deployment URL is NOT from a separate endpoint. It's in the lease creation response at `data.leases[0].status.services["web"].uris[0]`. This was initially unclear from docs.

3. **Bid State Filtering:** Only bids with `bid.state == "open"` are available. Bids with `state == "closed"` are already taken.

4. **Price Comparison:** All bids use the same denom (IBC token address), so comparing `float(price.amount)` works for selecting cheapest.

5. **Service Name:** The SDL template uses service name "web" consistently. URL extraction also looks for "web" service.

6. **Bid ID Structure:** Bid IDs contain 4 fields needed for lease creation: `dseq`, `gseq`, `oseq`, `provider`.

7. **List Response Nesting:** GET /v1/deployments returns deployments wrapped as `data.deployments[].deployment` (extra nesting level).

### SDL Template Pattern

```yaml
version: "2.0"
services:
  web:
    image: <IMAGE>
    expose:
      - port: <CONTAINER_PORT>
        as: 80
        to:
          - global: true
    env:  # Optional
      - KEY=VALUE
profiles:
  compute:
    web:
      resources:
        cpu:
          units: <CPU>
        memory:
          size: <MEMORY>
        storage:
          - size: <STORAGE>
  placement:
    dcloud:
      pricing:
        web:
          denom: ibc/170C677610AC31DF0904FFE09CD3B5C657492170E7E52372E48756B71E56F2F1
          amount: 10000
deployment:
  web:
    dcloud:
      profile: web
      count: 1
```

### Python stdlib HTTP Pattern

```python
import urllib.request
import json

def api_request(method, url, data=None, api_key=None):
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))
```

### Error Handling Pattern

- Always catch `urllib.error.HTTPError` and print response body before re-raising
- Return `None` gracefully for "not ready" states (e.g., missing URIs)
- Raise explicit `RuntimeError` with clear messages for expected failures (no bids, invalid data)

### What NOT to Do

- Don't assume URL comes from a separate endpoint
- Don't forget to filter for `bid.state == "open"`
- Don't use `.get()` without checking for `None` before accessing nested fields
- Don't parse prices as Decimal; float is fine for comparison

### Future Enhancement Ideas

1. Add polling retry for transient network errors
2. Validate port ranges (1-65535) and positive deposits
3. Escape env var values for YAML special characters
4. Add `--wait` flag to poll until URL is available
5. Support custom service names beyond "web"
6. Add a `status` command to check deployment health

---

## Reference

See `agent_docs/akash-deploy-cli/` for detailed workflow artifacts from this task.

---

## 2025-02-25: FastAPI Agent with AkashML LLM Integration

### What Was Built

**Three components:**
1. `akash_core.py` - Extracted library from deploy.py (httpx-based, returns dicts)
2. `agent.py` - FastAPI web app with AkashML LLM integration
3. Deployment artifacts: Dockerfile, deploy-agent.sdl.yaml

**Architecture:**
- User sends natural language → FastAPI → AkashML Llama 3.3 70B
- LLM can call tools: deploy(), list_deployments(), close_deployment()
- Tools call Akash Console API → Results back to LLM → Final response
- HTML chat interface served inline (no separate files)

### AkashML API Integration

**Endpoint:** `https://api.akashml.com/v1`
**Model:** `meta-llama/Llama-3.3-70B-Instruct`
**Auth:** `Authorization: Bearer $AKASHML_API_KEY`
**Format:** OpenAI-compatible chat completions with tool/function calling

### Critical Gotchas

1. **Empty Tool Arguments Bug** - AkashML returns `arguments: ""` (empty string) for tools with no parameters. When sending this back in a follow-up request with tool results, the API returns 400 "Input is a zero-length, empty document: line 1 column 1 (char 0)".
   - **Fix:** Check if `arguments == ""` and convert to `"{}"` before appending to messages

2. **LLM Over-helpfulness** - LLM tries to call tools even for capability questions like "what can you do?"
   - For "what can you do?" → LLM calls list_deployments first (~15s), then answers
   - **Fix:** Skip tools for general questions by detecting keywords: ["what can you do", "help", "capabilities", "introduce"]

3. **Tool Calling Response Format** - When LLM decides to call tools:
   ```python
   {
       "choices": [{
           "message": {
               "role": "assistant",
               "content": "",  # Empty when tool_calls present
               "tool_calls": [{
                   "id": "call_...",
                   "type": "function",
                   "function": {
                       "name": "list_deployments",
                       "arguments": "{}"  # May be "" or "{}"
                   }
               }]
           }
       }]
   }
   ```

4. **Tool Result Format** - After executing tools, add to messages:
   ```python
   messages.append({
       "role": "tool",
       "tool_call_id": tool_call["id"],
       "content": json.dumps(result)
   })
   ```

5. **AkashML Performance** - LLM calls take 5-6 seconds consistently
   - This is inherent to the provider, not our code
   - Multiple round trips compound the delay
   - Recommendation: Minimize tool calls for simple queries

6. **httpx vs urllib** - httpx has slightly different exception types
   - `httpx.HTTPStatusError` instead of `urllib.error.HTTPError`
   - Need to wrap and convert to our own exception types
   - httpx.Client(timeout=30.0) for explicit timeout control

### FastAPI Patterns

**Inline HTML Interface:**
```python
@app.get("/", response_class=HTMLResponse)
async def get_chat_interface():
    return """<!DOCTYPE html>...</html"""
```
- Great for hackathons (no separate files)
- Not for production (harder to maintain)

**CORS Middleware:**
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
```
- Required for cross-origin requests (localtunnel testing)
- Use specific origins in production

**SSE Pattern (designed but not implemented):**
```python
from fastapi.responses import StreamingResponse
async def event_generator():
    yield f"event: progress\ndata: {json.dumps(event)}\n\n"
return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### Environment Variables Required

- `AKASH_API_KEY` - For Akash Console API
- `AKASHML_API_KEY` - For AkashML LLM endpoint

### Docker Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install httpx fastapi uvicorn
COPY *.py .
EXPOSE 8080
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]
```

### Localtunnel for Demo

**Installation:**
```bash
npm install -g localtunnel
lt --port 8080
```

**Gotchas:**
- Requires password on first visit (user's public IP)
- URL changes each time
- Adds ~500ms latency per request
- For demo, get password via: `curl https://loca.lt/mytunnelpassword`

### Performance Optimizations

1. **Bid Polling Reduction**
   - Original: 10 retries × 3s delay = 30s max
   - Optimized: 5 retries × 2s delay = 10s max
   - Trade-off: Slightly lower chance of finding bids, acceptable for demo

2. **Tool Skipping for General Questions**
   - Detect: ["what can you do", "help", "capabilities", ...]
   - Skip tools parameter entirely for these
   - Result: 30s → 7s for capability questions

3. **Timeout Increase**
   - Original: 5s (default httpx timeout)
   - Updated: 30s
   - Reason: Akash Console API often takes 15+ seconds

### Files Modified

```
deploy.py        485 → 140 lines (refactored to import from akash_core)
akash_core.py  350 lines (new library)
agent.py         390 lines (new FastAPI app)
Dockerfile        10 lines (new)
deploy-agent.sdl.yaml  43 lines (new)
```

### GitHub Repository

Created: `https://github.com/AnastasiaBelenkii/akash-deploy-agent`

All code committed and pushed with descriptive commit messages.

### Known Limitations for Production

1. **No authentication** on FastAPI endpoints
2. **No rate limiting** - could be abused
3. **No input validation** on message length
4. **Lib functions return dicts** instead of raising exceptions
5. **No progress visibility** during long operations (designed, not implemented due to time)
6. **Agent-browser** - Useful tool for testing web interfaces programmatically

### Future Work (Documented but Not Implemented)

See `PROGRESS_VISIBILITY_PLAN.md` for Server-Sent Events (SSE) design that would show real-time progress during deployments.

---

## 2025-02-26: Performance Optimization - Eliminate Second LLM Call

### What Was Fixed

Three performance bottlenecks were addressed:
1. **Two LLM round-trips per tool-using request** — Eliminated the second call that sent tool results back for "natural language summary"
2. **No `max_tokens` set** — Model was over-generating
3. **New `httpx.Client()` per call** — Fresh TCP+TLS handshake each time

### Key Changes

**agent.py:**
- Added module-level `_http_client = httpx.Client(timeout=120.0)` for connection reuse
- Added `max_tokens: 256` to LLM requests with fallback handling
- Added `_format_tool_result()` for Python-based result formatting
- Removed the `max_iterations` loop — tool results now formatted directly

**akash_core.py:**
- Added module-level `_http_client = httpx.Client(timeout=30.0)`
- Removed context manager pattern, use shared client directly

### Performance Results

| Operation | Before | After | Improvement |
|-----------|--------|-------|-------------|
| General questions | ~22s | ~4.5s | 5x faster |
| List/close | ~10s+ | ~5.5s | 2x faster |
| Deploy | ~24s | ~20s | 20% faster |

### Important Gotchas

1. **max_tokens parameter fallback** — AkashML may reject `max_tokens`. The code detects 400 errors with keywords like "max_tokens", "invalid request", "unrecognized" and retries without the parameter.

2. **httpx.Client is thread-safe** — Per httpx documentation, `Client` instances are thread-safe. This makes module-level clients safe for FastAPI's threadpool execution of sync endpoints.

3. **Manual formatting trade-off** — Removed LLM's ability to provide context-aware responses. The `_format_tool_result()` function handles success/error cases with hardcoded strings. This is faster but less flexible.

4. **Module-level client lifecycle** — The `httpx.Client` persists for the lifetime of the process. There's no explicit cleanup — the OS will handle this on process exit. This is acceptable for long-running server processes.

5. **Multi-worker deployments** — With `uvicorn --workers N`, each worker process gets its own module-level client. This is expected and correct behavior.

### Patterns Learned

**Eliminating Unnecessary LLM Calls:**
```python
# OLD: Send tool results back to LLM for summary
for tool_call in tool_calls:
    result = _execute_tool_call(...)
    messages.append({"role": "tool", "content": result})
response = _call_llm(messages, tools=TOOLS)  # Second call

# NEW: Format in Python
results = []
for tool_call in tool_calls:
    result = _execute_tool_call(...)
    results.append(_format_tool_result(function_name, result))
return "\n\n".join(results)  # No second call
```

**HTTP Connection Reuse:**
```python
# At module level
_http_client = httpx.Client(timeout=30.0)

# In functions
response = _http_client.get(url, headers=headers)
```

### Files Modified

```
agent.py         390 → 340 lines (net -50 lines)
akash_core.py    350 → 362 lines (net +12 lines)
```

### Decision Audit Findings

- **CRITICAL (FIXED):** Added error handling for `max_tokens` parameter rejection
- **NOTE:** Hard-coded `max_tokens: 256` value — could be made configurable
- **NOTE:** Emoji characters in formatting may not render in all terminals

### Related Documentation

- `PERFORMANCE_FIX_PLAN.md` — Implementation plan
- `PERFORMANCE_FIX_VERIFICATION.md` — Verification report with testing instructions

---

## 2025-02-26: Docker Containerization and GitHub Actions CI/CD

### What Was Built

**Docker image published to Docker Hub:**
- Image: `hoskayne/akash-deploy-agent:latest`
- Size: ~55 MB
- Docker Hub: https://hub.docker.com/r/hoskayne/akash-deploy-agent
- GitHub: https://github.com/AnastasiaBelenkii/akash-deploy-agent

**CI/CD Pipeline:**
- GitHub Actions workflow (`.github/workflows/docker-publish.yml`)
- Automatic builds on push to `main` branch
- Manual workflow dispatch available
- Builds and pushes in ~30 seconds

**Supporting files:**
- `docker-compose.yml` - Local development deployment
- `.env.example` - Environment variable template
- `README.md` - Usage documentation

### WSL Docker Build Workaround

**Problem:** Docker buildkit fails in WSL2 due to filesystem mount restrictions.
```
ERROR: failed to mount ... operation not permitted
```

**Solution:** Use GitHub Actions for cloud-based builds.
- Bypasses local WSL limitations entirely
- Uses GitHub's Ubuntu runners (native Docker support)
- Faster builds with better caching
- No local Docker daemon required

### GitHub Actions Workflow

**File:** `.github/workflows/docker-publish.yml`

**Triggers:**
- Push to `main`/`master` branches
- Manual workflow dispatch via `gh workflow run`

**Secrets required:**
- `DOCKER_USERNAME` - Docker Hub username (e.g., `hoskayne`)
- `DOCKER_PASSWORD` - Docker Hub PAT (Personal Access Token)

**Workflow steps:**
1. Checkout repository
2. Set up Docker Buildx
3. Log in to Docker Hub
4. Build and push image (`linux/amd64` platform)

### Running the Container

**Option 1: Direct Docker**
```bash
docker run -d \
  -p 8080:8080 \
  -e AKASH_API_KEY=your_key \
  -e AKASHML_API_KEY=your_key \
  hoskayne/akash-deploy-agent:latest
```

**Option 2: Docker Compose**
```bash
cp .env.example .env
# Edit .env with your API keys
docker-compose up -d
```

**Option 3: Deploy to Akash Network**
```bash
python deploy.py deploy hoskayne/akash-deploy-agent:latest --port 8080 \
  --env AKASH_API_KEY=your_key \
  --env AKASHML_API_KEY=your_key
```

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `AKASH_API_KEY` | Akash Console API key | Yes |
| `AKASHML_API_KEY` | AkashML LLM API key | Yes |

### Important Gotchas

1. **WSL Docker Limitations** — WSL2 doesn't support all required syscalls for Docker's buildkit mount operations. Use cloud builds (GitHub Actions) or Docker Desktop for Windows.

2. **Docker Hub PAT vs Password** — Use a Personal Access Token (PAT) instead of your password. Create at: https://hub.docker.com/settings/security

3. **Image Tag Strategy** — Currently using `latest` tag. For production, consider semantic versioning (`v1.0.0`, etc.) and git-tag-based releases.

4. **Platform Specificity** — Build is pinned to `linux/amd64`. For multi-arch support (ARM, etc.), modify the workflow platforms list.

### Patterns Learned

**GitHub Actions Docker Publishing:**
```yaml
- name: Build and push Docker image
  uses: docker/build-push-action@v5
  with:
    context: .
    push: true
    tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
    platforms: linux/amd64
```

**Setting secrets via GitHub CLI:**
```bash
gh secret set DOCKER_USERNAME -b "your_username"
gh secret set DOCKER_PASSWORD -b "dckr_pat_..."
```

**Triggering workflows manually:**
```bash
gh workflow run workflow-name.yml
gh run watch <run-id>
```

### Dockerfile

The project uses a simple, multi-stage-free Dockerfile:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir httpx fastapi uvicorn
COPY agent.py akash_core.py deploy.py .
EXPOSE 8080
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]
```

### Future Enhancements

1. **Multi-architecture support** — Add ARM64 builds for Apple Silicon/ARM servers
2. **Semantic versioning** — Use git tags to trigger versioned releases
3. **Security scanning** — Add Trivy or Snyk scans to workflow
4. **Test coverage** — Run tests in CI before building images
5. **Automatic deployment** — Add Akash deployment step to workflow

### Files Created

```
.github/workflows/docker-publish.yml  30 lines (new)
docker-compose.yml                     18 lines (new)
.env.example                            6 lines (new)
README.md                              41 lines (new)
```

