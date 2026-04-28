# Default: list recipes
default:
    @just --list

# Install everything (Python workspace + Node workspace)
install:
    uv sync --all-groups
    pnpm install

# Python: format + lint (auto-fix)
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Python: lint check only (CI)
lint:
    uv run ruff check .
    uv run ruff format --check .

# Python: typecheck
typecheck:
    uv run mypy packages apps/pipeline

# Python: tests
test:
    uv run pytest

# All CI checks (Python)
ci: lint typecheck test

# Scrape PDFs for one company (sap | asml | dassault)
scrape COMPANY:
    uv run report-scrape scrape {{COMPANY}}

# Scrape all configured companies
scrape-all:
    just scrape sap
    just scrape asml
    just scrape dassault

# Classify downloaded PDFs (annual / quarterly / other)
classify COMPANY:
    uv run report-scrape classify {{COMPANY}}

# Parse annual reports for statements (IS / BS / CF)
parse COMPANY:
    uv run report-scrape parse {{COMPANY}}

# AI-validate extracted rows (drop noise, flag wrong-kind tables, attach hierarchy)
confirm COMPANY:
    uv run report-scrape confirm {{COMPANY}}

# Normalize line items (canonical mapping + hierarchy)
normalize COMPANY:
    uv run report-scrape normalize {{COMPANY}}

# Consolidate into "latest restated" view across all reports
build:
    uv run report-scrape build

# Export JSON for the Next.js viewer
export:
    uv run report-scrape export

# Full pipeline for one company
pipeline COMPANY:
    just scrape {{COMPANY}}
    just classify {{COMPANY}}
    just parse {{COMPANY}}
    just confirm {{COMPANY}}
    just normalize {{COMPANY}}

# Start the Next.js viewer in dev mode
web:
    pnpm --filter @report-scrape/web dev

# Build the Next.js viewer (for Cloudflare Pages / Vercel)
web-build:
    pnpm --filter @report-scrape/web build

# Typecheck + lint the web app
web-check:
    pnpm --filter @report-scrape/web lint
    pnpm --filter @report-scrape/web typecheck
