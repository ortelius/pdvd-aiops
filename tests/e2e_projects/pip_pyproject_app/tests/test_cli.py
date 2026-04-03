"""Tests for the CLI E2E app."""

from pydantic import BaseModel
from click.testing import CliRunner


def test_pydantic_model():
    """Verify pydantic models work."""

    class Item(BaseModel):
        name: str
        count: int

    item = Item(name="test", count=5)
    assert item.name == "test"
    assert item.count == 5


def test_pydantic_validation():
    """Verify pydantic validation rejects bad data."""
    from pydantic import ValidationError

    class Item(BaseModel):
        name: str
        count: int

    try:
        Item(name="test", count="not_a_number")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_click_runner():
    """Verify click CLI infrastructure works."""
    import click

    @click.command()
    @click.argument("name")
    def hello(name):
        click.echo(f"Hello {name}")

    runner = CliRunner()
    result = runner.invoke(hello, ["World"])
    assert result.exit_code == 0
    assert "Hello World" in result.output


def test_httpx_importable():
    """Verify httpx is installed and importable."""
    import httpx
    assert hasattr(httpx, "get")
    assert hasattr(httpx, "Client")
