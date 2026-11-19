---
name: intensify-implement-node
description: Implement a planned Intensify node (or change an existing one) using its intent, contract, invariants and surrounding architecture from intensify.yaml, then verify it. Use when the user asks to build, implement or work on a node, a planned feature, or something listed in intensify.yaml.
---

# Implement an Intensify node

`intensify.yaml` describes what the code should do and why, including planned nodes that don't exist yet. To implement a node, read its context, build exactly what it describes, and prove it with `intensify verify`.

Never edit `intensify.yaml` by hand. Use `intensify node` commands (or the MCP tools), which keep the file's formatting and validate every change.

## 1. Pick the node

If the user gave an ID, use it. Otherwise find it:

```sh
intensify node list --state planned          # work waiting to be done
intensify node list refund webhook           # search by words
```

If several nodes match, ask which one.

## 2. Read its context, all of it

```sh
intensify context <id> --include-source
```

This tells you:

- **Intent and contract:** what to build. The contract (inputs, outputs, error modes) is the agreed interface. If there's no contract, design a small one and record it before coding (step 3).
- **Invariants:** rules that must hold, including those inherited from parent modules. Rules marked "checked" are enforced by `intensify drift` and `verify`.
- **Depends on / Used by:** the code around it. Use the existing nodes listed rather than writing new versions of them.
- **Future capabilities:** planned work nearby. Make your code easy to extend in those directions, but **don't build them** and don't add speculative abstractions for them.
- **Location:** where the code goes. For planned nodes, use the suggested location next to the parent.

If a "blocks this node (prerequisite)" item isn't active yet, tell the user before starting. It may need to be built first.

## 3. Mark it in progress, and record a contract if needed

```sh
intensify node set <id> --state in_progress
intensify node set <id> --json '{"contract": {"inputs": [{"name": "charge_id", "type": "str"}], "outputs": [{"type": "Refund"}]}}'
```

## 4. Implement it

- Put the code where the location says, with the node's name.
- Match the contract exactly: parameter names, order, types, and which parameters are optional.
- Keep every invariant.
- Write tests that import and call the node directly. `intensify analyze` links tests to the code they call, and `verify` requires at least one test.
- Follow the conventions of the surrounding code.

## 5. Record and verify

```sh
intensify analyze
intensify verify <id>
```

`analyze` records the new code in the spec. If it added nodes for helper code you wrote, describe them:

```sh
intensify node set <helper-id> --author ai --summary "..."
```

`verify` checks that the code exists, matches the contract, keeps the invariants and has tests. It also runs `config.verify.test_command` if one is set. When everything passes, it marks the node `active`. If a check fails, fix the code and run `verify` again.

If you conclude that the contract itself is wrong, don't quietly change it to match your code. Explain to the user why, and change it only with their agreement.

## 6. Finish

Run `intensify drift`. It should report no errors. Then summarize for the user:

- what you built and where;
- the tests you added;
- any helper nodes you added;
- anything you noticed about the planned work nearby.
