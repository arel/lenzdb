"""DuckDB-backed query execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from lenzdb.errors import ProjectError
from lenzdb.project import Project, split_resource_key


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def duckdb_type(column_type: str) -> str:
    return {
        "string": "VARCHAR",
        "integer": "BIGINT",
        "float": "DOUBLE",
        "boolean": "BOOLEAN",
        "enum": "VARCHAR",
        "ref": "VARCHAR",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
    }.get(column_type, "VARCHAR")


@dataclass(slots=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]


def build_connection(
    project: Project, rows_by_table: dict[str, list[dict[str, Any]]] | None = None
) -> duckdb.DuckDBPyConnection:
    rows_by_table = rows_by_table or project.load_all_rows()
    connection = duckdb.connect(database=":memory:")

    for table_name, schema in project.schemas.items():
        namespace, local_name = split_resource_key(table_name)
        if namespace != "main":
            connection.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(namespace)}")
        qualified_table_name = f"{quote_identifier(namespace)}.{quote_identifier(local_name)}"
        columns_sql = ", ".join(
            f"{quote_identifier(column_name)} {duckdb_type(column.type)}"
            for column_name, column in schema.columns.items()
        )
        connection.execute(f"CREATE TABLE {qualified_table_name} ({columns_sql})")

        rows = rows_by_table.get(table_name, [])
        if not rows:
            continue
        placeholders = ", ".join("?" for _ in schema.columns)
        insert_sql = f"INSERT INTO {qualified_table_name} VALUES ({placeholders})"
        values = [[row.get(column_name) for column_name in schema.columns] for row in rows]
        connection.executemany(insert_sql, values)

    return connection


def resolve_sql_table_references(project: Project, sql: str) -> str:
    try:
        expression = parse_one(sql, read="duckdb")
    except ParseError:
        return sql

    cte_names = {cte.alias for cte in expression.find_all(exp.CTE)}
    for table_expression in expression.find_all(exp.Table):
        if not table_expression.db and table_expression.name in cte_names:
            continue
        table_name = project.resolve_table_name(
            table_expression.name, table_expression.db or None
        )
        namespace, local_name = split_resource_key(table_name)
        table_expression.set("this", exp.to_identifier(local_name))
        table_expression.set("db", exp.to_identifier(namespace))
    return expression.sql(dialect="duckdb")


def query_lens(
    project: Project, lens_name: str, rows_by_table: dict[str, list[dict[str, Any]]] | None = None
) -> QueryResult:
    sql = project.lens_sql(lens_name)
    try:
        sql = resolve_sql_table_references(project, sql)
    except ProjectError as exc:
        raise ProjectError(f"Invalid SQL for lens {lens_name!r}: {exc}") from exc
    connection = build_connection(project, rows_by_table)
    try:
        cursor = connection.execute(sql)
        columns = [description[0] for description in cursor.description]
        rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        return QueryResult(columns=columns, rows=rows)
    finally:
        connection.close()
