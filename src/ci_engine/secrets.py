import os


def get_secret(name: str) -> str:
    """Return the latest version of a GCP Secret Manager secret.

    Falls back to the env var formed by uppercasing and replacing hyphens with
    underscores (e.g. anthropic-key -> ANTHROPIC_KEY) so tests run without
    network access.
    """
    env_var = name.upper().replace("-", "_")
    env_value = os.environ.get(env_var)
    if env_value is not None:
        return env_value

    from google.cloud import secretmanager  # noqa: PLC0415

    from ci_engine.config import get  # noqa: PLC0415

    project_id = get("project.gcp_project_id")
    client = secretmanager.SecretManagerServiceClient()
    secret_name = f"projects/{project_id}/secrets/{name}/versions/latest"
    response = client.access_secret_version(name=secret_name)
    return response.payload.data.decode("utf-8")
