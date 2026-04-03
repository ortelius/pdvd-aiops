"""Linter integration definitions."""

from src.integrations.registry import register_integration

register_integration(
    name="eslint",
    category="linter",
    config_files=[".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml", "eslint.config.js", "eslint.config.mjs"],
    run_command="npx eslint . --format json --no-error-on-unmatched-pattern",
    detect_command="npx eslint --version",
    output_format="json",
    ecosystem="nodejs",
    description="JavaScript/TypeScript linter",
)

register_integration(
    name="ruff",
    category="linter",
    config_files=["ruff.toml", ".ruff.toml"],
    run_command="ruff check . --output-format json",
    detect_command="ruff --version",
    output_format="json",
    ecosystem="python",
    description="Fast Python linter",
)

register_integration(
    name="golangci-lint",
    category="linter",
    config_files=[".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json"],
    run_command="golangci-lint run --out-format json",
    detect_command="golangci-lint --version",
    output_format="json",
    ecosystem="go",
    description="Go linter aggregator",
)

register_integration(
    name="clippy",
    category="linter",
    config_files=["clippy.toml", ".clippy.toml"],
    run_command="cargo clippy --message-format json",
    detect_command="cargo clippy --version",
    output_format="json",
    ecosystem="rust",
    description="Rust linter",
)

register_integration(
    name="rubocop",
    category="linter",
    config_files=[".rubocop.yml", ".rubocop.yaml"],
    run_command="rubocop --format json",
    detect_command="rubocop --version",
    output_format="json",
    ecosystem="ruby",
    description="Ruby linter and formatter",
)

register_integration(
    name="phpcs",
    category="linter",
    config_files=["phpcs.xml", "phpcs.xml.dist", ".phpcs.xml"],
    run_command="phpcs --report=json",
    detect_command="phpcs --version",
    output_format="json",
    ecosystem="php",
    description="PHP code sniffer",
)
