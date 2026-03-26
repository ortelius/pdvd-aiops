#!/usr/bin/env python3
"""
Scheduled update script for repositories matching a prefix.

Lists all GitHub repos matching REPO_PREFIX (default: "pdvd"),
starts the FastAPI server, and triggers dependency updates for each.
"""

import os
import subprocess
import sys
import time

import requests

API_BASE = "http://127.0.0.1:8000"
REPO_PREFIX = os.getenv("REPO_PREFIX", "pdvd")
GITHUB_TOKEN = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
POLL_INTERVAL = 30  # seconds
SERVER_STARTUP_TIMEOUT = 120  # seconds
JOB_TIMEOUT = 600  # 10 minutes per repo


def list_repos_with_prefix(prefix: str) -> list[str]:
    """Fetch all repos for the authenticated user matching the given prefix."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    repos = []
    page = 1

    while True:
        resp = requests.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={"per_page": 100, "page": page, "sort": "full_name"},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for repo in batch:
            if repo["name"].startswith(prefix):
                repos.append(repo["full_name"])
        page += 1

    return repos


def wait_for_server():
    """Wait until the FastAPI server is ready."""
    print("Waiting for server to start...")
    deadline = time.time() + SERVER_STARTUP_TIMEOUT
    while time.time() < deadline:
        try:
            resp = requests.get(f"{API_BASE}/health", timeout=5)
            if resp.status_code == 200:
                print("Server is ready.")
                return
        except requests.ConnectionError:
            pass
        time.sleep(2)
    print("ERROR: Server did not start in time.", file=sys.stderr)
    sys.exit(1)


def trigger_update(repo: str) -> str:
    """Call the update endpoint and return the job_id."""
    resp = requests.post(
        f"{API_BASE}/api/repositories/update",
        json={"repository": repo},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["job_id"]


def wait_for_job(job_id: str, repo: str) -> dict:
    """Poll job status until completed or failed."""
    deadline = time.time() + JOB_TIMEOUT
    while time.time() < deadline:
        resp = requests.get(f"{API_BASE}/api/jobs/{job_id}", timeout=10)
        resp.raise_for_status()
        job = resp.json()
        status = job["status"]
        if status in ("completed", "failed"):
            return job
        time.sleep(POLL_INTERVAL)

    return {"status": "timeout", "error": f"Job {job_id} for {repo} timed out"}


def main():
    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_PERSONAL_ACCESS_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)

    # 1. List repos
    print(f"Listing repos with prefix '{REPO_PREFIX}'...")
    repos = list_repos_with_prefix(REPO_PREFIX)
    if not repos:
        print(f"No repositories found matching prefix '{REPO_PREFIX}'.")
        return
    print(f"Found {len(repos)} repo(s): {', '.join(repos)}")

    # 2. Start server in background
    print("Starting FastAPI server...")
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "src.api.startup", "--skip-checks", "--no-reload"],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    try:
        wait_for_server()

        # 3. Process each repo sequentially
        results = []
        for repo in repos:
            print(f"\n{'='*60}")
            print(f"Processing: {repo}")
            print(f"{'='*60}")
            try:
                job_id = trigger_update(repo)
                print(f"  Job ID: {job_id}")
                job = wait_for_job(job_id, repo)
                results.append({"repo": repo, "status": job["status"], "job_id": job_id})
                print(f"  Status: {job['status']}")
                if job.get("error"):
                    print(f"  Error: {job['error']}")
            except Exception as e:
                results.append({"repo": repo, "status": "error", "error": str(e)})
                print(f"  Error: {e}")

        # 4. Summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        for r in results:
            icon = "OK" if r["status"] == "completed" else "FAIL"
            print(f"  [{icon}] {r['repo']} — {r['status']}")

        failed = [r for r in results if r["status"] != "completed"]
        if failed:
            print(f"\n{len(failed)} repo(s) failed.")
            sys.exit(1)
        else:
            print(f"\nAll {len(results)} repo(s) updated successfully.")

    finally:
        print("\nShutting down server...")
        server_proc.terminate()
        server_proc.wait(timeout=10)


if __name__ == "__main__":
    main()
