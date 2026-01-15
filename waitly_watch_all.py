name: Waitly Watch

on:
  schedule:
    - cron: "0 * * * *"   # en gang i timen (UTC)
  workflow_dispatch:

jobs:
  run-watch:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout waitly-watch
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Cache pip
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt') }}
          restore-keys: |
            ${{ runner.os }}-pip-

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Install Playwright browsers
        run: |
          python -m playwright install --with-deps chromium

      - name: Run Waitly Watch
        env:
          WAITLY_SMTP_HOST: smtp.gmail.com
          WAITLY_SMTP_PORT: "587"
          WAITLY_SMTP_USER: ${{ secrets.WAITLY_SMTP_USER }}
          WAITLY_SMTP_PASS: ${{ secrets.WAITLY_SMTP_PASS }}
          WAITLY_MAIL_FROM: ${{ secrets.WAITLY_SMTP_USER }}

          # A) Login til my.waitly.dk (positions-scrape)
          WAITLY_LOGIN_EMAIL: ${{ secrets.WAITLY_LOGIN_EMAIL }}
          WAITLY_LOGIN_PASSWORD: ${{ secrets.WAITLY_LOGIN_PASSWORD }}
        run: |
          echo "LOGIN EMAIL SET? -> $([ -n "$WAITLY_LOGIN_EMAIL" ] && echo YES || echo NO)"
          echo "LOGIN PASS  SET? -> $([ -n "$WAITLY_LOGIN_PASSWORD" ] && echo YES || echo NO)"
          python waitly_watch_all.py
          echo "Repo root files after run:"
          ls -la
          echo "current.json exists?"
          test -f current.json && (echo "YES"; head -n 60 current.json) || echo "NO"

      - name: Checkout venteliste-dashboard
        uses: actions/checkout@v4
        with:
          repository: jeppenaeb/venteliste-dashboard
          token: ${{ secrets.DASHBOARD_PUSH_TOKEN }}
          path: dashboard
          persist-credentials: false

      - name: Copy current.json into dashboard repo (fallback to keep existing if missing)
        run: |
          mkdir -p dashboard/data
          if test -f current.json; then
            cp -f current.json dashboard/data/current.json
            echo "Copied current.json -> dashboard/data/current.json"
          else
            echo "WARNING: current.json missing; leaving dashboard/data/current.json unchanged"
          fi

      - name: Commit and push dashboard update
        env:
          DASHBOARD_PUSH_TOKEN: ${{ secrets.DASHBOARD_PUSH_TOKEN }}
        run: |
          cd dashboard

          # Ensure pushes use the PAT (classic token)
          git config --local --unset-all http.https://github.com/.extraheader || true
          git remote set-url origin "https://x-access-token:${DASHBOARD_PUSH_TOKEN}@github.com/jeppenaeb/venteliste-dashboard.git"

          if git status --porcelain | grep -q 'data/current.json'; then
            git config user.name "github-actions[bot]"
            git config user.email "github-actions[bot]@users.noreply.github.com"
            git add data/current.json
            git commit -m "Update data/current.json (Waitly Watch)"
            git push origin HEAD:main
          else
            echo "No changes to commit"
          fi
