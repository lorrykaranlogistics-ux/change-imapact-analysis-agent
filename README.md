# DEVOPS Change Impact Analysis Agent (Python)

## Setup

```bash
cd impact-agent-python
cp .env.example .env
docker compose up --build
```

## Environment Variables

- `OPENAI_API_KEY`: Optional. If omitted, deterministic heuristic reasoning is used.
- `OPENAI_MODEL`: Default `gpt-4o-mini`
- `GITHUB_TOKEN`: Optional PAT for private repo access and higher GitHub API limits
- `GITHUB_API_BASE_URL`: Default `https://api.github.com`
- `GITHUB_API_TIMEOUT_SECONDS`: Default `25`
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
  -d '{"repo_url":"https://github.com/example/sample-microservices-node","pr_number":24,"use_llm":false,"run_regression_tests":false}'
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

- PR retrieval is deterministic for demo with `sample-microservices-node/sample-pr/pr-24.patch`.
- For non-demo PRs, service uses GitHub API first, then git ref fallback.
- Dependency graph is computed from real JS import relationships using NetworkX.
- Risk scoring combines code churn, dependency depth, core-service multiplier, and LLM severity.
- Structured analysis history is persisted to MySQL.
- Analyze flow includes sanity checks for PR payload and graph output.
- LLM quota/API failures automatically fall back to heuristic reasoning.
- Response includes `sanityCheckResults` and `regressionTestResults`; set `run_regression_tests=true` to execute pytest during analysis.

## Regression Tests

```bash
cd impact-agent-python
python3 -m pytest -q
```

Current regression coverage includes:
- LLM failure fallback behavior
- GitHub token/header handling for private repositories
- `/analyze-pr` error mapping and no-LLM success path
