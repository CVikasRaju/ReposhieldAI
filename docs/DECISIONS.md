# Decisions

Deviations from the specification pack and the reasoning behind each. Newest last.

| Date | Decision | Reason |
|---|---|---|
| 2026-10-18 | The specification files were moved from `docs/docs/` to `docs/` and the empty nested directory removed. | Every document and `AGENTS.md` refers to `docs/01-...` through `docs/09-...`; the extra nesting was a packaging artifact. |
| 2026-10-18 | The MCP SDK is pinned to `mcp>=1.2,<2.0`. | The specification requires FastMCP. The installed 2.3 SDK renamed FastMCP to `MCPServer`; pinning 1.x keeps the documented API. Verified against the installed 1.30.0 source. |
| 2026-10-18 | No Faker dependency was added. Seeded customer data is produced with `random.Random(42)` and small local generators. | Faker is not in the AGENTS.md dependency list. The stdlib generator is deterministic and satisfies the "Faker seed 42" intent without a new dependency. |
| 2026-10-18 | The seeded workspace uses the documented *fake* example secrets verbatim and is gitignored. | `docs/08` specifies the exact `.env` contents and marks all values fake. The workspace is generated at seed time and never committed, so GitHub push protection cannot flag the repository. |
| 2026-10-18 | `ALLOWED` is added as a display-only reason code with the template "The action is within what the agent was asked to do." | Every event needs a plain-language sentence, but `docs/03` section 7 has no generic allow template. The code is never produced as a policy rule. |
