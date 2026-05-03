IMAGE_NAME := "ductor"

# Development task runner for ductor
# Requires: just (https://github.com/casey/just)

default:
    @just --list

# Auto-fix formatting and lint issues
fix:
    uv run ruff format .
    uv run ruff check --fix .

# Run all linters, type checks, i18n completeness, and tests
check: _lint _format _types _i18n _test

# Run the test suite sequentially (safe default; see `test-parallel` for opt-in)
test *args:
    uv run pytest {{args}}

# Run the test suite in parallel via pytest-xdist (opt-in; not verified parallel-safe across all 2246+ tests)
test-parallel *args:
    uv run pytest -n auto {{args}}

build tag="latest":
    docker build -t {{IMAGE_NAME}}:{{tag}} .

run:
    docker build -t {{IMAGE_NAME}}:local .
    docker run --rm --network host --env-file .env {{IMAGE_NAME}}:local

shell:
    docker-compose exec {{IMAGE_NAME}} sh

clean-docker:
    docker system prune -f
    docker volume prune -f

push-gitdocker tag="latest":
    docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) -t {{IMAGE_NAME}}:{{tag}} .
    docker tag {{IMAGE_NAME}}:{{tag}} ghcr.io/montelibero/{{IMAGE_NAME}}:{{tag}}
    docker push ghcr.io/montelibero/{{IMAGE_NAME}}:{{tag}}

[private]
_lint:
    uv run ruff check .

[private]
_format:
    uv run ruff format --check .

[private]
_types:
    uv run mypy ductor_bot

[private]
_i18n:
    uv run python -m ductor_bot.i18n.check --quiet

[private]
_test:
    uv run pytest
