"""Build bronze, silver, and gold Delta tables for platform run events.

This file is executed by Databricks as a Spark Python task. PySpark and Delta
Lake are supplied by the Databricks runtime and are intentionally not runtime
dependencies of the API service.
"""

from __future__ import annotations

import argparse
import json
import re

from pyspark.sql import SparkSession, functions as F


SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def identifier(value: str) -> str:
    if not SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe Unity Catalog identifier: {value!r}")
    return value


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--catalog", required=True, type=identifier)
    parser.add_argument("--bronze-schema", default="bronze", type=identifier)
    parser.add_argument("--silver-schema", default="silver", type=identifier)
    parser.add_argument("--gold-schema", default="gold", type=identifier)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    spark = SparkSession.builder.appName("agent-platform-run-analytics").getOrCreate()

    bronze_table = f"{args.catalog}.{args.bronze_schema}.run_events"
    silver_table = f"{args.catalog}.{args.silver_schema}.run_facts"
    gold_table = f"{args.catalog}.{args.gold_schema}.daily_run_metrics"

    for schema_name in (args.bronze_schema, args.silver_schema, args.gold_schema):
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {args.catalog}.{schema_name}")

    incoming = (
        spark.read.json(args.source_path)
        .select(
            F.col("id").cast("string").alias("event_id"),
            F.col("app_id").cast("string"),
            F.col("topic").cast("string"),
            F.col("source").cast("string"),
            F.to_timestamp("created_at").alias("created_at"),
            F.to_json("payload").alias("payload_json"),
        )
        .where(F.col("event_id").isNotNull())
        .dropDuplicates(["event_id"])
        .withColumn("ingested_at", F.current_timestamp())
        .withColumn("source_file", F.input_file_name())
    )
    incoming.createOrReplaceTempView("incoming_run_events")

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {bronze_table} (
          event_id STRING,
          app_id STRING,
          topic STRING,
          source STRING,
          created_at TIMESTAMP,
          payload_json STRING,
          ingested_at TIMESTAMP,
          source_file STRING
        ) USING DELTA
        """
    )
    spark.sql(
        f"""
        MERGE INTO {bronze_table} target
        USING incoming_run_events source
        ON target.event_id = source.event_id
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """
    )

    spark.sql(
        f"""
        CREATE OR REPLACE TABLE {silver_table}
        USING DELTA
        AS SELECT
          event_id,
          app_id,
          topic,
          CASE
            WHEN topic LIKE 'workflow.run.%' THEN 'workflow'
            WHEN topic LIKE 'run.%' THEN 'agent'
            ELSE 'platform'
          END AS run_type,
          element_at(split(topic, '\\.'), -1) AS lifecycle_state,
          source,
          created_at,
          payload_json,
          ingested_at
        FROM {bronze_table}
        WHERE app_id IS NOT NULL
        """
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TABLE {gold_table}
        USING DELTA
        AS SELECT
          app_id,
          date(created_at) AS event_date,
          run_type,
          lifecycle_state,
          count(*) AS event_count,
          max(created_at) AS latest_event_at
        FROM {silver_table}
        GROUP BY app_id, date(created_at), run_type, lifecycle_state
        """
    )

    result = {
        "bronze_table": bronze_table,
        "silver_table": silver_table,
        "gold_table": gold_table,
        "gold_row_count": spark.table(gold_table).count(),
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
