# Contributing to ESP-Weaver

Thank you for your interest in contributing to ESP-Weaver! This document provides guidelines for contributing to this project.

## Getting Started

### Prerequisites

- Python 3.13.2 or higher
- Home Assistant Core 2025.12.5 or higher
- An ESP device with ESP Local Control enabled

### Development Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/espressif/esp_weaver.git
   cd esp_weaver
   ```

2. Install development dependencies:

   ```bash
   # Recommended: Install package with dev dependencies (editable mode)
   pip install -e ".[dev]"

   # Alternative: Install from requirements file
   # Use this if you need additional test utilities not included in [dev]
   pip install -r requirements_test.txt
   ```

   > **Note**: For most contributors, `pip install -e ".[dev]"` is sufficient. The `requirements_test.txt` file includes additional utilities that may be useful for CI/CD environments or advanced testing scenarios.

3. Run tests to verify setup:

   ```bash
   pytest tests/
   ```

4. Copy the integration to your Home Assistant custom_components directory:

   **Linux/macOS:**

   ```bash
   cp -r custom_components/esp_weaver /path/to/homeassistant/config/custom_components/
   ```

   **Windows (PowerShell):**

   ```powershell
   Copy-Item -Recurse custom_components\esp_weaver $env:USERPROFILE\.homeassistant\custom_components\
   ```

   **Cross-platform (Python 3.13.2+):**

   ```bash
   python -c "import shutil; shutil.copytree('custom_components/esp_weaver', '/path/to/config/custom_components/esp_weaver', dirs_exist_ok=True)"
   ```

   > **Note**: The `dirs_exist_ok=True` parameter allows overwriting an existing directory.

   > **Note**: Windows users can also use Git Bash or WSL to run the Unix `cp` command. The typical Home Assistant config path on Windows is `%USERPROFILE%\.homeassistant\`.

## Code Style

This project uses [ruff](https://github.com/astral-sh/ruff) for code formatting and linting.

### Before Submitting

Run the following checks:

```bash
# Format code
ruff format .

# Check for issues
ruff check .
```

### Style Guidelines

- Follow PEP 8 guidelines
- Use type hints for all function parameters and return values
- Write docstrings for all public functions and classes
- Keep lines under 88 characters
- Use `async`/`await` for all I/O operations
  - **Enforcement**: Ruff ASYNC rules detect blocking calls inside async functions:
    - `ASYNC210`: Blocking HTTP calls (e.g., `urllib`, `requests`)
    - `ASYNC220`/`ASYNC221`: Blocking process calls (e.g., `subprocess`, `os.system`)
    - `ASYNC230`: Blocking file operations (e.g., `open()`, synchronous file I/O)
    - `ASYNC251`: Blocking sleep calls (e.g., `time.sleep()`)
  - **CI validation**: The `ruff check .` step in the PR workflow fails the build if blocking calls are detected in async code
  - Run `ruff check . --select=ASYNC` locally to verify before submitting

## Pull Request Process

1. Fork the repository
2. Create a branch from `main` using a descriptive name:

   **Branch naming pattern:** `type/ISSUE-123-short-description` or `type/short-description`

   | Prefix     | Use case                                      | Example                              |
   |------------|-----------------------------------------------|--------------------------------------|
   | `feature/` | New functionality                             | `feature/42-add-humidity-alerts`     |
   | `bugfix/`  | Bug fixes                                     | `bugfix/108-fix-connection-timeout`  |
   | `hotfix/`  | Urgent production fixes                       | `hotfix/critical-auth-bypass`        |
   | `docs/`    | Documentation changes                         | `docs/update-installation-guide`     |
   | `refactor/`| Code restructuring without behavior change    | `refactor/simplify-discovery-logic`  |
   | `test/`    | Adding or updating tests                      | `test/add-coordinator-tests`         |
   | `chore/`   | Maintenance tasks (deps, CI, tooling)         | `chore/upgrade-ruff-config`          |

   ```bash
   git checkout -b feature/your-feature-name
   # or with issue reference:
   git checkout -b bugfix/123-fix-sensor-discovery
   ```

3. Make your changes
4. Run linting checks:

   ```bash
   ruff format . && ruff check .
   ```

5. Run tests:

   ```bash
   pytest tests/
   ```

6. Commit your changes with a descriptive message
7. Push to your fork and submit a Pull Request

### PR Guidelines

- Keep PRs focused on a single feature or fix
- Update documentation if needed
- Add tests for new features or bug fixes
- Ensure all tests pass before submitting
- Add entries to CHANGELOG.md under `[Unreleased]` using this format:

  ```markdown
  ## [Unreleased]

  ### Added
  - Brief description of new feature (#PR-NUMBER)

  ### Changed
  - Brief description of modification (#PR-NUMBER)

  ### Fixed
  - Brief description of bug fix (#PR-NUMBER)

  ### Deprecated
  - Brief description of deprecated feature (#PR-NUMBER)
  ```

  **Example entry:**

  ```markdown
  ### Fixed
  - Resolve connection timeout when device is in deep sleep mode (#142)
  ```

- PRs will be validated by automated CI checks (linting, tests) before review

## Reporting Issues

Please use GitHub Issues to report bugs or request features.

### Bug Reports

Include the following information:

- Home Assistant version
- ESP-Weaver integration version
- ESP device model and firmware version
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (Settings → System → Logs)

### Feature Requests

Describe:

- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

## Code of Conduct

Please be respectful and constructive in all interactions. We're all here to learn and improve.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.

