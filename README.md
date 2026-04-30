# LenzDB

SQL views over CSV files, with safe edits back to text.

LenzDB is a small, Git-native data tool for project data that belongs in plain
files. You define SQL views, called lenses, over CSV tables; inspect them from
the CLI; export rows for editing; and safely write supported changes back to the
source CSV files. Your data stays text, so Git can track ordinary, reviewable
diffs.

LenzDB is installed as the Python package `lenzdb` and used from the command
line as `lnz`. A lens is the saved SQL view concept inside a LenzDB project.

[Screen recording 2026-04-25 12.05.35 PM.webm](https://github.com/user-attachments/assets/72860259-5e87-41fa-b667-0e80bf75ada8)

## Why

- Keep project data in boring, diffable CSV files.
- Use SQL to shape the views people actually want to inspect and edit.
- Put schema, relationships, and write policies next to the data.
- Review data changes with normal Git diffs.
- Edit a projection and write safe changes back to the source rows.

LenzDB is for small project data, operational notes, lightweight catalogs,
curated datasets, and other places where a full database is more ceremony than
the job needs.

## Install

Install via [pipx](https://pipx.pypa.io/stable/):

```bash
pipx install lenzdb
```

Or via pip:

```bash
pip install lenzdb
```

## Getting started

Example:

```bash
# Show usage
lnz --help

cd examples/basic
ls  # all_tasks.sql  open_tasks.sql  projects.csv  tasks.csv

lnz list
lnz view projects
lnz view all_tasks
lnz view all_tasks --distinct project_name
lnz view open_tasks

export LENZDB_EDITOR="code --wait"  # or "vim" or any editor

# make changes in a *view* (or "lens") of the data
lnz edit open_tasks

# upon saving, any changes will be reflected in the source CSV file (tasks.csv)
cat tasks.csv
```

## Configuration

Most commands locate a project in this order:

1. `--project PATH`
2. `$LENZDB_PROJECT_ROOT`
3. The current working directory

`lnz edit` chooses an editor in this order:

1. `--editor COMMAND`
2. `$LENZDB_EDITOR`
3. `$EDITOR`

Example:

```bash
export LENZDB_PROJECT_ROOT=examples/basic
export LENZDB_EDITOR=vim
lnz list
lnz edit open_tasks
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
Schema and policies live under `.lenzdb/`. CSV is currently the only supported
persisted table data format.

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

view:
  page_size: 100
```

Folders and globs must specify a namespace. Single registered files may omit one
and will use `main`. `view.page_size` controls `lnz view --page`; when omitted,
it defaults to `100`.

## Namespaces

Every table and lens has a namespace. The default is `main`.

```sql
select * from tasks;
select * from main.tasks;
select * from acme.tasks;
```

Unqualified names work only when they are unambiguous. If both `main.tasks` and
`acme.tasks` exist, `tasks` is rejected and LenzDB asks you to be precise.

## Usage

Run the bundled example:

```bash
lnz check --project examples/basic
lnz view open_tasks --project examples/basic
lnz view tasks --project examples/basic
lnz view tasks --project examples/basic --columns id,title,status
lnz view tasks --project examples/basic --filter "status = 'todo'"
lnz view tasks --project examples/basic --order status,-title --limit 20
lnz view tasks --project examples/basic --page 2
lnz view tasks --project examples/basic --count
lnz view open_tasks --project examples/basic --sql "select title from resource where status = 'doing'"
lnz list --project examples/basic
lnz list --project examples/basic --check
lnz explain open_tasks --project examples/basic
```

`view`, `explain`, `diff`, `plan`, `apply`, and `edit` accept either a lens or
a table name. CSV tables behave like they have an implicit identity lens, so
`tasks.csv` is roughly `select * from tasks`. Table and lens names must be
distinct.

`lnz view` has convenience flags for quick inspection. `--columns` chooses a
comma-separated set of output columns, `--filter` accepts a SQL `WHERE` fragment,
and `--order` accepts comma-separated columns with a `-` prefix for descending
sorts. `--page` uses the project page size. `--limit` and `--offset` control
the returned row range. `--sql` runs a SQL query where the selected table or
lens is available as `resource`.

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
LENZDB_EDITOR=vim lnz edit open_tasks --project examples/basic
```

`apply` and `edit` do not ask a final `y/N` question. Keep project files under
version control so data changes can be reviewed and reverted normally.

If `edit` fails after the editor opens, LenzDB preserves the edited CSV in
`.lenzdb/recovery/`. The next `lnz edit RESOURCE` resumes the newest recovery
file for that resource; pass `--discard-recovery` to start from current data
instead. A successful edit clears the recovery files for that resource.

Writeback uses CSV snapshots only: `lnz edit` opens a CSV file, and `lnz diff`,
`lnz plan`, and `lnz apply` read edited CSV files.

## Output Formats

`lnz view` and `lnz list` support these output formats:

```bash
lnz view open_tasks --format table
lnz view open_tasks --format markdown
lnz view open_tasks --format csv
lnz view open_tasks --format tsv
lnz view open_tasks --format json
lnz view open_tasks --format ndjson
lnz view open_tasks --format yaml
lnz view open_tasks --format html
```

These are output formats only; they do not change persisted table files or edit
snapshot formats.

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

For composite primary keys, use a list:

```yaml
table: memberships
primary_key: [org_id, user_id]
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

MIT.
