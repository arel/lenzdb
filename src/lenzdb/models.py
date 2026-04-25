"""Pydantic models for schemas and policies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ColumnType = Literal[
    "string",
    "integer",
    "float",
    "boolean",
    "enum",
    "ref",
    "date",
    "timestamp",
]


class ColumnSchema(BaseModel):
    type: ColumnType = "string"
    immutable: bool = False
    values: list[str] = Field(default_factory=list)
    table: str | None = None

    @model_validator(mode="after")
    def validate_constraints(self) -> ColumnSchema:
        if self.type == "enum" and not self.values:
            raise ValueError("enum columns require non-empty `values`")
        if self.type == "ref" and not self.table:
            raise ValueError("ref columns require `table`")
        return self


class TableSchema(BaseModel):
    table: str
    primary_key: str
    columns: dict[str, ColumnSchema]

    @model_validator(mode="after")
    def validate_primary_key(self) -> TableSchema:
        if self.primary_key not in self.columns:
            raise ValueError(
                f"primary_key {self.primary_key!r} is not defined in columns for table {self.table!r}"
            )
        return self


class ReferenceLookup(BaseModel):
    table: str
    match: str
    create_if_missing: bool = False


class ReferencePolicy(BaseModel):
    display: str
    write_to: str
    lookup: ReferenceLookup


class LensPolicy(BaseModel):
    lens: str
    primary_table: str
    primary_key: str
    editable: dict[str, str] = Field(default_factory=dict)
    references: dict[str, ReferencePolicy] = Field(default_factory=dict)
