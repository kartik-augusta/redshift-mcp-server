"""Authorization policy tests for Entra app roles and IdP groups."""

import pytest

from src.auth.oidc_verifier import Principal
from src.auth.policy import AuthzPolicy


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        """
default_effect: deny
rules:
  - roles: [capsa-analysts]
    tools: [query_data, list_tables]
    schemas: [gold_capsaai]
  - roles: [capsa-admins]
    tools: ["*"]
    schemas: ["*"]
  - groups: [legacy-readers]
    tools: [list_schemas]
    schemas: [gold_capsaai]
""",
        encoding="utf-8",
    )
    return AuthzPolicy.from_file(str(path))


def _principal(*, roles=(), groups=()):
    return Principal(
        sub="test-user",
        email="user@example.com",
        roles=list(roles),
        groups=list(groups),
    )


def test_app_role_allows_matching_tool_and_schema(policy):
    result = policy.is_allowed(
        _principal(roles=["capsa-analysts"]), "query_data", "gold_capsaai"
    )
    assert result.allowed


@pytest.mark.parametrize(
    ("tool", "schema"),
    [("drop_table", "gold_capsaai"), ("query_data", "private_schema")],
)
def test_analyst_is_denied_out_of_policy_scope(policy, tool, schema):
    result = policy.is_allowed(_principal(roles=["capsa-analysts"]), tool, schema)
    assert not result.allowed


def test_unknown_role_is_denied(policy):
    result = policy.is_allowed(_principal(roles=["other-role"]), "list_tables")
    assert not result.allowed


def test_admin_wildcards_tools_and_schemas(policy):
    result = policy.is_allowed(
        _principal(roles=["capsa-admins"]), "any_tool", "any_schema"
    )
    assert result.allowed


def test_group_policy_remains_supported(policy):
    result = policy.is_allowed(
        _principal(groups=["legacy-readers"]), "list_schemas", "gold_capsaai"
    )
    assert result.allowed


def test_missing_principal_is_denied(policy):
    result = policy.is_allowed(None, "list_schemas")
    assert not result.allowed
