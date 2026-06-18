from ci_engine.config import get, tracked_companies


def test_gcp_project_id():
    assert get("project.gcp_project_id") == "jfrog-intel-rag"


def test_embedding_dimensions():
    assert get("embedding.dimensions") == 1536


def test_database_iam_user_is_service_account_database_username():
    assert get("database.iam_user") == "ci-engine-sa@jfrog-intel-rag.iam"
    assert (
        get("database.impersonate_service_account")
        == "ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com"
    )


def test_missing_key_returns_default():
    assert get("nonexistent.key") is None
    assert get("nonexistent.key", 42) == 42


def test_tracked_companies():
    companies = tracked_companies()

    assert "JFrog" in companies
    assert "Snyk" in companies
