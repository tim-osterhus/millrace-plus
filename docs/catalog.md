# Local workflow catalog

`millrace-plus catalog list`, `init SELECTOR DESTINATION`, and `copy SOURCE DESTINATION` operate on ordinary graph-v3 folders. Add `--catalog-home PATH` to any of these commands to select a local root for that command. Without an override, Plus uses your saved catalog root, then falls back to `~/.local/share/millrace-plus/workflows/`. Listing a missing root is empty; create that directory before copying a new entry into it.

Save a custom catalog root once for future CLI calls and Python API calls:

```bash
mkdir -p ~/my-workflows
millrace-plus catalog home set ~/my-workflows
millrace-plus catalog home show
millrace-plus catalog home reset
```

`set` requires an existing directory and saves its absolute resolved path. `show` reports the effective root, whether it came from the saved setting or fallback, and the settings file. `reset` removes only the saved setting; your workflow folders remain intact. The setting lives in `~/.config/millrace-plus/catalog.json`, outside the installed package, so package upgrades preserve it. Catalog list/init/copy never rewrite it. An explicit `--catalog-home` takes precedence without changing the saved default. Invalid settings produce `invalid_catalog_settings`; use `home reset` or `home set PATH` to repair them.

Selectors are always qualified: `bundled:document-review`, `bundled:software-change`, or `local:NAME`. Names match `[A-Za-z][A-Za-z0-9_-]{0,63}`. Equal bundled/local names are separate entries. Listing is deterministic. An invalid linked or nonregular candidate fails the whole list with `invalid_template`, rather than returning partial results.

Init creates a new folder outside known catalog/bundled roots. Copy accepts a filesystem source and can create a workspace or a new direct child of the effective catalog. Parents must exist. No operation overwrites or merges an existing destination, including a dangling symlink. Copies use new regular files, preserve executable bits, and remain independent after source edits/removal. Linked or special source entries are refused. Conventional `__pycache__` folders are skipped; a declared control asset inside one is refused. Other files are copied normally.

The catalog checks strict JSON and the `millrace-router/v3` format marker only. It rejects duplicate keys and nonfinite numbers. Core graph and selected-adapter validation occur at registration/validation; live native prerequisites occur at invocation. List/init/copy import no Core or native adapter and probe no credentials, executables or versions. Local command/other graphs retain their authored bindings.

The Python API is `millrace_plus.catalog.list_templates`, `init_template` and `copy_workflow`, each with keyword-only `catalog_home: Path | None = None`. `catalog_home_status()`, `set_catalog_home(Path)`, and `reset_catalog_home()` manage the saved default. Errors raise `CatalogError` with `code` and message. CLI errors emit stderr JSON and exit 2; successful commands emit JSON. A missing destination parent produces `destination_parent_missing` with an instruction to create it first.

## Router ownership checks

For router commands the override is an exact optional prefix: `millrace-plus router --catalog-home PATH CORE_ARGUMENTS` (or `--catalog-home=PATH`). Use it at most once before all Core arguments. Bundled, fallback, saved and explicit catalog roots remain protected even with an override. Both workflow and project roots must be independent of all those roots.

The Plus facade and both router CLI forms check create/envelopes/replay and dispatch-capable mutations. Unsafe instances created directly through Core can still be inspected and have holds released. Run/plane pause/cancel must target their sole nonterminal run; reconcile additionally requires that same run paused. Wrong-target or nonpaused reconciliation refuses before Core state mutation. The sixteen guarded facade functions accept trailing keyword-only `catalog_home`; all other controls keep their Core signatures without the resolver argument.

This is a client ownership check, not a security boundary. Public lookup and Core execution are not atomic. Pre-existing queued controls or concurrent direct-Core mutators/controllers on unsafe instances are unsupported; no universal dispatch barrier is claimed. Core remains catalog-agnostic. Use independent copies for supported operation.
