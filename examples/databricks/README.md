# Databricks lakehouse example

This bundle deploys a serverless Lakeflow Job that reads platform event JSON,
uses PySpark to normalize and idempotently merge bronze events, and uses Spark
SQL to materialize silver run facts and gold daily metrics as Delta tables.

## Deploy

Configure Databricks unified authentication, then run:

```bash
cd examples/databricks
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev run_analytics
```

The deployed job ID is shown by the Databricks CLI. Replace `12345` in
`platform-workflow.json` with that ID, then create the platform workflow:

```bash
curl -X POST http://127.0.0.1:8000/v1/workflows \
  -H "x-api-key: $DEVPLATFORM_API_KEY" \
  -H 'content-type: application/json' \
  --data @platform-workflow.json
```

The API service uses `DEVPLATFORM_DATABRICKS_HOST` plus either a token or OAuth
service-principal credentials. Those secrets are never included in the
workflow JSON or persisted execution request.

The declared lineage is contractual metadata: on successful completion the
platform records the silver and gold tables as Delta artifacts. Databricks and
Unity Catalog remain responsible for physical table access, privileges,
auditing, and engine-generated lineage.
