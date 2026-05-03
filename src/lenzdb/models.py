"""Pydantic models for schemas and lens metadata."""

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
    kind: Literal["table"] = "table"
    name: str | None = None
    path: str | None = None
    table: str
    primary_key: str | list[str]
    columns: dict[str, ColumnSchema]

    @model_validator(mode="after")
    def validate_primary_key(self) -> TableSchema:
        primary_keys = [self.primary_key] if isinstance(self.primary_key, str) else self.primary_key
        if not primary_keys:
            raise ValueError(f"primary_key must include at least one column for table {self.table!r}")
        duplicates = sorted({column for column in primary_keys if primary_keys.count(column) > 1})
        if duplicates:
            raise ValueError(
                f"primary_key contains duplicate column(s) for table {self.table!r}: "
                f"{', '.join(duplicates)}"
            )
        missing = [column for column in primary_keys if column not in self.columns]
        if missing:
            raise ValueError(
                f"primary_key column(s) {missing!r} are not defined in columns for table {self.table!r}"
            )
        if self.name is not None and not self.name:
            raise ValueError("table manifest name must not be empty")
        if self.path is not None and not self.path:
            raise ValueError("table manifest path must not be empty")
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
    primary_key: str | list[str]
    editable: dict[str, str] = Field(default_factory=dict)
    references: dict[str, ReferencePolicy] = Field(default_factory=dict)


class LensManifest(BaseModel):
    kind: Literal["lens"] = "lens"
    name: str
    path: str

    @model_validator(mode="after")
    def validate_manifest(self) -> LensManifest:
        if not self.name:
            raise ValueError("lens manifest name must not be empty")
        if not self.path:
            raise ValueError("lens manifest path must not be empty")
        return self
