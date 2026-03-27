"""Tests for knowledge boundaries -- sharing rules and secret scanning."""

import pytest

from devteam.knowledge.boundaries import (
    SharingScope,
    SecretDetectedError,
    apply_scope_filter,
    determine_sharing_scope,
    scan_for_secrets,
)


class TestSharingScope:
    def test_process_knowledge_is_shared(self):
        scope = determine_sharing_scope(
            tags=["process"],
            content="CodeRabbit comments must be resolved before merge",
        )
        assert scope == SharingScope.SHARED

    def test_platform_knowledge_is_shared(self):
        scope = determine_sharing_scope(
            tags=["shared", "cloud"],
            content="Fly.io requires HEALTHCHECK in Dockerfile",
        )
        assert scope == SharingScope.SHARED

    def test_project_code_knowledge_is_project_scoped(self):
        scope = determine_sharing_scope(
            tags=["project", "backend"],
            content="This project uses Drizzle ORM",
        )
        assert scope == SharingScope.PROJECT

    def test_explicit_shared_tag_overrides(self):
        scope = determine_sharing_scope(
            tags=["shared", "backend"],
            content="All backends should use structured logging",
        )
        assert scope == SharingScope.SHARED

    def test_no_tags_defaults_to_project(self):
        scope = determine_sharing_scope(
            tags=[],
            content="Something without tags",
        )
        assert scope == SharingScope.PROJECT


class TestSecretScanning:
    def test_detects_aws_access_key(self):
        with pytest.raises(SecretDetectedError, match="AWS"):
            scan_for_secrets("Use key AKIAIOSFODNN7EXAMPLE for access")

    def test_detects_generic_api_key_assignment(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets('api_key = "sk-1234567890abcdef"')

    def test_detects_password_assignment(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets('password = "hunter2"')

    def test_detects_bearer_token(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoiMSJ9.abc123")

    def test_detects_private_key_block(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets("-----BEGIN RSA PRIVATE KEY-----")

    def test_detects_connection_string_with_password(self):
        with pytest.raises(SecretDetectedError):
            scan_for_secrets("postgres://user:s3cret@localhost:5432/db")

    def test_detects_connection_string_with_dollar_in_password(self):
        """Connection string passwords containing $ must still be detected."""
        with pytest.raises(SecretDetectedError):
            scan_for_secrets("postgres://user:pa$$@localhost:5432/db")

    def test_allows_safe_content(self):
        # Should not raise
        scan_for_secrets("Use Drizzle ORM for database access")
        scan_for_secrets("Fly.io requires HEALTHCHECK in Dockerfile")
        scan_for_secrets("Run pytest with the -v flag for verbose output")

    def test_detects_password_with_dollar_signs(self):
        """Literal $ in passwords should NOT be skipped as a placeholder."""
        with pytest.raises(SecretDetectedError):
            scan_for_secrets('password = "pa$$word123"')

    def test_allows_placeholder_patterns(self):
        # Placeholder patterns should not trigger
        scan_for_secrets("Set API_KEY=<your-key-here>")
        scan_for_secrets("password: ${DB_PASSWORD}")
        scan_for_secrets("Use $ENV_VAR for the secret")


class TestScopeFilter:
    def test_shared_scope_filter(self):
        f = apply_scope_filter("shared", project=None, role=None)
        assert f["sharing"] == "shared"
        assert "project" not in f
        assert "role" not in f

    def test_project_scope_includes_shared(self):
        f = apply_scope_filter("project", project="myapp", role=None)
        assert f["project"] == "myapp"
        # project scope should also include shared entries -- the store layer
        # handles this via (sharing="shared" OR project=$project) when project
        # is set, so the filter must NOT restrict sharing to "project" only.
        assert "sharing" not in f, (
            "project scope filter must not set sharing; "
            "the store includes shared entries automatically when project is set"
        )

    def test_role_scope_filter(self):
        f = apply_scope_filter("my_role", project=None, role="backend_engineer")
        assert f["role"] == "backend_engineer"

    def test_all_scope_with_project(self):
        f = apply_scope_filter("all", project="myapp", role="backend_engineer")
        assert f["project"] == "myapp"
        assert f["role"] == "backend_engineer"
