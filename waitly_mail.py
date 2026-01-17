name: waitly-watch

on:
  workflow_dispatch:
  push:
    branches: ["main"]
    paths:
      - "**/*.py"
      - "requirements.txt"
      - ".github/workflows/waitly-watch.yml"
      - "state/**"
  schedule:
    # GitHub cron is UTC. 06:15 UTC ≈ 07:15 DK (vinter)
    - cron: "15 6 * * *"

concurrency:
  group: waitly-watch
  cancel-in-progress: true

permissions:
  contents: write

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      # Playwright is optional; install browser only if playwright is present
      - name: Install Playwright Chromium (no apt deps)
        run: |
          python - <<'PY'
          import importlib.util, sys
          if importlib.util.find_spec("playwright"):
              sys.exit(0)
          sys.exit(1)
          PY
          || exit 0
          python -m playwright install chromium

      - name: Run watcher
        env:
          WAITLY_LOGIN_EMAIL: ${{ secrets.WAITLY_LOGIN_EMAIL }}
          WAITLY_LOGIN_PASSWORD: ${{ secrets.WAITLY_LOGIN_PASSWORD }}
          WAITLY_SMTP_HOST: ${{ secrets.WAITLY_SMTP_HOST }}
          WAITLY_SMTP_PORT: ${{ secrets.WAITLY_SMTP_PORT }}
          WAITLY_SMTP_USER: ${{ secrets.WAITLY_SMTP_USER }}
          WAITLY_SMTP_PASS: ${{ secrets.WAITLY_SMTP_PASS }}
          WAITLY_MAIL_FROM: ${{ secrets.WAITLY_MAIL_FROM }}
          WAITLY_MAIL_TO: ${{ secrets.WAITLY_MAIL_TO }}
        run: |
          python waitly_watch_all.py

      - name: Show outputs (debug)
        if: always()
        run: |
          echo "==== sitemap_kbh present? ===="
          test -f current.json && grep -n "sitemap_kbh" current.json || echo "NO sitemap_kbh"
          echo "==== current.json (tail) ===="
          test -f current.json && tail -n 60 current.json || true
          echo "==== state/ ===="
          ls -la state || true

      - name: Commit updated state
        run: |
          set -e
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          # Always stage current.json
          git add current.json || true

          # Stage state/*.json only if files exist
          if ls state/*.json >/dev/null 2>&1; then
            git add state/*.json
          fi

          # Commit only if there are staged changes
          if git diff --cached --quiet; then
            echo "No changes to commit."
          else
            git commit -m "Update waitly state [skip ci]"
            git push
          fi
