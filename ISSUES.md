- [x] `lnz apply` and `lnz edit` apply without a `y/N` prompt.
- [x] Fix duplicated default `lnz view` table output.
- [x] Add MIT license file.
- [x] Project root precedence: `--project`, then `LENZDB_PROJECT_ROOT`, then current working directory.
- [x] Project metadata is project-local under `PROJECT_ROOT/.lenzdb`.
- [x] Root-level CSVs are discovered as tables: `PROJECT_ROOT/*.csv`.
- [x] Root-level SQL files are discovered as lenses: `PROJECT_ROOT/*.sql`.
- [x] Managed/hidden CSVs are discovered from `PROJECT_ROOT/.lenzdb/data/*.csv`.
- [x] Managed/hidden lenses are discovered from `PROJECT_ROOT/.lenzdb/lenses/*.sql`.
- [x] Schema is loaded from `PROJECT_ROOT/.lenzdb/schema/*.yaml`.
- [x] Policies are loaded from `PROJECT_ROOT/.lenzdb/policies/*.yaml`.
- [x] Legacy `data/`, `schema/`, `lenses/`, `policies/` discovery is removed.
- [x] Tables and lenses resolve through the implicit `main` namespace.
- [x] `main.[name]` works for table references in SQL and lens names in CLI commands.
- [x] Unqualified names continue to resolve when unambiguous.
- [x] Unknown namespaces are rejected with project errors.
- [x] Duplicate names in the current namespace are hard errors.
- [x] Table and lens names must be distinct.
- [x] CSV/SQL files in subfolders are ignored by default.
- [x] Subfolder files/folders/globs can be registered explicitly in `.lenzdb/project.yaml`.
- [x] Registered folders/globs must specify a namespace.
- [x] Single registered files may omit namespace and then use `main`.
- [x] Registered duplicate short names require explicit qualification instead of precedence.

- [x] On error after `lnz edit`, preserve the edited CSV under `.lenzdb/recovery/`, resume it on the next edit, and clear recovery files after a successful save.

- [x] Be able to `view` CSV data files as well as lenses, including shell completion sources.

- [x] Add `lnz list` for namespaces, tables, lenses, paths, and state, with optional `--check/-c`.

- [x] Treat CSV tables as identity-lens resources for `view`, `explain`, `diff`, `plan`, `apply`, and `edit`.

- [x] Add `view` inline options for SQL, filter, selected columns, order, count rows, limit, offset, and page with `view.page_size` defaulting to 100.

- [x] Fix missing project folder error:
vscode ➜ /workspaces/lenzdb (Arel/initial-project-skeleton) $ lnz view all_tasks
Error: Missing schema directory: /workspaces/lenzdb/.lenzdb/schema

- [x] Fix CSV output/writeback line endings to use `\n`.
-id,title,status,project_id
+id,title,status,project_id^M

- [x] defining PK? default behavior `id` column? first column? compound columns? defined in config somewhere?

- [x] how are ambiguous csv types handled (string/number/null) etc.? handled by duckdb?

- [x] add deletion logic for "edit"? (compare against original query) -- won't do

- [x] Naming is consistent: product/docs `LenzDB`, package/repo `lenzdb`, CLI `lnz`, concept `lens`/`lenses`.

- [x] update README with less disjointed and techy humor -- only add a witty comment if it truly begs for it.

- [x] accept `$LENZDB_EDITOR` with precedence between `--editor` and `$EDITOR`.

- [x] refactor lnz list

- [x] `lnz list` should also show any untracked resources in current dir
- [x] bug: `lnz add [path]` path CLI should be relative to current dir (and be added relative to the project)
- [x] bug: .lenzdb folder should not be required for a project dir to work (to use `lnz list`)

- [x] lnz list should not fail if path is missing (it should list it with "missing" state)

- [x] add --distinct flag to `lnz view` that takes a list of column names and returns the distinct values for those columns

- [x] add multi-column PKs feature -- in a separate feature branch

- [ ] add support for other input/output formats such as JSON, JSONL, tsv?
