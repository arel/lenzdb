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

- [ ] how are ambiguous csv types handled (string/number/null) etc.? handled by duckdb?

- [ ] on error after edit, can the broken file be preserved somewhere and re-editable? (not needed for apply?)

- [ ] add deletion logic for "edit"? (compare against original query)

- [x] Be able to `view` CSV data files as well as lenses, including shell completion sources.

- [x] Add `lnz list` for namespaces, tables, lenses, and paths, with optional `--with-status/-s`.

- [x] Treat CSV tables as identity-lens resources for `view`, `explain`, `diff`, `plan`, `apply`, and `edit`.

- [ ] create/edit a lense from the CLI?

- [ ] add to `view` inline options for sql / filter / order / count rows / limit /offset / page (applies limit/offset with page size in project.yaml default 100)?

- [x] Fix missing project folder error:
vscode ➜ /workspaces/lenz-db (Arel/initial-project-skeleton) $ lnz view all_tasks
Error: Missing schema directory: /workspaces/lenz-db/.lenzdb/schema

- [x] Fix CSV output/writeback line endings to use `\n`.
-id,title,status,project_id
+id,title,status,project_id^M

- [ ] update README with less disjointed humor (stick to one universal less nerdy theme)
