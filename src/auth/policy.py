"""
Role & group-based authorization policy engine.

Loads a YAML policy file that maps IdP **app roles** and/or **group**
identifiers to allowed MCP tools and database schemas.  Default effect is
**deny** — if no rule matches, access is refused.

Policy rules may use ``roles`` (matched against the token's ``roles`` claim,
typical for Entra ID App Roles), ``groups`` (matched against the token's
group-membership claim), or both.  When both are present on the same rule the
caller must satisfy **at least one** of the two (logical OR).

Policy file format::

    default_effect: deny          # only "deny" is supported today

    rules:
      # Entra ID App Role example
      - roles: ["capsa-analysts"]
        tools:
          - list_schemas
          - query_data
        schemas:
          - gold_capsaai

      # Legacy group-based example
      - groups: ["capsa-iq-analysts"]
        tools:
          - list_schemas
        schemas:
          - gold_capsaai

      # Admins via app role
      - roles: ["capsa-admins"]
        tools: ["*"]              # wildcard — all tools
        schemas: ["*"]            # wildcard — all configured schemas
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import yaml

from src.auth.oidc_verifier import Principal

logger = logging.getLogger("redshift_mcp.auth.policy")


@dataclass(frozen=True)
class AuthzDecision:
    """Result of an authorization check."""
    allowed: bool
    reason: str
    matched_rule: str | None = None


@dataclass(frozen=True)
class _PolicyRule:
    """One parsed rule from the policy file."""
    name: str
    groups: frozenset[str]      # from ``groups:`` key in YAML
    roles: frozenset[str]       # from ``roles:`` key in YAML
    tools: frozenset[str]       # {"*"} means all
    schemas: frozenset[str]     # {"*"} means all


class AuthzPolicy:
    """Evaluates authorization decisions against a loaded policy."""

    def __init__(self, rules: list[_PolicyRule] | None = None) -> None:
        self._rules: list[_PolicyRule] = rules or []

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str) -> "AuthzPolicy":
        """Load a policy from a YAML file.  Raises on parse errors."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Policy file not found: {path}")

        with open(path, "r", encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}

        default_effect = raw.get("default_effect", "deny")
        if default_effect != "deny":
            raise ValueError(
                f"Unsupported default_effect '{default_effect}'; only 'deny' is supported."
            )

        rules: list[_PolicyRule] = []
        for idx, r in enumerate(raw.get("rules", [])):
            groups = r.get("groups", [])
            roles = r.get("roles", [])
            tools = r.get("tools", [])
            schemas = r.get("schemas", [])
            if not groups and not roles:
                raise ValueError(f"Rule #{idx} has no groups or roles defined.")
            rules.append(_PolicyRule(
                name=f"rule-{idx}",
                groups=frozenset(str(g) for g in groups),
                roles=frozenset(str(rl) for rl in roles),
                tools=frozenset(str(t) for t in tools),
                schemas=frozenset(str(s).lower() for s in schemas),
            ))

        logger.info("Loaded authorization policy with %d rules from %s", len(rules), path)
        return cls(rules=rules)

    @classmethod
    def allow_all(cls) -> "AuthzPolicy":
        """Return a policy that allows everything (for stdio / dev without policy file)."""
        return cls(rules=[
            _PolicyRule(
                name="allow-all",
                groups=frozenset({"*"}),
                roles=frozenset({"*"}),
                tools=frozenset({"*"}),
                schemas=frozenset({"*"}),
            ),
        ])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _identity_matches(
        rule: _PolicyRule,
        caller_groups: set[str],
        caller_roles: set[str],
    ) -> bool:
        """Return True if the caller satisfies the rule's identity constraint.

        A rule can specify ``groups``, ``roles``, or both.  The caller needs
        to match **at least one** of them (logical OR):

        * ``roles`` only → caller must have at least one listed role.
        * ``groups`` only → caller must belong to at least one listed group.
        * Both → caller must match at least one role **or** one group.
        * Either side using ``"*"`` is an unconditional match for that side.
        """
        has_groups = bool(rule.groups)
        has_roles = bool(rule.roles)

        groups_ok = False
        roles_ok = False

        if has_groups:
            groups_ok = ("*" in rule.groups) or bool(caller_groups.intersection(rule.groups))

        if has_roles:
            roles_ok = ("*" in rule.roles) or bool(caller_roles.intersection(rule.roles))

        return groups_ok or roles_ok

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def is_allowed(
        self,
        principal: Principal | None,
        tool: str,
        schema: str | None = None,
    ) -> AuthzDecision:
        """Check whether *principal* may invoke *tool* (optionally on *schema*).

        Returns an ``AuthzDecision`` with ``allowed=True`` on the first
        matching rule.  If no rule matches, returns ``allowed=False``
        (default deny).
        """
        if principal is None:
            return AuthzDecision(
                allowed=False,
                reason="No authenticated principal",
            )

        caller_groups = set(principal.groups)
        caller_roles = set(principal.roles)

        for rule in self._rules:
            # Identity match: groups, roles, or both
            if not self._identity_matches(rule, caller_groups, caller_roles):
                continue

            # Tool match
            if "*" not in rule.tools and tool not in rule.tools:
                continue

            # Schema match (when a schema is being accessed)
            if schema is not None:
                if "*" not in rule.schemas and schema.lower() not in rule.schemas:
                    continue

            return AuthzDecision(
                allowed=True,
                reason=f"Matched {rule.name}",
                matched_rule=rule.name,
            )

        return AuthzDecision(
            allowed=False,
            reason=f"No policy rule matches principal={principal.sub}, "
                   f"groups={list(caller_groups)}, roles={list(caller_roles)}, "
                   f"tool={tool}, schema={schema}",
        )

    def allowed_tools(self, principal: Principal | None) -> set[str] | None:
        """Return the set of tools allowed for *principal*.

        Returns ``None`` when a wildcard rule applies (meaning *all* tools are
        allowed).  Returns an empty set when nothing is allowed.
        """
        if principal is None:
            return set()

        caller_groups = set(principal.groups)
        caller_roles = set(principal.roles)
        tools: set[str] = set()

        for rule in self._rules:
            if not self._identity_matches(rule, caller_groups, caller_roles):
                continue
            if "*" in rule.tools:
                return None  # wildcard — all tools
            tools.update(rule.tools)

        return tools
