# Intensify AI skills

| Skill | Use it to |
|---|---|
| [`enrich`](enrich/SKILL.md) | Draft the intent of nodes that `intensify analyze` added, from code, tests and git history. |
| [`implement-node`](implement-node/SKILL.md) | Build a planned node from its intent, contract and context, then `intensify verify` it. |

Both skills drive the `intensify` CLI, so they work with any agent that can run shell commands. With the [MCP server](../docs/agents.md) connected, agents can use the equivalent MCP tools instead.

## Install

```sh
intensify skills install                  # Claude Code: .claude/skills/
intensify skills install --target cursor  # Cursor: .cursor/rules/
intensify skills install --target plain --dir prompts/   # plain Markdown, for any other agent
```

Each `SKILL.md` is plain Markdown with a short front-matter header, so you can also paste it into any agent as a prompt.
