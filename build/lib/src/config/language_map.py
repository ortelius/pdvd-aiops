LANGUAGE_PACKAGE_BUILD_MAP = {
    "python": {
        "detect_files": [
            "requirements.txt",
            "requirements.in",
            "pyproject.toml",
            "Pipfile",
            "setup.py",
        ],
        "package_managers": {
            "pip": {
                "lock_files": [],
                "install": "pip install -r requirements.txt",
                "build": "pip install -r requirements.txt",
                "outdated_cmd": "pip list --outdated --format json",
                "output_format": "json_array",
                "field_map": {
                    "name": "name",
                    "current": "version",
                    "latest": "latest_version",
                },
            },
            "pip-tools": {
                "lock_files": ["requirements.txt"],
                "install": "pip install -r requirements.txt",
                "build": "pip install -r requirements.txt",
                "outdated_cmd": "pip list --outdated --format json",
                "output_format": "json_array",
                "field_map": {
                    "name": "name",
                    "current": "version",
                    "latest": "latest_version",
                },
            },
            "pipenv": {
                "lock_files": ["Pipfile.lock"],
                "install": "pipenv install --deploy",
                "build": "pipenv install",
                "outdated_cmd": "pipenv update --outdated",
                "output_format": "text",
            },
            "poetry": {
                "lock_files": ["poetry.lock"],
                "install": "poetry install --no-root",
                "build": "poetry install",
                "outdated_cmd": "poetry show --outdated",
                "output_format": "text",
            },
        },
    },
    "nodejs": {
        "detect_files": ["package.json"],
        "package_managers": {
            "npm": {
                "lock_files": ["package-lock.json"],
                "install": "npm install",
                "build": "npm run build",
                "outdated_cmd": "npm outdated --json",
                "output_format": "json_dict",
                "field_map": {
                    "name": "_key",
                    "current": "current",
                    "latest": "latest",
                },
            },
            "yarn": {
                "lock_files": ["yarn.lock"],
                "install": "yarn install",
                "build": "yarn build",
                "outdated_cmd": "yarn outdated",
                "output_format": "text",
            },
            "pnpm": {
                "lock_files": ["pnpm-lock.yaml"],
                "install": "pnpm install",
                "build": "pnpm build",
                "outdated_cmd": "pnpm outdated --format json",
                "output_format": "json_dict",
                "field_map": {
                    "name": "_key",
                    "current": "current",
                    "latest": "latest",
                },
            },
        },
    },
    "go": {
        "detect_files": ["go.mod"],
        "package_managers": {
            "go-mod": {
                "lock_files": ["go.sum"],
                "install": "go mod download",
                "build": "go build ./...",
                "outdated_cmd": "go list -u -m -json all",
                "output_format": "ndjson",
                "field_map": {
                    "name": "Path",
                    "current": "Version",
                    "latest": "Update.Version",
                },
                "skip_when": {"Main": True, "Update": None},
            }
        },
    },
    "java": {
        "detect_files": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "package_managers": {
            "maven": {
                "lock_files": [],
                "install": "mvn dependency:resolve",
                "build": "mvn package",
                "outdated_cmd": "mvn versions:display-dependency-updates",
                "output_format": "text",
            },
            "gradle": {
                "lock_files": ["gradle.lockfile"],
                "install": "gradle build",
                "build": "gradle build",
                "outdated_cmd": "./gradlew dependencyUpdates",
                "output_format": "text",
            },
        },
    },
    "rust": {
        "detect_files": ["Cargo.toml"],
        "package_managers": {
            "cargo": {
                "lock_files": ["Cargo.lock"],
                "install": "cargo fetch",
                "build": "cargo build --release",
                "outdated_cmd": "cargo install cargo-outdated && cargo outdated",
                "output_format": "text",
            }
        },
    },
    "ruby": {
        "detect_files": ["Gemfile"],
        "package_managers": {
            "bundler": {
                "lock_files": ["Gemfile.lock"],
                "install": "bundle install",
                "build": "bundle install",
                "outdated_cmd": "bundle outdated",
                "output_format": "text",
            }
        },
    },
    "php": {
        "detect_files": ["composer.json"],
        "package_managers": {
            "composer": {
                "lock_files": ["composer.lock"],
                "install": "composer install",
                "build": "composer install",
                "outdated_cmd": "composer outdated",
                "output_format": "text",
            }
        },
    },
    "dotnet": {
        "detect_files": ["*.csproj", "*.fsproj", "*.vbproj"],
        "package_managers": {
            "nuget": {
                "lock_files": ["packages.lock.json"],
                "install": "dotnet restore",
                "build": "dotnet build",
                "outdated_cmd": "dotnet list package --outdated",
                "output_format": "text",
            }
        },
    },
    "dart": {
        "detect_files": ["pubspec.yaml"],
        "package_managers": {
            "pub": {
                "lock_files": ["pubspec.lock"],
                "install": "dart pub get",
                "build": "dart compile exe",
                "outdated_cmd": "dart pub outdated",
                "output_format": "text",
            }
        },
    },
    "flutter": {
        "detect_files": ["pubspec.yaml"],
        "package_managers": {
            "flutter": {
                "lock_files": ["pubspec.lock"],
                "install": "flutter pub get",
                "build": "flutter build",
                "outdated_cmd": "flutter pub outdated",
                "output_format": "text",
            }
        },
    },
    "swift": {
        "detect_files": ["Package.swift"],
        "package_managers": {
            "swiftpm": {
                "lock_files": ["Package.resolved"],
                "install": "swift package resolve",
                "build": "swift build",
                "outdated_cmd": "swift package update --dry-run",
                "output_format": "text",
            }
        },
    },
    "terraform": {
        "detect_files": ["*.tf"],
        "package_managers": {
            "terraform": {
                "lock_files": [".terraform.lock.hcl"],
                "install": "terraform init",
                "build": "terraform plan",
                "outdated_cmd": "terraform providers lock -platform=all && terraform providers mirror ./mirror",
                "output_format": "text",
            }
        },
    },
}
