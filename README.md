# LenzDB

SQL views over CSV files, with safe edits back to text.

LenzDB lets you define SQL views (called *lenses*) over CSV files, inspect them from the CLI, and safely write edits back to the source data. Everything stays as plain text, so Git works naturally.

[Screen recording 2026-04-25 12.05.35 PM.webm](https://github.com/user-attachments/assets/72860259-5e87-41fa-b667-0e80bf75ada8)

---

## 3-Minute Demo

```bash
# 1) Create a project with CSV data
mkdir demo && cd demo

cat > projects.csv <<EOF
id,name
p-1,Core Platform
p-2,Docs Refresh
EOF

cat > tasks.csv <<EOF
id,title,status,project_id
t-1,Ship CLI skeleton,todo,p-1
t-2,Write getting started docs,doing,p-2
t-3,Close phase zero,done,p-1
EOF

# tell LenzDB about the files
lnz add projects.csv
lnz add tasks.csv

# list tables LenzDB knows about
lnz list
```

```bash
# 2) Inspect your data
lnz view tasks
```

```bash
# 3) Define a lens (a saved SQL view)
cat > open_tasks.sql <<EOF
select
  t.id,
  t.title,
  t.status,
  p.name as project_name
from tasks t
join projects p on p.id = t.project_id
where t.status != 'done'
EOF

lnz view open_tasks
```

```bash
# 4) Edit through the view
export LENZDB_EDITOR="code --wait"   # or vim/nano
lnz edit open_tasks
```

Make a change (e.g. update a title or status), save, and close.

```bash
# 5) Changes are written back to the source CSV
cat tasks.csv
```

That’s the core idea:

* Define the *view you want to work with*
* Edit it
* LenzDB safely writes changes back to CSV

---

## Why

* Keep data in simple, diffable CSV files
* Use SQL to define the views people actually want
* Edit projections, not raw tables
* Review changes with normal Git diffs
* Avoid the overhead of a full database

---

## Install

```bash
pipx install lenzdb
# or
pip install lenzdb
```

---

## Core Concepts

* **Tables** → CSV files (`tasks.csv`)
* **Lenses** → SQL views (`open_tasks.sql`)
* **`lnz add`** → register a source table with LenzDB 
* **`lnz view`** → view a table or lens
* **`lnz edit`** → modify a view; write changes back to source rows

---

## Common Commands

```bash
lnz add
lnz list
lnz view tasks
lnz view open_tasks

lnz describe tasks
lnz explain open_tasks

lnz edit open_tasks
lnz edit tasks --filter "status = 'doing'"

lnz view tasks --filter "status = 'todo'"
lnz view tasks --columns id,title
lnz view tasks --order status,-title --limit 10
```

---

## Optional: Explicit edit flow

```bash
lnz view open_tasks --format csv > /tmp/edit.csv
$EDITOR /tmp/edit.csv

lnz diff open_tasks /tmp/edit.csv
lnz plan open_tasks /tmp/edit.csv
lnz apply open_tasks /tmp/edit.csv
```

---

## Project Structure (when you need it)

```text
my-project/
  tasks.csv
  projects.csv
  open_tasks.sql

  .lenzdb/
    schema/
    policies/
```

You can ignore `.lenzdb/` entirely to start.

---

## Notes

* CSV files are the source of truth
* Lenses are just SQL files
* Edits are validated before writeback
* Keep your repo in Git for safety

---

## License

MIT
