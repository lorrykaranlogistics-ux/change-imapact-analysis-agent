# Enterprise DevOps Change Impact Analysis Agent (Python)

## Setup

```bash
cd impact-agent-python
cp .env.example .env
docker compose up --build
```

## Environment Variables

- `OPENAI_API_KEY`: Optional. If omitted, deterministic heuristic reasoning is used.
- `OPENAI_MODEL`: Default `gpt-4o-mini`
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
  -d '{"repo_url":"https://github.com/example/sample-microservices-node","pr_number":24,"use_llm":true}'
```

## Enterprise Notes

- PR retrieval is deterministic for demo with `sample-microservices-node/sample-pr/pr-24.patch`.
- Dependency graph is computed from real JS import relationships using NetworkX.
- Risk scoring combines code churn, dependency depth, core-service multiplier, and LLM severity.
- Structured analysis history is persisted to MySQL.
