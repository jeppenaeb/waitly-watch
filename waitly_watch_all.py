#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Waitly Watch (sitemap + openings) + Dashboard export + Watchlist cleanup

Funktioner:
1) NYE SIDER:
   - Henter https://waitly.eu/da/sitemap (HTML)
   - Finder nye links der matcher områder (Kbh K/V/N/Ø/S + Frederiksberg (+ -c))
   - Notifier via mail med 🆕 [Waitly] NY SIDE

2) ÅBNINGER:
   - Læser watch_urls.txt (én Waitly-URL pr linje)
   - Checker om siden er åben ved at finde et "Tilmeld"-link til app.waitly.*
   - Notifier KUN ved transition lukket -> åben (ingen spam på første run)
   - Notifier via mail med 🚨 [Waitly] ÅBNING
   - Auto-fjerner 404/410 URL'er fra watch_urls.txt (bevarer kommentarer/blanke linjer)

3) DASHBOARD (A):
   - Logger ind på my.waitly.dk via Playwright (kræver env)
   - Henter dine kø-positioner (heuristik på JSON responses)
   - Skriver current.json i repo-roden (workflow kopierer til venteliste-dashboard/data/current.json)

State:
- Gemmer baseline + sidestatus i waitly_watch_state.json

Afhængigheder:
  pip install requests beautifulsoup4 playwright

SMTP (via env vars):
  WAITLY_SMTP_HOST=smtp.gmail.com
  WAITLY_SMTP_PORT=587
  WAIT
