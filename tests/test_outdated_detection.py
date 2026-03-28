"""
Outdated detection tests.

Verifies that raw command output from each package manager's
outdated command is parsed correctly into {name, current, latest} dicts.

Each test uses realistic output captured from actual package manager commands.
"""

import json

import pytest

from src.ecosystems import get_plugin_by_name
from src.pipeline.nodes.analyze import parse_outdated_output


# ── pip: json_array format ───────────────────────────────


class TestPipOutdatedDetection:

    def test_json_array_parsing(self):
        """pip list --outdated --format json"""
        plugin = get_plugin_by_name("pip")
        stdout = json.dumps([
            {"name": "flask", "version": "2.3.2", "latest_version": "3.0.0", "latest_filetype": "wheel"},
            {"name": "requests", "version": "2.28.0", "latest_version": "2.31.0", "latest_filetype": "wheel"},
            {"name": "sqlalchemy", "version": "1.4.49", "latest_version": "2.0.21", "latest_filetype": "wheel"},
        ])
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 3
        assert result[0] == {"name": "flask", "current": "2.3.2", "latest": "3.0.0"}
        assert result[1] == {"name": "requests", "current": "2.28.0", "latest": "2.31.0"}
        assert result[2] == {"name": "sqlalchemy", "current": "1.4.49", "latest": "2.0.21"}

    def test_empty_array(self):
        """pip reports empty array when everything is up to date."""
        plugin = get_plugin_by_name("pip")
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output("[]", detected_info, plugin)
        assert result == []

    def test_single_package(self):
        """Single outdated package."""
        plugin = get_plugin_by_name("pip")
        stdout = json.dumps([
            {"name": "django", "version": "4.2.0", "latest_version": "5.0.0", "latest_filetype": "wheel"},
        ])
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 1
        assert result[0]["name"] == "django"
        assert result[0]["current"] == "4.2.0"
        assert result[0]["latest"] == "5.0.0"


# ── npm: json_dict format ────────────────────────────────


class TestNpmOutdatedDetection:

    def test_json_dict_parsing(self):
        """npm outdated --json"""
        plugin = get_plugin_by_name("npm")
        stdout = json.dumps({
            "express": {"current": "4.18.2", "wanted": "4.18.3", "latest": "5.0.0", "location": "node_modules/express"},
            "lodash": {"current": "4.17.21", "wanted": "4.17.21", "latest": "4.17.21", "location": "node_modules/lodash"},
            "axios": {"current": "1.4.0", "wanted": "1.6.2", "latest": "1.6.2", "location": "node_modules/axios"},
        })
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 3
        assert result[0]["name"] == "express"
        assert result[0]["current"] == "4.18.2"
        assert result[0]["latest"] == "5.0.0"
        # lodash is at latest — npm still reports it
        assert result[1]["name"] == "lodash"
        assert result[1]["current"] == "4.17.21"
        assert result[1]["latest"] == "4.17.21"

    def test_empty_dict(self):
        """npm returns empty JSON object when up to date."""
        plugin = get_plugin_by_name("npm")
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output("{}", detected_info, plugin)
        assert result == []

    def test_extracts_name_from_dict_key(self):
        """npm uses dict keys as package names (_key field map)."""
        plugin = get_plugin_by_name("npm")
        stdout = json.dumps({
            "@scope/package": {"current": "1.0.0", "wanted": "1.1.0", "latest": "2.0.0"},
        })
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 1
        assert result[0]["name"] == "@scope/package"


# ── pnpm: json_dict format ───────────────────────────────


class TestPnpmOutdatedDetection:

    def test_json_dict_parsing(self):
        """pnpm outdated --format json"""
        plugin = get_plugin_by_name("pnpm")
        stdout = json.dumps({
            "fastify": {"current": "4.20.0", "latest": "4.25.0"},
            "zod": {"current": "3.21.0", "latest": "3.22.4"},
        })
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 2
        assert result[0] == {"name": "fastify", "current": "4.20.0", "latest": "4.25.0"}
        assert result[1] == {"name": "zod", "current": "3.21.0", "latest": "3.22.4"}


# ── go: ndjson format with skip rules ────────────────────


class TestGoOutdatedDetection:

    def test_ndjson_parsing_with_skip_rules(self):
        """go list -u -m -json all — skips Main module and packages without Update."""
        plugin = get_plugin_by_name("go-mod")
        stdout = '\n'.join([
            json.dumps({"Path": "github.com/test/project", "Main": True, "Version": "v0.0.0"}),
            json.dumps({"Path": "github.com/gin-gonic/gin", "Version": "v1.9.1", "Update": {"Path": "github.com/gin-gonic/gin", "Version": "v1.10.0"}}),
            json.dumps({"Path": "golang.org/x/crypto", "Version": "v0.11.0", "Update": {"Path": "golang.org/x/crypto", "Version": "v0.17.0"}}),
            json.dumps({"Path": "github.com/stretchr/testify", "Version": "v1.8.4"}),
            json.dumps({"Path": "google.golang.org/grpc", "Version": "v1.56.0", "Update": {"Path": "google.golang.org/grpc", "Version": "v1.60.0"}}),
        ])
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
            "skip_when": plugin.outdated_skip_when(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 3
        names = [r["name"] for r in result]
        assert "github.com/gin-gonic/gin" in names
        assert "golang.org/x/crypto" in names
        assert "google.golang.org/grpc" in names
        assert "github.com/test/project" not in names
        assert "github.com/stretchr/testify" not in names

    def test_nested_version_extraction(self):
        """Correct version extraction from nested Update.Version field."""
        plugin = get_plugin_by_name("go-mod")
        stdout = json.dumps({"Path": "github.com/pkg/errors", "Version": "v0.9.1", "Update": {"Path": "github.com/pkg/errors", "Version": "v0.9.2"}})
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
            "skip_when": plugin.outdated_skip_when(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 1
        assert result[0]["name"] == "github.com/pkg/errors"
        assert result[0]["current"] == "v0.9.1"
        assert result[0]["latest"] == "v0.9.2"

    def test_all_up_to_date(self):
        """No Update field on any module — all up to date."""
        plugin = get_plugin_by_name("go-mod")
        stdout = '\n'.join([
            json.dumps({"Path": "github.com/test/project", "Main": True, "Version": "v0.0.0"}),
            json.dumps({"Path": "github.com/pkg/errors", "Version": "v0.9.1"}),
        ])
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
            "skip_when": plugin.outdated_skip_when(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)
        assert result == []

    def test_indirect_dependencies_with_update(self):
        """Indirect deps that have Update field should still be detected."""
        plugin = get_plugin_by_name("go-mod")
        stdout = json.dumps({
            "Path": "golang.org/x/net",
            "Version": "v0.10.0",
            "Indirect": True,
            "Update": {"Path": "golang.org/x/net", "Version": "v0.19.0"},
        })
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
            "skip_when": plugin.outdated_skip_when(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 1
        assert result[0]["name"] == "golang.org/x/net"
        assert result[0]["latest"] == "v0.19.0"


# ── poetry: text format ──────────────────────────────────


class TestPoetryOutdatedDetection:

    def test_text_parsing(self):
        """poetry show --outdated"""
        plugin = get_plugin_by_name("poetry")
        stdout = """django       4.2.7  5.0.0  A high-level Python web framework
celery       5.3.4  5.3.6  Distributed Task Queue
redis        4.6.0  5.0.1  Python client for Redis"""
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 3
        assert result[0]["name"] == "django"
        assert result[0]["current"] == "4.2.7"
        assert result[0]["latest"] == "5.0.0"
        assert result[2]["name"] == "redis"
        assert result[2]["latest"] == "5.0.1"

    def test_description_not_included_in_version(self):
        """Description text after version should not pollute the latest field."""
        plugin = get_plugin_by_name("poetry")
        stdout = "flask  2.3.2  3.0.0  A lightweight WSGI web application framework"
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 1
        assert result[0]["latest"] == "3.0.0"  # NOT "framework"


# ── cargo: text format ───────────────────────────────────


class TestCargoOutdatedDetection:

    def test_text_parsing(self):
        """cargo outdated"""
        plugin = get_plugin_by_name("cargo")
        stdout = """Name    Project  Compat  Latest  Kind
----    -------  ------  ------  ----
serde   1.0.180  1.0.193 1.0.193 Normal
tokio   1.29.0   1.35.0  1.35.0  Normal
clap    4.3.0    4.4.11  4.4.11  Normal"""
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 3
        assert result[0]["name"] == "serde"
        assert result[0]["current"] == "1.0.180"
        assert result[0]["latest"] == "1.0.193"

    def test_skips_header_and_separator(self):
        """Header row (Name ...) and separator (----) should be skipped."""
        plugin = get_plugin_by_name("cargo")
        stdout = """Name    Project  Compat  Latest  Kind
----    -------  ------  ------  ----
serde   1.0.180  1.0.193 1.0.193 Normal"""
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) == 1
        assert result[0]["name"] == "serde"


# ── bundler: text format ─────────────────────────────────


class TestBundlerOutdatedDetection:

    def test_text_parsing(self):
        """bundle outdated"""
        plugin = get_plugin_by_name("bundler")
        stdout = """Outdated gems included in the bundle:
  * rails (newest 7.1.2, installed 7.0.8)
  * puma (newest 6.4.0, installed 6.3.1)"""
        detected_info = {
            "output_format": plugin.outdated_output_format(),
            "field_map": plugin.outdated_field_map(),
        }

        result = parse_outdated_output(stdout, detected_info, plugin)

        assert len(result) >= 2


# ── Generic text parser edge cases ───────────────────────


class TestGenericTextParser:

    def test_skips_headers(self):
        """Headers starting with Package, Name, =, - should be skipped."""
        stdout = """Package     Current  Latest
-------     -------  ------
flask       2.3.2    3.0.0
requests    2.28.0   2.31.0"""
        detected_info = {"output_format": "text"}

        result = parse_outdated_output(stdout, detected_info)

        assert len(result) == 2
        assert result[0]["name"] == "flask"

    def test_skips_separator_rows(self):
        stdout = """flask    2.3.2  3.0.0
---------+------+-----
requests 2.28.0 2.31.0"""
        detected_info = {"output_format": "text"}

        result = parse_outdated_output(stdout, detected_info)

        assert len(result) == 2

    def test_empty_output(self):
        detected_info = {"output_format": "text"}
        result = parse_outdated_output("", detected_info)
        assert result == []

    def test_malformed_json_falls_back_to_text(self):
        """If JSON parsing fails, fall back to text parser."""
        stdout = "not valid json { [}"
        detected_info = {"output_format": "json_array", "field_map": {}}

        result = parse_outdated_output(stdout, detected_info)
        assert isinstance(result, list)

    def test_json_dict_with_extra_fields_ignored(self):
        """Parsers should extract only name/current/latest."""
        stdout = json.dumps({
            "express": {
                "current": "4.18.2",
                "wanted": "4.18.3",
                "latest": "5.0.0",
                "location": "node_modules/express",
                "type": "dependencies",
                "homepage": "https://expressjs.com/"
            }
        })
        detected_info = {
            "output_format": "json_dict",
            "field_map": {"name": "_key", "current": "current", "latest": "latest"},
        }

        result = parse_outdated_output(stdout, detected_info)

        assert len(result) == 1
        assert set(result[0].keys()) == {"name", "current", "latest"}
