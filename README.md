# 📋 M365 Roadmap Tracker

Automated dashboard that tracks every Microsoft 365 roadmap change — with AI summaries, change detection, and smart filtering.

**Live at:** [aguidetocloud.com/m365-roadmap/](https://www.aguidetocloud.com/m365-roadmap/)

## What It Does

- 📡 Pulls from the [M365 Roadmap API](https://www.microsoft.com/releasecommunications/api/v1/m365) daily
- 🔄 Detects changes: new items, status changes, GA date moves
- 🤖 Generates plain-English AI summaries (GPT-4o mini)
- 📂 Groups 36 products into 11 clean categories
- 🔍 Fast search, status filters, product filters
- 📊 Daily / Weekly / Monthly digest tabs

## Architecture

```
M365 API → fetch_roadmap.py → summarise.py → generate_data.py → JSON
                                                                  │
GitHub Actions (daily cron) ──────────────────────────────────────┘
         │
         ▼
aguidetocloud-revamp/static/data/roadmap/ → Hugo + roadmap.js → /m365-roadmap/
```

## Pipeline Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fetch_roadmap.py` | Fetch API data, detect changes vs previous run |
| `scripts/summarise.py` | AI summaries for new/changed items (cached) |
| `scripts/generate_data.py` | Produce latest.json, weekly.json, monthly.json |
| `scripts/categories.json` | Product → UI category mapping |

## Running Locally

```bash
pip install -r requirements.txt
python scripts/fetch_roadmap.py
# For AI summaries, set AZURE_OPENAI_TOKEN and AZURE_OPENAI_ENDPOINT
python scripts/summarise.py
python scripts/generate_data.py
```

## Cost

~$0.10/month — only new/changed items get AI-summarized (cached).

## Part of

[A Guide to Cloud & AI](https://www.aguidetocloud.com) — Free Tools collection.
