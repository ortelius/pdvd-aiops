"""Simple CLI app using click and httpx for E2E testing."""

import click
import httpx
from pydantic import BaseModel


class ApiResponse(BaseModel):
    url: str
    status_code: int
    content_length: int


def check_url(url: str) -> ApiResponse:
    """Check a URL and return structured response info."""
    resp = httpx.get(url, follow_redirects=True, timeout=10)
    return ApiResponse(
        url=str(resp.url),
        status_code=resp.status_code,
        content_length=len(resp.content),
    )


@click.command()
@click.argument("url")
def main(url: str):
    """Check the status of a URL."""
    result = check_url(url)
    click.echo(f"URL: {result.url}")
    click.echo(f"Status: {result.status_code}")
    click.echo(f"Size: {result.content_length} bytes")


if __name__ == "__main__":
    main()
