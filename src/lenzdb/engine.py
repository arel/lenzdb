"""DuckDB-backed query execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb
from duckdb import Error as DuckDBError
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from lenzdb.errors import ProjectError
from lenzdb.project import Project, normalize_primary_key, split_resource_key


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


@dataclass(slots=True)
class ResourceQuery:
    columns: list[str] | None = None
    distinct: list[str] | None = None
    where: str | None = None
    order: list[str] | None = None
    limit: int | None = None
    offset: int | None = None
    count: bool = False
    sql: str | None = None


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


def referenced_sql_tables(project: Project, sql: str) -> set[str]:
    try:
        expression = parse_one(sql, read="duckdb")
    except ParseError:
        return set()

    cte_names = {cte.alias for cte in expression.find_all(exp.CTE)}
    tables: set[str] = set()
    for table_expression in expression.find_all(exp.Table):
        if not table_expression.db and table_expression.name in cte_names:
            continue
        tables.add(
            project.resolve_table_name(table_expression.name, table_expression.db or None)
        )
    return tables


def query_lens(
    project: Project, lens_name: str, rows_by_table: dict[str, list[dict[str, Any]]] | None = None
) -> QueryResult:
    resolved_lens_name = project.resolve_lens_name(lens_name)
    sql = project.lens_sql(lens_name)
    try:
        sql = resolve_sql_table_references(project, sql)
    except ProjectError as exc:
        raise ProjectError(f"Invalid SQL for lens {lens_name!r}: {exc}") from exc
    scoped_rows = rows_by_table
    if scoped_rows is None:
        scoped_rows = {
            table: project.load_table_rows(table)
            for table in referenced_sql_tables(project, project.lens_sql(resolved_lens_name))
        }
    connection = build_connection(project, scoped_rows)
    try:
        cursor = connection.execute(sql)
        columns = [description[0] for description in cursor.description]
        rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        return QueryResult(columns=columns, rows=rows)
    finally:
        connection.close()


def table_sql(project: Project, table_name: str) -> str:
    resolved_table = project.resolve_table_name(table_name)
    namespace, local_name = split_resource_key(resolved_table)
    return f"SELECT * FROM {quote_identifier(namespace)}.{quote_identifier(local_name)}"


def lens_sql(project: Project, lens_name: str) -> str:
    sql = project.lens_sql(lens_name).strip().removesuffix(";")
    try:
        return resolve_sql_table_references(project, sql)
    except ProjectError as exc:
        raise ProjectError(f"Invalid SQL for lens {lens_name!r}: {exc}") from exc


def resource_sql(project: Project, resource_name: str) -> str:
    resource_kind, resolved_name = project.resolve_resource_name(resource_name)
    if resource_kind == "lens":
        return lens_sql(project, resolved_name)
    return table_sql(project, resolved_name)


def resource_tables(project: Project, resource_name: str) -> set[str]:
    resource_kind, resolved_name = project.resolve_resource_name(resource_name)
    if resource_kind == "lens":
        return referenced_sql_tables(project, project.lens_sql(resolved_name))
    return {resolved_name}


def load_resource_rows(project: Project, resource_name: str) -> dict[str, list[dict[str, Any]]]:
    return {table: project.load_table_rows(table) for table in resource_tables(project, resource_name)}


def fetch_query_result(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    error_prefix: str = "Query failed",
) -> QueryResult:
    try:
        cursor = connection.execute(sql)
    except DuckDBError as exc:
        raise ProjectError(f"{error_prefix}: {exc}") from exc
    columns = [description[0] for description in cursor.description]
    rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
    return QueryResult(columns=columns, rows=rows)


def validate_query_columns(
    requested_columns: list[str],
    available_columns: list[str],
    *,
    option_name: str,
) -> None:
    unknown = [column for column in requested_columns if column not in available_columns]
    if unknown:
        raise ProjectError(
            f"Unknown {option_name} column(s): {', '.join(unknown)}. "
            f"Available columns: {', '.join(available_columns)}"
        )


def order_expression(order_column: str) -> tuple[str, str]:
    direction = "DESC" if order_column.startswith("-") else "ASC"
    column = order_column[1:] if order_column.startswith("-") else order_column
    if not column:
        raise ProjectError("Order columns cannot be empty")
    return column, direction


def build_resource_view_sql(
    base_sql: str,
    available_columns: list[str],
    query: ResourceQuery,
) -> str:
    if query.sql:
        return f"WITH resource AS ({base_sql}) {query.sql.strip().removesuffix(';')}"

    if query.count:
        select_sql = "SELECT count(*) AS count"
    elif query.distinct:
        validate_query_columns(query.distinct, available_columns, option_name="distinct")
        select_sql = "SELECT DISTINCT " + ", ".join(
            quote_identifier(column) for column in query.distinct
        )
    elif query.columns:
        validate_query_columns(query.columns, available_columns, option_name="selected")
        select_sql = "SELECT " + ", ".join(quote_identifier(column) for column in query.columns)
    else:
        select_sql = "SELECT *"

    sql = f"WITH resource AS ({base_sql}) {select_sql} FROM resource"
    if query.where:
        sql += f" WHERE {query.where}"
    if query.order:
        parsed_order = [order_expression(column) for column in query.order]
        validate_query_columns(
            [column for column, _direction in parsed_order],
            available_columns,
            option_name="order",
        )
        sql += " ORDER BY " + ", ".join(
            f"{quote_identifier(column)} {direction}" for column, direction in parsed_order
        )
    if query.limit is not None:
        sql += f" LIMIT {query.limit}"
    if query.offset is not None:
        sql += f" OFFSET {query.offset}"
    return sql


def build_resource_view_query_sql(
    project: Project,
    resource_name: str,
    query: ResourceQuery,
    connection: duckdb.DuckDBPyConnection,
) -> str:
    base_sql = resource_sql(project, resource_name)
    base_result = fetch_query_result(
        connection,
        f"WITH resource AS ({base_sql}) SELECT * FROM resource LIMIT 0",
        error_prefix="Resource query failed",
    )
    return build_resource_view_sql(base_sql, base_result.columns, query)


def query_resource_view(project: Project, resource_name: str, query: ResourceQuery) -> QueryResult:
    connection = build_connection(project, load_resource_rows(project, resource_name))
    try:
        sql = build_resource_view_query_sql(project, resource_name, query, connection)
        return fetch_query_result(connection, sql)
    finally:
        connection.close()


def describe_resource_view(project: Project, resource_name: str, query: ResourceQuery) -> QueryResult:
    connection = build_connection(project, load_resource_rows(project, resource_name))
    try:
        sql = build_resource_view_query_sql(project, resource_name, query, connection)
        result = fetch_query_result(connection, f"DESCRIBE ({sql})")
    finally:
        connection.close()

    resource_kind, resolved_name = project.resolve_resource_name(resource_name)
    primary_keys: set[str] = set()
    if resource_kind == "table":
        primary_keys = set(project.primary_key_columns(resolved_name))
    elif resource_kind == "lens" and resolved_name in project.policies:
        primary_keys = set(normalize_primary_key(project.policies[resolved_name].primary_key))

    rows = []
    for row in result.rows:
        column_name = row.get("column_name")
        primary_key = column_name in primary_keys
        rows.append(
            {
                "column": column_name,
                "type": row.get("column_type"),
                "primary_key": "yes" if primary_key else "no",
            }
        )
    return QueryResult(columns=["column", "type", "primary_key"], rows=rows)


def query_table(
    project: Project, table_name: str, rows_by_table: dict[str, list[dict[str, Any]]] | None = None
) -> QueryResult:
    resolved_table = project.resolve_table_name(table_name)
    columns = project.table_headers(resolved_table)
    rows = (rows_by_table or {resolved_table: project.load_table_rows(resolved_table)}).get(
        resolved_table, []
    )
    return QueryResult(columns=columns, rows=rows)


def query_resource(
    project: Project, resource_name: str, rows_by_table: dict[str, list[dict[str, Any]]] | None = None
) -> QueryResult:
    resource_kind, resolved_name = project.resolve_resource_name(resource_name)
    if resource_kind == "lens":
        return query_lens(project, resolved_name, rows_by_table)
    return query_table(project, resolved_name, rows_by_table)
