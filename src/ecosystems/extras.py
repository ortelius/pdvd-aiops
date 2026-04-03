"""
Additional ecosystem plugins — bundler, composer, dotnet, maven, gradle, etc.

These follow the same pattern as the core plugins. Each uses ecosystem defaults
for build/test commands since their dependency files are more complex.
"""

from src.ecosystems import EcosystemPlugin, Dependency, register


@register
class PipenvPlugin(EcosystemPlugin):
    name = "pipenv"
    language = "python"
    detect_files = ["Pipfile"]
    lock_files = ["Pipfile.lock"]
    dependency_file = "Pipfile"

    def detect(self, repo_files): return "Pipfile.lock" in repo_files
    def parse_dependencies(self, content): return []
    def apply_updates(self, content, updates, file_name=""): return content, []
    def rollback_package(self, content, pkg, ver, file_name=""): return content
    def default_commands(self):
        return {"install": "pipenv install", "build": "pipenv install", "test": "pipenv run pytest", "lint": None}
    def outdated_command(self): return "pipenv update --outdated"


@register
class BundlerPlugin(EcosystemPlugin):
    name = "bundler"
    language = "ruby"
    detect_files = ["Gemfile"]
    lock_files = ["Gemfile.lock"]
    dependency_file = "Gemfile"

    def detect(self, repo_files): return "Gemfile" in repo_files
    def parse_dependencies(self, content): return []
    def apply_updates(self, content, updates, file_name=""): return content, []
    def rollback_package(self, content, pkg, ver, file_name=""): return content
    def default_commands(self):
        return {"install": "bundle install", "build": "bundle install", "test": "bundle exec rspec", "lint": None}
    def outdated_command(self): return "bundle outdated"


@register
class ComposerPlugin(EcosystemPlugin):
    name = "composer"
    language = "php"
    detect_files = ["composer.json"]
    lock_files = ["composer.lock"]
    dependency_file = "composer.json"

    def detect(self, repo_files): return "composer.json" in repo_files
    def parse_dependencies(self, content): return []
    def apply_updates(self, content, updates, file_name=""): return content, []
    def rollback_package(self, content, pkg, ver, file_name=""): return content
    def default_commands(self):
        return {"install": "composer install", "build": "composer install", "test": "composer test", "lint": None}
    def outdated_command(self): return "composer outdated"


@register
class NugetPlugin(EcosystemPlugin):
    name = "nuget"
    language = "dotnet"
    detect_files = ["*.csproj", "*.fsproj"]
    lock_files = ["packages.lock.json"]
    dependency_file = ""

    def detect(self, repo_files):
        return any(f.endswith(".csproj") or f.endswith(".fsproj") for f in repo_files)
    def parse_dependencies(self, content): return []
    def apply_updates(self, content, updates, file_name=""): return content, []
    def rollback_package(self, content, pkg, ver, file_name=""): return content
    def default_commands(self):
        return {"install": "dotnet restore", "build": "dotnet build", "test": "dotnet test", "lint": None}
    def outdated_command(self): return "dotnet list package --outdated"


@register
class MavenPlugin(EcosystemPlugin):
    name = "maven"
    language = "java"
    detect_files = ["pom.xml"]
    lock_files = []
    dependency_file = "pom.xml"

    def detect(self, repo_files): return "pom.xml" in repo_files
    def parse_dependencies(self, content): return []
    def apply_updates(self, content, updates, file_name=""): return content, []
    def rollback_package(self, content, pkg, ver, file_name=""): return content
    def default_commands(self):
        return {"install": "mvn dependency:resolve", "build": "mvn package", "test": "mvn test", "lint": None}
    def outdated_command(self): return "mvn versions:display-dependency-updates"


@register
class GradlePlugin(EcosystemPlugin):
    name = "gradle"
    language = "java"
    detect_files = ["build.gradle", "build.gradle.kts"]
    lock_files = ["gradle.lockfile"]
    dependency_file = "build.gradle"

    def detect(self, repo_files):
        return "build.gradle" in repo_files or "build.gradle.kts" in repo_files
    def parse_dependencies(self, content): return []
    def apply_updates(self, content, updates, file_name=""): return content, []
    def rollback_package(self, content, pkg, ver, file_name=""): return content
    def default_commands(self):
        return {"install": "gradle build", "build": "gradle build", "test": "gradle test", "lint": None}
    def outdated_command(self): return "./gradlew dependencyUpdates"
