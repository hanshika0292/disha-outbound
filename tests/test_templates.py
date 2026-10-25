"""Template loading + placeholder replacement."""

from __future__ import annotations

import json
from pathlib import Path

from disha.tenant.provisioner import _apply_template_vars, _load_template


def test_apply_template_vars_replaces_nested_strings() -> None:
    inp = {
        "prompt": "Hi, I'm {{SENDER_NAME}}",
        "nested": {"title": "{{SENDER_TITLE}}", "list": ["from {{SENDER_CITY}}", 42]},
    }
    out = _apply_template_vars(
        inp, {"SENDER_NAME": "Alice", "SENDER_TITLE": "UX Consultant", "SENDER_CITY": "Mumbai"}
    )
    blob = json.dumps(out)
    assert "{{" not in blob
    assert "Alice" in blob and "UX Consultant" in blob and "Mumbai" in blob
    # numeric leaves untouched
    assert out["nested"]["list"][1] == 42


def test_apply_template_vars_with_missing_key_leaves_placeholder() -> None:
    """Defensive: only the keys explicitly passed are substituted."""
    out = _apply_template_vars("Hi {{SENDER_NAME}}, from {{OTHER}}", {"SENDER_NAME": "A"})
    assert out == "Hi A, from {{OTHER}}"


def test_ai_consulting_template_loads_and_has_required_keys() -> None:
    tpl = _load_template("ai_consulting")
    for key in ("vertical", "icp_rubric", "prompts", "voice", "cadence_in", "cadence_us", "geo"):
        assert key in tpl
    assert tpl["vertical"] == "ai_consulting"
    assert len(tpl["prompts"]) >= 8


def test_ux_design_template_loads_and_has_required_keys() -> None:
    tpl = _load_template("ux_design")
    for key in ("vertical", "icp_rubric", "prompts", "voice", "cadence_in", "cadence_us", "geo"):
        assert key in tpl
    assert tpl["vertical"] == "ux_design"
    assert len(tpl["prompts"]) >= 8


def test_template_directory_only_lists_known_files() -> None:
    """If we drop a new template here, this test prompts us to register it
    (or makes the failure explicit so we update the test)."""
    template_dir = Path(__file__).resolve().parent.parent / "src" / "disha" / "seeds" / "templates"
    known = {"ai_consulting", "ux_design"}
    actual = {p.stem for p in template_dir.glob("*.json")}
    assert actual == known
