# DEVOPS Change Impact Analysis Agent (Python)

## Setup

```bash
cd impact-agent-python
cp .env.example .env
docker compose up --build
```

## Quick Start

```bash
./bootstrap.sh
./healthcheck.sh
```

This project runs standalone. Cloning the microservices repo locally is optional.

## Environment Variables

- `GEMINI_API_KEY`: Optional. If omitted, deterministic heuristic reasoning is used.
- `GEMINI_MODEL`: Default `gemini-1.5-flash`
- `GEMINI_API_BASE_URL`: Default `https://generativelanguage.googleapis.com/v1beta`
- `GEMINI_TIMEOUT_SECONDS`: Default `30`
- `GITHUB_TOKEN`: Optional PAT for private repo access and higher GitHub API limits
- `GITHUB_API_BASE_URL`: Default `https://api.github.com`
- `GITHUB_API_TIMEOUT_SECONDS`: Default `25`
- `GITHUB_WORKFLOW_FILE`: GitHub Actions workflow file to dispatch for regression checks. Default `regression-dispatch.yml`
- `GITHUB_WORKFLOW_REF`: Branch/tag ref used for workflow dispatch. Default `master`
- `GITHUB_WORKFLOW_LOOKUP_TIMEOUT_SECONDS`: Wait time for workflow run creation. Default `60`
- `GITHUB_WORKFLOW_TIMEOUT_SECONDS`: Wait time for workflow completion. Default `420`
- `GITHUB_WORKFLOW_POLL_SECONDS`: Poll interval for workflow status checks. Default `5`
- `MICROSERVICES_PROJECT_PATH`: Optional absolute path to a local microservices repo for dependency-graph enrichment and local demo patch support.
- `JWT_SECRET`: JWT signing secret
- `MYSQL_URL`: SQLAlchemy connection URL
- `REDIS_URL`: Redis connection URL
- `RATE_LIMIT`: API rate limit, e.g. `20/minute`

## API Usage

1. Login and get token:

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

2. Analyze PR:

```bash
TOKEN="<paste-token>"
curl -s -X POST http://localhost:8000/analyze-pr \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/example/microservices-project","pr_number":24,"use_llm":false,"run_regression_tests":false}'
```

3. Analyze private repo:

```bash
TOKEN="<paste-token>"
curl -s -X POST http://localhost:8000/analyze-pr \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/<org>/<private-repo>","pr_number":1,"use_llm":false,"run_regression_tests":true,"github_token":"<github-pat>"}'
```

## Notes

- PR retrieval is deterministic for demo with `microservices-project/sample-pr/pr-24.patch`.
- For non-demo PRs, service uses GitHub API first, then git ref fallback.
- Dependency graph is computed from real JS import relationships using NetworkX.
- Risk scoring combines code churn, dependency depth, core-service multiplier, and LLM severity.
- Structured analysis history is persisted to MySQL.
- Analyze flow includes sanity checks for PR payload and graph output.
- Gemini quota/API failures automatically fall back to heuristic reasoning.
- Response includes `sanityCheckResults` and `regressionTestResults`; set `run_regression_tests=true` to dispatch GitHub Actions regression tests (falls back to local pytest when dispatch is skipped).

## Regression Tests

```bash
cd impact-agent-python
python3 -m pytest -q
```

Current regression coverage includes:
- LLM failure fallback behavior
- GitHub token/header handling for private repositories
- `/analyze-pr` error mapping and no-LLM success path
