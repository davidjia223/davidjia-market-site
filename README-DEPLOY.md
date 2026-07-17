# davidjia.ca — Protective Put Simulator with automatic EOD data

Fully automatic, $0/month pipeline:

```
GitHub Actions (cron, Mon–Fri 21:45 UTC, after US close)
   └─ fetch_market_data.py  → pulls from free, no-API-key sources:
        SPY history ......... Stooq  (fallback: FRED SP500 ÷ 10)
        VIX, VIX9D/3M/6M .... CBOE official CSVs (fallback: FRED VIXCLS)
        3-mo T-bill, 10Y .... FRED
   └─ commits site/data/market.json to this repo
   └─ (optional) deploys site/ to your AWS server
Site (index.html) loads whichever copy of market.json is newest:
   your server's file, or the GitHub raw copy — with a labeled
   bundled fallback if both fail.
```

---

## Setup (about 15 minutes)

### 1. Create the repo and push these files
- Make a **public** GitHub repo (public = the site can read
  `raw.githubusercontent.com` with no credentials, and Actions minutes are
  unlimited; a private repo also works if you use deploy mode s3/ssh below).
- Push everything in this folder, keeping the structure:
  `fetch_market_data.py`, `.github/workflows/update-market-data.yml`, `site/`.

### 2. Run the workflow once by hand
GitHub → your repo → **Actions** → "EOD market data update" → **Run workflow**.
This generates the first `site/data/market.json`. After this it runs itself
every weekday evening. (GitHub cron can start a few minutes late — harmless.)

### 3. Point the site at your repo
In `site/index.html`, replace on the `GITHUB_RAW_DATA_URL` line:
`YOUR_GITHUB_USERNAME/YOUR_REPO_NAME` → your actual `username/repo`.
This is the only edit the site needs.

### 4. Pick ONE deploy mode

**Mode 0 — no AWS credentials in GitHub (simplest).**
Upload `site/` to your server once (see nginx notes below). Done. The page
fetches fresh data from GitHub raw every visit, so it stays current even
though you never redeploy. Requires the repo to be public.

**Mode S3 — site hosted on S3 (+ CloudFront).**
Repo → Settings → Secrets and variables → Actions:
- *Variables:* `DEPLOY_MODE` = `s3`, `AWS_REGION`, `S3_BUCKET`, and
  optionally `CLOUDFRONT_ID`.
- *Secrets:* `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` for an IAM user
  with this minimal policy (replace BUCKET):

```json
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::BUCKET"},
  {"Effect":"Allow","Action":["s3:PutObject","s3:DeleteObject","s3:GetObject"],
   "Resource":"arn:aws:s3:::BUCKET/*"},
  {"Effect":"Allow","Action":["cloudfront:CreateInvalidation"],"Resource":"*"}]}
```

**Mode SSH — site on an EC2 / Lightsail box (nginx or apache).**
- On the server: `ssh-keygen -t ed25519 -f deploykey` (no passphrase), append
  `deploykey.pub` to `~/.ssh/authorized_keys` of the deploy user, make sure
  the user can write the web root.
- Repo variables: `DEPLOY_MODE` = `ssh`, optional `DEPLOY_PATH`
  (default `/var/www/davidjia.ca`).
- Repo secrets: `SSH_HOST` (server IP or hostname), `SSH_USER`,
  `SSH_PRIVATE_KEY` (contents of the private `deploykey` file).

### 5. Serve it at davidjia.ca
EC2/nginx example:

```nginx
server {
  listen 80;
  server_name davidjia.ca www.davidjia.ca;
  root /var/www/davidjia.ca;
  index index.html;
  location /data/ { add_header Cache-Control "no-store"; }
}
```

Then `sudo certbot --nginx -d davidjia.ca -d www.davidjia.ca` for free HTTPS,
and a Route 53 / registrar **A record** pointing at your Elastic IP
(or the CloudFront distribution if you chose S3).

---

## Test locally

```bash
python3 fetch_market_data.py          # writes site/data/market.json
cd site && python3 -m http.server 8000
# open http://localhost:8000 — header should read "server file · live EOD"
```

## Maintenance notes
- **Weekends/holidays** show the last trading day's close — expected for EOD.
- **Dividend yield** is a constant (`divYield` in `fetch_market_data.py`,
  currently 1.0%). Glance at it once or twice a year.
- **Resilience:** every source has a fallback, and the site itself falls back
  gracefully with a visible label, so a broken feed never blanks the page.
- **Cost:** $0. Public-repo Actions are free (this job uses ~1 min/day);
  Stooq, CBOE, and FRED are free with no keys; you already pay for the server.
- The Action commits a small JSON daily, so the repo history grows slowly —
  that's normal and harmless.
