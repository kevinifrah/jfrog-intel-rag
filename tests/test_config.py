from ci_engine.config import get


def test_gcp_project_id():
    assert get("project.gcp_project_id") == "jfrog-intel-rag"


def test_embedding_dimensions():
    assert get("embedding.dimensions") == 1536


def test_missing_key_returns_default():
    assert get("nonexistent.key") is None
    assert get("nonexistent.key", 42) == 42
