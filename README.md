# Amr Kamal Jobs Dashboard

Encrypted daily dashboard for Flutter/mobile jobs matched to Amr's CV.

## Sources

- JSearch via RapidAPI for LinkedIn, Indeed, and Glassdoor results.
- Remotive for remote Flutter/mobile jobs.
- Arbeitnow for EU/Germany mobile jobs.
- Safe direct search cards for LinkedIn Jobs, LinkedIn feed/content search, Bayt, NaukriGulf, and GulfTalent.
- Email-only MENA/Gulf leads for cold outreach.

LinkedIn is not scraped directly. The dashboard uses JSearch for job results and direct LinkedIn search links for feeds/jobs, which avoids login, bot checks, and brittle page scraping.

## GitHub Secrets

Set these in repository secrets:

- `DASHBOARD_PASSWORD`
- `JSEARCH_API_KEY`

Without `JSEARCH_API_KEY`, the dashboard still builds using Remotive, Arbeitnow, Gulf search links, and email leads.

## Local Run

```bash
python3 scripts/daily_refresh.py
```

If local Python cannot find `cryptography`, install it in your environment:

```bash
python3 -m pip install cryptography certifi
```
