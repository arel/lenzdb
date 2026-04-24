# 📦 Project: **LenzDB**

**CLI:** `lnz`
**Tagline:** *Lenses over your data.*

---

# 1. 🧭 Vision

> A Git-native, text-first relational data system where **SQL views (“lenses”) are the primary interface**, and **safe edits to those views propagate back to source data**.

LenzDB combines:

* Plain-text data (CSV/JSON/etc.)
* SQL-based projections (“lenses”)
* Safe, policy-driven writeback
* Notion-like workflows (kanban, tagging, etc.)

---

# 2. 🧠 Core Concept

> A **lens** is a projection of data that may be partially editable.

```text
data → lens (SQL) → user edits → safe writeback → data
```

---

# 3. 🎯 Core Principles

### 1. Git-native

* All state lives in files
* Diffable, mergeable, auditable

### 2. Lenses are the interface

* SQL defines how data is seen
* Users interact with lenses, not raw tables

### 3. Safe writeback

* Only allow edits that map unambiguously to source data
* Reject or require policy otherwise

### 4. Separation of concerns

```text
SQL = read model
Policy = write model
```

### 5. Composable behavior

* Domain semantics (kanban, tagging, workflows) layered on top

---

# 4. 🧱 System Architecture

```text
/data        ← authoritative data
/schema      ← types, keys, relationships
/lenses      ← SQL files (views)
/policies    ← write rules + behaviors
```

---

# 5. ⚙️ Tech Stack

```text
Language:        Python 3.12+
CLI:             Typer
Query engine:    DuckDB (embedded)
SQL parser:      SQLGlot
Schema:          Pydantic + YAML
Packaging:       uv
Testing:         pytest
Lint/format:     ruff
```

---

# 6. 📁 Example Project

```text
my-project/
  data/
    tasks.csv
    projects.csv

  schema/
    tasks.yaml
    projects.yaml

  lenses/
    open_tasks.sql
    kanban.sql

  policies/
    open_tasks.yaml
    kanban.yaml
```

---

# 7. 🧾 Schema Example

```yaml
table: tasks
primary_key: id

columns:
  id:
    type: string
    immutable: true

  title:
    type: string

  status:
    type: enum
    values: [todo, doing, done]

  project_id:
    type: ref
    table: projects
```

---

# 8. 🔍 Lens Definition (SQL)

```sql
select
  t.id,
  t.title,
  t.status,
  p.name as project_name
from tasks t
join projects p on p.id = t.project_id
where t.status != 'done'
```

---

# 9. ✏️ Writability Model

### Column classification

| Type                | Writable    |
| ------------------- | ----------- |
| direct base column  | ✅           |
| aliased base column | ✅           |
| joined lookup       | ❌ (default) |
| computed            | ❌           |
| aggregate           | ❌           |

---

### Row identity rule

A lens row is editable if:

```text
maps to exactly one base row
AND
primary key is present
```

---

# 10. 🧠 Policy Layer

Defines how edits propagate.

```yaml
lens: open_tasks

primary_table: tasks
primary_key: id

editable:
  title: tasks.title
  status: tasks.status

references:
  project_name:
    display: projects.name
    write_to: tasks.project_id
    lookup:
      table: projects
      match: name
      create_if_missing: true
```

---

# 11. 🔄 Notion-like Behaviors

Example: Kanban

```yaml
lens: kanban

group_by: status

on_move:
  column: status
  actions:
    - set: status = new_value
    - if:
        condition: started_at is null
        then:
          set: started_at = now()
```

---

# 12. 🖥️ CLI (`lnz`)

### Viewing

```bash
lnz view open_tasks
lnz view open_tasks --format markdown
lnz view kanban --format board
lnz view open_tasks --format csv > tmp.csv
```

---

### Editing

```bash
lnz edit open_tasks
lnz apply open_tasks tmp.csv
```

---

### Validation & introspection

```bash
lnz check
lnz diff
lnz plan open_tasks tmp.csv
lnz explain open_tasks
```

---

# 13. 🔄 Writeback Flow

```text
1. User edits lens output
2. lnz loads edited data
3. Match rows via primary key
4. Compute diff
5. Validate writability
6. Apply policy rules
7. Generate mutations
8. Update source files
9. Re-validate constraints
```

---

# 14. ⚠️ Safety Model

Reject edits if:

* no primary key
* multiple base rows per lens row
* ambiguous reference resolution
* editing computed/aggregated column

---

# 15. 📤 Output Formats

```bash
--format table
--format csv
--format json
--format ndjson
--format markdown
--format html
--format board   # kanban
```

---

# 16. 🧩 Extensibility

* custom output renderers
* policy plugins
* validation rules
* import/export adapters

---

# 17. 🚀 MVP Roadmap

### Phase 1

* DuckDB + CSV
* `lnz view`
* basic schema validation

### Phase 2

* SQLGlot analysis
* safe writable columns
* `lnz apply`

### Phase 3

* policy layer
* reference resolution
* `lnz plan`

### Phase 4

* kanban behavior
* markdown/board rendering

### Phase 5

* plugin system
* optional UI

---

# 18. 🧠 Key Insight

> **LenzDB is not a database. It’s a system for editing projections safely.**

---

# 19. 🏁 Summary

**LenzDB** provides:

* text-native data storage
* SQL-defined lenses
* safe, policy-driven writeback
* extensible workflows

**lnz** provides:

* fast, ergonomic CLI
* view/edit/apply workflow
* developer-friendly UX

---

# 20. ✅ V1 Status

The core CLI in this repository is implemented.

Included in v1:

* pip-installable Python package (`lenzdb`)
* zero-config project discovery from `data/`, `schema/`, `lenses/`, `policies/`
* CSV-backed tables
* `lnz view`, `check`, `explain`, `diff`, `plan`, `apply`, and `edit`
* safe updates and inserts for writable lenses
* policy-driven reference resolution and optional related-row creation
* table, csv, json, ndjson, markdown, and html output formats

Out of scope in v1:

* board / kanban rendering
* plugin system
* UI
* JSON writeback

---

# 21. 🚀 Quickstart

Install locally:

```bash
pip install .
```

For development:

```bash
pip install -e .[dev]
```

Run the bundled example project:

```bash
lnz check --project examples/basic
lnz view open_tasks --project examples/basic
lnz explain open_tasks --project examples/basic
lnz view open_tasks --project examples/basic --format csv > /tmp/open_tasks.csv
lnz plan open_tasks /tmp/open_tasks.csv --project examples/basic
lnz apply open_tasks /tmp/open_tasks.csv --project examples/basic
```

Interactive edit flow:

```bash
EDITOR=vim lnz edit open_tasks --project examples/basic
```

---

# 22. 🧪 Development

Run the checks:

```bash
ruff check .
pytest
```
