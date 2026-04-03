"""Formatter integration definitions."""

from src.integrations.registry import register_integration

register_integration(
    name="prettier",
    category="formatter",
    config_files=[".prettierrc", ".prettierrc.js", ".prettierrc.json", ".prettierrc.yml", ".prettierrc.yaml", "prettier.config.js"],
    run_command="npx prettier --check .",
    detect_command="npx prettier --version",
    output_format="text",
    ecosystem="nodejs",
    severity="info",
    description="Code formatter for JS/TS/CSS/HTML/JSON",
)

register_integration(
    name="black",
    category="formatter",
    config_files=[],  # Configured in pyproject.toml [tool.black] — detected via pyproject check
    run_command="black --check --diff .",
    detect_command="black --version",
    output_format="text",
    ecosystem="python",
    severity="info",
    description="Python code formatter",
)

register_integration(
    name="gofmt",
    category="formatter",
    config_files=[],  # Built into Go — always available if go.mod exists
    run_command="gofmt -l .",
    detect_command="gofmt -h",
    output_format="text",
    ecosystem="go",
    severity="info",
    description="Go code formatter",
)
