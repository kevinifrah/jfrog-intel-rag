from ci_engine.skills import compose, load_skill


def test_load_skill_non_empty():
    body = load_skill("grounding-contract")
    assert body, "load_skill returned empty string"


def test_load_skill_no_frontmatter():
    body = load_skill("grounding-contract")
    assert not body.startswith("---"), "frontmatter was not stripped"


def test_compose_joins_skills():
    result = compose("grounding-contract", "grounding-contract")
    assert "\n\n" in result
