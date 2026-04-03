"""Security scanner integration definitions."""

import json
from src.integrations.registry import register_integration


def _parse_trivy_json(stdout: str, stderr: str) -> list[dict]:
    """Parse trivy JSON output into structured findings."""
    try:
        data = json.loads(stdout)
        findings = []
        for result in data.get("Results", []):
            for vuln in result.get("Vulnerabilities", []):
                findings.append({
                    "package": vuln.get("PkgName", "unknown"),
                    "severity": vuln.get("Severity", "unknown"),
                    "vulnerability": vuln.get("VulnerabilityID", "unknown"),
                    "detail": vuln.get("Title", vuln.get("Description", ""))[:500],
                })
        return findings
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_osv_json(stdout: str, stderr: str) -> list[dict]:
    """Parse osv-scanner JSON output into structured findings."""
    try:
        data = json.loads(stdout)
        findings = []
        for result in data.get("results", []):
            for pkg in result.get("packages", []):
                for vuln in pkg.get("vulnerabilities", []):
                    findings.append({
                        "package": pkg.get("package", {}).get("name", "unknown"),
                        "severity": vuln.get("database_specific", {}).get("severity", "unknown"),
                        "vulnerability": vuln.get("id", "unknown"),
                        "detail": vuln.get("summary", "")[:500],
                    })
        return findings
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_semgrep_json(stdout: str, stderr: str) -> list[dict]:
    """Parse semgrep JSON output into structured findings."""
    try:
        data = json.loads(stdout)
        findings = []
        for result in data.get("results", []):
            findings.append({
                "package": result.get("path", "unknown"),
                "severity": result.get("extra", {}).get("severity", "unknown"),
                "vulnerability": result.get("check_id", "unknown"),
                "detail": result.get("extra", {}).get("message", "")[:500],
            })
        return findings
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_bandit_json(stdout: str, stderr: str) -> list[dict]:
    """Parse bandit JSON output into structured findings."""
    try:
        data = json.loads(stdout)
        findings = []
        for result in data.get("results", []):
            findings.append({
                "package": result.get("filename", "unknown"),
                "severity": result.get("issue_severity", "unknown"),
                "vulnerability": result.get("test_id", "unknown"),
                "detail": result.get("issue_text", "")[:500],
            })
        return findings
    except (json.JSONDecodeError, TypeError):
        return []


# ── Universal scanners (no config needed, always useful) ──

register_integration(
    name="trivy",
    category="security_scanner",
    config_files=["trivy.yaml", ".trivyignore"],
    run_command="trivy fs . --format json --scanners vuln,secret,misconfig",
    detect_command="trivy --version",
    install_command="brew install trivy || curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin",
    uninstall_command="rm -f /usr/local/bin/trivy",
    output_format="json",
    parse_output=_parse_trivy_json,
    ecosystem=None,
    severity="warning",
    description="Vulnerability, secret, and misconfiguration scanner",
)

register_integration(
    name="osv-scanner",
    category="security_scanner",
    config_files=["osv-scanner.toml"],
    run_command="osv-scanner scan --format json .",
    detect_command="osv-scanner --version",
    install_command="go install github.com/google/osv-scanner/cmd/osv-scanner@latest",
    uninstall_command="rm -f $(go env GOPATH)/bin/osv-scanner",
    output_format="json",
    parse_output=_parse_osv_json,
    ecosystem=None,
    severity="warning",
    description="Google OSV vulnerability database scanner",
)

register_integration(
    name="semgrep",
    category="security_scanner",
    config_files=[".semgrep.yml", ".semgrep.yaml", ".semgrep/"],
    run_command="semgrep --config auto --json .",
    detect_command="semgrep --version",
    install_command="pip install semgrep",
    uninstall_command="pip uninstall semgrep -y",
    output_format="json",
    parse_output=_parse_semgrep_json,
    ecosystem=None,
    severity="warning",
    description="Static analysis with community security rules",
)

register_integration(
    name="bandit",
    category="security_scanner",
    config_files=[".bandit", "bandit.yaml", "bandit.yml"],
    run_command="bandit -r . -f json",
    detect_command="bandit --version",
    install_command="pip install bandit",
    uninstall_command="pip uninstall bandit -y",
    output_format="json",
    parse_output=_parse_bandit_json,
    ecosystem="python",
    severity="warning",
    description="Python security linter",
)

register_integration(
    name="hadolint",
    category="security_scanner",
    config_files=[".hadolint.yaml", ".hadolint.yml", "Dockerfile"],
    run_command="hadolint Dockerfile -f json",
    detect_command="hadolint --version",
    install_command="brew install hadolint || wget -qO /usr/local/bin/hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64 && chmod +x /usr/local/bin/hadolint",
    uninstall_command="rm -f /usr/local/bin/hadolint",
    output_format="json",
    ecosystem=None,
    severity="info",
    description="Dockerfile best practices linter",
)

register_integration(
    name="checkov",
    category="security_scanner",
    config_files=[".checkov.yaml", ".checkov.yml"],
    run_command="checkov -d . -o json --quiet",
    detect_command="checkov --version",
    install_command="pip install checkov",
    uninstall_command="pip uninstall checkov -y",
    output_format="json",
    ecosystem=None,
    severity="warning",
    description="IaC security scanner (Terraform, K8s, Docker)",
)

register_integration(
    name="tfsec",
    category="security_scanner",
    config_files=[".tfsec/", "*.tf"],
    run_command="tfsec . --format json",
    detect_command="tfsec --version",
    output_format="json",
    ecosystem=None,
    severity="warning",
    description="Terraform security scanner",
)
