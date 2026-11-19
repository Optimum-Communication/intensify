from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from intensify.cli import app
from intensify.spec import load_and_validate
from tests.unit.agents.conftest import implement_member_discount

runner = CliRunner()


def invoke(repo: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(
        app,
        [*args[:2], *args[2:], "--spec", str(repo)]
        if args[0] == "node"
        else [*args, "--spec", str(repo)],
    )
    return result.exit_code, result.output


def test_node_get_list_set_add(repo: Path) -> None:
    code, out = invoke(repo, "node", "get", "shop.pricing")
    assert code == 0
    assert out.startswith("id: shop.pricing\n")

    code, out = invoke(repo, "node", "list", "member", "discount")
    assert code == 0
    assert out.splitlines()[0].startswith("shop.pricing.member_discount  function  planned")

    code, out = invoke(repo, "node", "list", "--unenriched", "--format", "json")
    assert all(item["summary"] == "TODO: describe intent" for item in json.loads(out))

    code, _ = invoke(
        repo,
        "node",
        "set",
        "shop.pricing.subtotal",
        "--summary",
        "Sum of line items.",
        "--author",
        "ai",
        "--tag",
        "domain:pricing",
        "--invariant",
        "non-negative=Never returns a negative total.",
    )
    assert code == 0
    node = next(
        e.node for e in load_and_validate(repo).nodes if e.node.id == "shop.pricing.subtotal"
    )
    assert node.intent.summary == "Sum of line items."
    assert node.intent.author == "ai"
    assert node.governance is not None
    assert node.governance.tags == ["domain:pricing"]
    assert [i.id for i in node.governance.invariants or []] == ["non-negative"]

    code, _ = invoke(
        repo,
        "node",
        "add",
        "shop.loyalty.award",
        "--kind",
        "function",
        "--parent",
        "shop.loyalty",
        "--summary",
        "Award points.",
    )
    assert code == 0
    code, out = invoke(
        repo, "node", "add", "shop.loyalty.award", "--kind", "function", "--summary", "Again."
    )
    assert code == 1
    assert "already exists" in out


def test_node_errors(repo: Path) -> None:
    assert invoke(repo, "node", "get", "nope")[0] == 1
    assert invoke(repo, "node", "set", "shop.pricing")[0] == 2  # nothing to change
    assert invoke(repo, "node", "set", "shop.pricing", "--json", "[1]")[0] == 2
    code, out = invoke(repo, "node", "set", "shop.pricing", "--json", '{"kind": "widget"}')
    assert code == 1
    assert "would make the spec invalid" in out


def test_context_command(repo: Path) -> None:
    code, out = invoke(repo, "context", "shop.pricing.apply_discount")
    assert code == 0
    assert out.startswith("# `shop.pricing.apply_discount` (function, active)")
    code, out = invoke(repo, "context", "shop.pricing.apply_discount", "--format", "json")
    assert json.loads(out)["target"]["id"] == "shop.pricing.apply_discount"
    assert invoke(repo, "context", "nope")[0] == 1


def test_verify_command(repo: Path) -> None:
    code, out = invoke(repo, "verify", "shop.pricing.member_discount")
    assert code == 1
    assert "✗ code exists" in out
    implement_member_discount(repo)
    code, out = invoke(repo, "verify", "shop.pricing.member_discount")
    assert code == 0, out
    assert "verified and marked active (was planned)" in out
    assert invoke(repo, "verify", "nope")[0] == 2


def test_skills_install(repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "skills"
    result = runner.invoke(app, ["skills", "install", "--dir", str(target)])
    assert result.exit_code == 0
    skill = target / "intensify-enrich" / "SKILL.md"
    assert skill.read_text().startswith("---\nname: intensify-enrich\n")
    skill.write_text("local change")
    result = runner.invoke(app, ["skills", "install", "--dir", str(target)])
    assert "skipped" in result.output
    assert skill.read_text() == "local change"
    runner.invoke(app, ["skills", "install", "--dir", str(target), "--force"])
    assert skill.read_text().startswith("---\nname: intensify-enrich\n")

    cursor = tmp_path / "rules"
    runner.invoke(app, ["skills", "install", "--target", "cursor", "--dir", str(cursor)])
    rule = (cursor / "intensify-implement-node.mdc").read_text()
    assert rule.startswith("---\ndescription: Implement a planned Intensify node")
    assert "alwaysApply: false\n---\n\n# Implement an Intensify node" in rule
