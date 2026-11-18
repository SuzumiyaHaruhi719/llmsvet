# Contributing to llmsvet

Thank you for helping improve `llmsvet`.

## Development Principles

- **Zero runtime dependencies**: `src/llmsvet/` uses only the Python 3.9+ standard library so CI pipelines can run `llmsvet` anywhere without dependency conflicts.
- **Deterministic checks**: Every score deduction must map to a documented rule ID (`ROB-*`, `LLM-*`, `SCH-*`) with a concrete remediation hint.

## Running Tests Locally (Containerized)

```bash
docker run --rm --network none -v "$PWD":/src -w /src python:3.12-slim   env PYTHONPATH=/src/src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

## Submitting a Pull Request

1. Open or reference an issue describing the bug or enhancement.
2. Add unit tests under `tests/` covering both the failing input and the expected pass state.
3. Verify the test suite passes on Python 3.9+.
