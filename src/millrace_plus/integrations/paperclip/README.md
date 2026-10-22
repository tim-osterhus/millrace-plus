# Local Paperclip external adapter

Register this directory with Paperclip's local plugin loader and select
`millrace_graph`. Configure `bridgeConfigFile` with an absolute path to the
strict `millrace-paperclip-bridge-config/v1` JSON file. Optional
`timeoutSeconds` must cover wait + cleanup + 10 seconds.

The selected contract is Paperclip `043f2514e67c50320433a0013fb228a75a51325c`,
adapter-utils 0.3.1, Node >=24.11.0. No npm install or publication is needed.
`createServerAdapter()` exposes `type`, `execute(ctx)` and
`testEnvironment(ctx)`. It spawns the explicit helper as an argv array, projects
only server-owned assignment fields, and registers public cancellation before
dispatch. It reports structured results without writing to issues.

See the installed Plus `docs/paperclip.md` for configuration, exact replay,
credential rotation, finite deadlines, result validation and qualification
limits. This independently authored integration bundles no upstream Paperclip
implementation. Paperclip's selected root license is MIT, copyright 2025
Paperclip AI; retained research records preserve its exact source/license pin.
