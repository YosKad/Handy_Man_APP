#!/usr/bin/env python3
"""Architecture guards.

Rules that documentation cannot enforce on its own, so CI does:

1. No provider SDK is imported outside ``app/infrastructure/ai/adapters/``.
   This is what keeps the provider abstraction real rather than aspirational.
2. ``domain`` layers stay pure: no FastAPI, SQLAlchemy, Redis, or provider SDKs.
3. Dependencies point inward: ``features/*/domain`` never imports ``application``,
   ``data``, ``api`` or ``infrastructure``.
4. Only ``app/main.py`` and ``app/api/`` import FastAPI.
5. No relative imports (also enforced by ruff TID252, checked here for clarity).

Exit code 1 with a per-violation report, so a failure says what to fix.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"

PROVIDER_SDKS = ("openai", "anthropic", "google.genai", "google.generativeai", "mistralai")
WEB_FRAMEWORKS = ("fastapi", "starlette")
INFRA_PACKAGES = ("sqlalchemy", "redis", "celery", "alembic", "asyncpg")

ADAPTER_PREFIX = "app/infrastructure/ai/adapters/"
API_PREFIXES = ("app/api/", "app/main.py")
CONTAINER = "app/core/container.py"


def _imports(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def _relative_imports(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            yield "." * node.level + (node.module or "")


def _matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def check() -> list[str]:
    violations: list[str] = []

    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR.parent).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - a syntax error fails elsewhere too
            violations.append(f"{rel}: cannot parse ({exc})")
            continue

        modules = list(_imports(tree))
        is_adapter = rel.startswith(ADAPTER_PREFIX)
        is_api = rel.startswith(API_PREFIXES)
        is_domain = "/domain/" in rel
        is_container = rel == CONTAINER

        for module in modules:
            if _matches(module, PROVIDER_SDKS) and not is_adapter:
                violations.append(
                    f"{rel}: imports provider SDK {module!r}. "
                    f"Provider code belongs in {ADAPTER_PREFIX} behind a port."
                )
            if _matches(module, WEB_FRAMEWORKS) and not is_api:
                violations.append(
                    f"{rel}: imports {module!r}. Only app/api/ and app/main.py "
                    f"know about the web framework."
                )
            if is_domain and _matches(module, INFRA_PACKAGES + WEB_FRAMEWORKS + PROVIDER_SDKS):
                violations.append(f"{rel}: domain layer must stay pure, but imports {module!r}.")
            if is_domain and module.startswith("app."):
                # The container is the one place allowed to see everything.
                forbidden = (".application", ".data", "app.api", "app.infrastructure")
                if any(token in module for token in forbidden):
                    violations.append(
                        f"{rel}: domain imports {module!r}; dependencies must point inward."
                    )
            if module.startswith("app.") and "/application/" in rel and "app.api" in module:
                violations.append(f"{rel}: application layer must not import the API layer.")

        if is_container:
            continue

        for relative in _relative_imports(tree):
            violations.append(f"{rel}: relative import {relative!r}; use absolute app.* imports.")

    return violations


def main() -> int:
    violations = check()
    if not violations:
        print("architecture check: ok")
        return 0
    print("architecture check failed:\n")
    for violation in violations:
        print(f"  - {violation}")
    print(f"\n{len(violations)} violation(s). See docs/02-architecture.md §2.2.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
