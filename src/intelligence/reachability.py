"""
Reachability analysis for npm/pip — determines if vulnerable code is actually called.

Go gets real call-graph analysis via govulncheck. npm and pip get nothing — all
findings appear generic. This analyzer greps source code for imports of vulnerable
packages, then feeds the import context + CVE description to the LLM to determine
whether the vulnerable code path is reachable from the application.

Cost: ~1 LLM call, only when non-Go ecosystems have audit findings.
"""

import subprocess
from typing import Optional

from src.callbacks.cost_tracker import CostTracker
from src.intelligence.base import invoke_llm


# Ecosystems that already have native reachability analysis
_ECOSYSTEMS_WITH_NATIVE_REACHABILITY = {"go-mod"}


class ReachabilityAnalyzer:
    """Determine if vulnerable packages are actually imported/called in source code."""

    @property
    def name(self) -> str:
        return "reachability"

    def should_run(self, state: dict) -> bool:
        """Run for non-Go ecosystems that have audit findings and repo access."""
        package_manager = state.get("package_manager", "")
        if package_manager in _ECOSYSTEMS_WITH_NATIVE_REACHABILITY:
            return False
        if not state.get("repo_path"):
            return False
        audit_results = state.get("audit_results") or []
        return any(r.get("finding_count", 0) > 0 for r in audit_results)

    def analyze(self, state: dict, tracker: Optional[CostTracker] = None) -> dict:
        """
        Grep source for imports of vulnerable packages, then assess reachability.

        Returns: {"reachability_summary": str}
        """
        repo_path = state.get("repo_path", "")
        language = state.get("language", "")
        audit_results = state.get("audit_results") or []

        # Collect vulnerable packages with their CVE details
        vuln_packages = _extract_vulnerable_packages(audit_results)
        if not vuln_packages:
            return {}

        # Grep source for imports of each vulnerable package
        import_context = _grep_imports(repo_path, language, vuln_packages)

        # Build the prompt
        findings_text = "\n".join(
            f"- **{pkg}**: {', '.join(v['id'] for v in vulns)} — {vulns[0].get('detail', '')[:150]}"
            for pkg, vulns in vuln_packages.items()
        )

        prompt = f"""You are a security engineer determining which vulnerable dependencies are actually
reachable (imported and used) in this application's source code.

Language: {language}

Vulnerable packages and their CVEs:
{findings_text}

Source code import analysis:
{import_context if import_context else "No imports of vulnerable packages found in source code."}

For each vulnerable package, determine:
1. **Reachable** — the package IS imported and the vulnerable functionality is likely used
2. **Imported but likely safe** — the package is imported but the vulnerable code path
   (e.g. specific function, parser, protocol handler) is probably not used
3. **Not imported** — the package is not directly imported (may be a transitive dependency)

For each package, explain WHY you made that determination based on the import evidence.
Format as concise markdown (no top-level heading). Use bold for package names.
Keep under 350 words."""

        summary = invoke_llm(prompt, max_tokens=500, tracker=tracker, phase_name=self.name)
        if not summary:
            return {}

        return {"reachability_summary": summary}


def _extract_vulnerable_packages(audit_results: list[dict]) -> dict:
    """Extract {package_name: [vuln_details]} from audit results."""
    from collections import defaultdict
    packages = defaultdict(list)

    for result in audit_results:
        for finding in result.get("findings", []):
            pkg = finding.get("package", "")
            if not pkg or pkg == "unknown":
                continue
            packages[pkg].append({
                "id": finding.get("vulnerability", "unknown"),
                "severity": finding.get("severity", "unknown"),
                "detail": finding.get("detail", ""),
            })

    return dict(packages)


def _grep_imports(repo_path: str, language: str, vuln_packages: dict) -> str:
    """Grep source files for imports of vulnerable packages."""
    if not repo_path:
        return ""

    ext_map = {
        "python": "*.py",
        "nodejs": "*.{js,ts,jsx,tsx,mjs,cjs}",
        "rust": "*.rs",
    }
    glob_pattern = ext_map.get(language, "*.{py,js,ts,go,rs}")

    results = []
    for pkg_name in list(vuln_packages.keys())[:15]:  # cap to prevent bloat
        # Search for import/require of this package
        lines = _grep_for_package(repo_path, pkg_name, glob_pattern, language)
        if lines:
            results.append(f"**{pkg_name}** — found in source:")
            results.extend(f"  {l}" for l in lines[:8])
        else:
            results.append(f"**{pkg_name}** — NOT found in source imports")

    return "\n".join(results) if results else ""


def _grep_for_package(repo_path: str, pkg_name: str, glob: str, language: str) -> list[str]:
    """Grep for imports of a specific package."""
    import re

    # Build search pattern based on language
    base = pkg_name.split("/")[-1].replace("-", "[-_]")

    if language == "python":
        pattern = rf"(?:from|import)\s+{base}"
    elif language == "nodejs":
        pattern = rf"""(?:require|from)\s*[\('"]+{re.escape(pkg_name)}"""
    else:
        pattern = re.escape(pkg_name)

    try:
        result = subprocess.run(
            ["rg", "-n", "--glob", glob, "--no-heading", "-m", "10", pattern, repo_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode <= 1:
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            return [l.replace(repo_path + "/", "") for l in lines]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(
            ["grep", "-rn", "--include", glob.replace("{", "").replace("}", "").split(",")[0],
             "-m", "10", "-E", pattern, repo_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode <= 1:
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            return [l.replace(repo_path + "/", "") for l in lines]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return []
