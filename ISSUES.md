- [x] `lnz apply` and `lnz edit` apply without a `y/N` prompt.
- [x] Fix duplicated default `lnz view` table output.
- [x] Add MIT license file.

- proposed project-root behavior:
    - project root precedence: `--project`, then `LENZDB_PROJECT_ROOT`, then current working directory
    - project metadata is project-local under `PROJECT_ROOT/.lenzdb`
    - use `LENZDB_CONFIG_DIR` only for user-level config/cache/defaults, not project data
    - do not create `.lenzdb` during read-only commands; create it from explicit setup/write commands

- proposed project layout:
    - root-level CSVs: `PROJECT_ROOT/*.csv`
    - root-level SQL lenses: `PROJECT_ROOT/*.sql`
    - managed/hidden CSVs: `PROJECT_ROOT/.lenzdb/data/*.csv`
    - managed/hidden lenses: `PROJECT_ROOT/.lenzdb/lenses/*.sql`
    - schema: `PROJECT_ROOT/.lenzdb/schema/*.yaml`
    - policies: `PROJECT_ROOT/.lenzdb/policies/*.yaml`
    - optional registration/config: `PROJECT_ROOT/.lenzdb/project.yaml`

- proposed table/lens resolution:
    - every table has a namespace
    - omitted namespace means `main`
    - unqualified SQL table names are allowed only when unambiguous
    - qualified SQL table names use `[namespace].[name]`
    - duplicate fully-qualified names are hard errors
    - duplicate unqualified names require explicit qualification instead of precedence

- proposed subfolder behavior:
    - ignore CSV/SQL files in subfolders by default
    - register subfolder files/folders/globs explicitly in `.lenzdb/project.yaml`
    - registered folders/globs must specify a namespace
    - single registered files may omit namespace and then use `main`
