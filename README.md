# LenzDB

LenzDB is a tiny, Git-native data tool for people who like CSV files but would
prefer not to experience them one VLOOKUP at a time.

It lets you define SQL views, called lenses, over plain CSV data. You can inspect
those lenses, edit exported rows, and safely write supported changes back to the
source CSV files. The files stay text. Git keeps doing Git things. Nobody has to
pretend a spreadsheet is a database, which is restful in its own small way.

## Why

- Keep data in boring, diffable CSV files.
- Use SQL for the views people actually want to work with.
- Put schema, relationships, and write policies next to the data.
- Review changes with normal Git diffs.
- Edit a projection and write safe changes back to the source rows.

LenzDB is not trying to replace Postgres. Postgres is busy and has a family.
This is for small project data, operational notes, lightweight catalogs,
curated datasets, and other places where a real database is too much ceremony
but raw CSVs are a little too haunted.

## Install

From this repository:

```bash
pip install .
```

For development:

```bash
pip install -e ".[dev]"
```

The CLI command is:

```bash
lnz --help
```

## Project Layout

A minimal project looks like this:

```text
my-project/
  tasks.csv
  projects.csv
  open_tasks.sql

  .lenzdb/
    schema/
      tasks.yaml
      projects.yaml

    policies/
      open_tasks.yaml
```

Root-level `*.csv` files are tables. Root-level `*.sql` files are lenses.
Schema and policies live under `.lenzdb/`.

LenzDB also reads managed files from:

```text
.lenzdb/data/*.csv
.lenzdb/lenses/*.sql
```

Subfolders are ignored unless you register them in `.lenzdb/project.yaml`:

```yaml
tables:
  - path: clients/acme/*.csv
    namespace: acme

lenses:
  - path: reports/acme/*.sql
    namespace: acme
```

Folders and globs must specify a namespace. Single registered files may omit one
and will use `main`.

## Namespaces

Every table and lens has a namespace. The default is `main`.

```sql
select * from tasks;
select * from main.tasks;
select * from acme.tasks;
```

Unqualified names work only when they are unambiguous. If both `main.tasks` and
`acme.tasks` exist, `tasks` is rejected and LenzDB asks you to be precise. A
small price to pay for not discovering later that you edited the wrong Tuesday.

## Usage

Run the bundled example:

```bash
lnz check --project examples/basic
lnz view open_tasks --project examples/basic
lnz view tasks --project examples/basic
lnz list --project examples/basic
lnz list --project examples/basic --with-status
lnz explain open_tasks --project examples/basic
```

`view`, `explain`, `diff`, `plan`, `apply`, and `edit` accept either a lens or
a table name. CSV tables behave like they have an implicit identity lens, so
`tasks.csv` is roughly `select * from tasks`. Table and lens names must be
distinct; guessing is how a Tuesday becomes paperwork.

Export a lens, edit it, and preview the writeback plan:

```bash
lnz view open_tasks --project examples/basic --format csv > /tmp/open_tasks.csv
$EDITOR /tmp/open_tasks.csv
lnz diff open_tasks /tmp/open_tasks.csv --project examples/basic
lnz plan open_tasks /tmp/open_tasks.csv --project examples/basic
```

The same flow works for raw CSV tables:

```bash
lnz view tasks --project examples/basic --format csv > /tmp/tasks.csv
$EDITOR /tmp/tasks.csv
lnz plan tasks /tmp/tasks.csv --project examples/basic
lnz apply tasks /tmp/tasks.csv --project examples/basic
```

Apply the changes:

```bash
lnz apply open_tasks /tmp/open_tasks.csv --project examples/basic
```

Or let LenzDB open the editor for you:

```bash
EDITOR=vim lnz edit open_tasks --project examples/basic
```

`apply` and `edit` do not ask a final `y/N` question. The assumption is that
your project files are versioned by Git, which is already a better adult
supervision system than a prompt you answer from muscle memory.

## Output Formats

```bash
lnz view open_tasks --format table
lnz view open_tasks --format markdown
lnz view open_tasks --format csv
lnz view open_tasks --format json
lnz view open_tasks --format ndjson
lnz view open_tasks --format html
```

## Schema Sketch

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

## Lens Sketch

```sql
select
  t.id,
  t.title,
  t.status,
  p.name as project_name
from tasks as t
join projects as p on p.id = t.project_id
where t.status != 'done'
order by t.id
```

## Policy Sketch

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

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
```

## License

MIT. Do something useful with it. Preferably something with fewer tabs named
`final_final_really.csv`.
