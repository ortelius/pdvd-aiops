#!/usr/bin/env python3
"""
CLI entry point for the Automated Dependency Update System.

Usage:
    python -m src.cli.main <repository>
    python -m src.agents.orchestrator <repository>   (backwards-compat)
"""

import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()


def validate_prerequisites() -> tuple[bool, str]:
    """
    Validate that all prerequisites are met for running the dependency updater.

    Returns:
        tuple: (is_valid: bool, message: str)
    """
    # Check for Docker
    try:
        docker_check = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=10
        )
        if docker_check.returncode != 0:
            return (
                False,
                "Docker is not available. Please install Docker from https://docs.docker.com/get-docker/",
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (
            False,
            "Docker is not available. Please install Docker from https://docs.docker.com/get-docker/",
        )

    # Check for GitHub Personal Access Token
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        return False, (
            "GITHUB_TOKEN not set. "
            "Please set your GitHub token: export GITHUB_TOKEN='your_token_here'. "
            "Create a token at: https://github.com/settings/tokens (Required scopes: repo, workflow)"
        )

    # Check for LLM provider API key
    from src.config.llm import get_required_api_key
    key_name, key_value = get_required_api_key()
    if key_name and not key_value:
        return False, (
            f"{key_name} not set. "
            f"Please set your API key: export {key_name}='your_key_here'"
        )

    return True, "All prerequisites validated successfully"


def main():
    """
    Main entry point for the automated dependency update system.
    """
    if len(sys.argv) < 2:
        print("""
Auto Update Dependencies Tool

Intelligently updates dependencies with automated testing and rollback.

Usage: python -m src.cli.main <repository>

Examples:
  python -m src.cli.main https://github.com/owner/repo
  python -m src.cli.main owner/repo

What it does:
  1. Analyzes your repo for outdated dependencies
  2. Updates ALL dependencies to latest (including major versions)
  3. Tests the changes (build, test, lint)
  4. Rolls back breaking updates if tests fail
  5. Creates PR if successful
  6. Creates Issue if updates can't be applied safely

Prerequisites:
  - Docker installed and running
  - GITHUB_TOKEN environment variable set
  - LLM provider API key (GROQ_API_KEY, ANTHROPIC_API_KEY, etc.)
  - Git configured with push access to the repository
""")
        sys.exit(1)

    repo_input = sys.argv[1]

    print("=" * 80)
    print("  Automated Dependency Update System (LangGraph Pipeline)")
    print("=" * 80)
    print()
    print(f"Repository: {repo_input}")
    print()

    # Check prerequisites
    print("Checking prerequisites...")
    is_valid, message = validate_prerequisites()

    if not is_valid:
        print(f"\n{message}\n")
        sys.exit(1)

    print("All prerequisites validated")
    print()

    # Run the LangGraph pipeline
    from src.pipeline.graph import run_pipeline

    try:
        result = run_pipeline(repo_url=repo_input)

        status = result.get("status", "unknown")
        if status == "pr_created":
            print(f"  PR Created: {result.get('url', 'N/A')}")
        elif status == "issue_created":
            print(f"  Issue Created: {result.get('url', 'N/A')}")
        elif status == "up_to_date":
            print(f"  {result.get('message', 'All dependencies are up to date.')}")
        else:
            print(f"  Status: {status}")
            if result.get("message"):
                print(f"  {result['message']}")

    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
