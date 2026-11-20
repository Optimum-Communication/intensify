---
name: intensify-enrich
description: Draft the intent (summary, rationale, invariants) of Intensify nodes that still have the placeholder intent, from the code, tests and git history. Use when the user asks to enrich, document or describe the codebase in intensify.yaml, or after `intensify analyze` added new nodes.
---

# Enrich Intensify nodes

`intensify analyze` creates a node for each module, class and function, with the placeholder intent `"TODO: describe intent"`. Your job is to replace those placeholders with what each piece of code is *for* and *why* it exists. You draft the intent; a person reviews it.

Never edit `intensify.yaml` by hand. Write through `intensify node set` (or the MCP `update_intent` tool), which keeps comments and formatting and validates every change.

## 1. Find the work

```sh
intensify node list --unenriched --format json
```

Work **top-down**: the repository root, then modules, then classes, then functions. A module's intent frames everything inside it, and you'll write better function summaries once you know what their module is for. Within a level, start with nodes that many others depend on (`intensify node get <id> --format json` lists incoming edges).

For a large codebase, work in batches of about 20 nodes and check in with the user between batches. Ask which areas matter most if it isn't obvious.

## 2. Gather evidence for each node

```sh
intensify context <id> --include-source
```

That gives you the code, the nodes it depends on and that depend on it, its tests, and its place in the architecture. Then look at history, which is where the *why* lives:

```sh
git log --follow --format='%h %ad %s%n%b' --date=short -- <path>        # a module
git log -L ':<function>:<path>' --format='%h %ad %s%n%b' --date=short | head -200   # a function
```

Also check architecture decision records (`docs/adr/`, `docs/decisions/`, `ADR-*.md`), README sections, docstrings and comments. If `gh` is available, pull request descriptions for the commits that introduced the code are often the best source of rationale.

## 3. Write the intent

```sh
intensify node set <id> --author ai \
  --summary "Issue a full or partial refund against a settled charge." \
  --rationale "Idempotent because the payment provider retries webhooks; see PR #412."
```

- **summary:** what the node does, for whom, in one or two sentences. Describe responsibility, not implementation. Write "Issue a refund against a settled charge", not "Calls the PSP client and saves a Refund row". Don't start with "This function…".
- **rationale:** why it exists, or why it's built this way: constraints, trade-offs, history, the incident it prevents. Only write what the evidence supports, and cite it (a commit, PR, ADR or issue) when you can. If you can't find a reason, leave the rationale out rather than guess.
- **author ai:** always set this. It tells people the intent is a draft to review.

Don't touch nodes whose intent a person already wrote (anything other than the placeholder) unless the user asks.

## 4. Record invariants you find

When the evidence shows a rule the code must keep, record it. Examples are a test that asserts it, a comment such as "must never…", an ADR, or a bug fix that added a guard.

```sh
intensify node set <id> --invariant 'idempotent=Issuing the same refund twice creates one refund.'
```

If the rule is about **dependencies**, add a machine-checkable form so `intensify drift` enforces it. Examples: "the domain layer never imports the web layer", "only the repository writes to the database".

```sh
intensify node set payments.domain --json '{"governance": {"invariants": [
  {"id": "no-web", "text": "Domain code never depends on the web layer.",
   "rule": {"forbid_edge": {"to": "payments.web.*"}}}]}}'
```

The rule types are `forbid_edge` and `require_edge` (`{type?, to: glob}`), and `allowed_dependents` (`{type?, from: [globs]}`). Run `intensify drift` afterwards. If a new rule is already violated, tell the user instead of weakening the rule.

## 5. Finish

1. Run `intensify validate`.
2. Run `intensify node list --unenriched` to see what's left.
3. Summarize for the user: how many nodes you described, which intents you're least sure of, and any invariants you added. People should review `author: ai` intents, and change `author` to `human` once confirmed.

Don't change any code while enriching.
