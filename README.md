# Oopsie

Checks deal sites in the UK, Europe and Switzerland every 15 minutes, picks out likely pricing mistakes, shows them on your own web page and pings your phone. Free to run.

## How it works

1. Every 15 minutes GitHub runs `radar.py` for you (no computer needed).
2. It reads the newest posts from HotUKDeals, mydealz, Dealabs, Chollometro, Pepper Italia, Pepper NL and Preispirat.
3. Each post gets a score from 0 to 100. Posts that say "price error", "Preisfehler", "error de precio" and so on score high. Very big discounts and very hot deals add points.
4. Results go into `docs/deals.json`, and your dashboard page shows them.
5. Anything scoring 60+ is sent to your phone through the ntfy app.

## Setup (about 15 minutes)

**1. Make a GitHub account** at github.com if you don't have one.

**2. Create a new repository**
- Click **+** → **New repository**, name it `price-error-radar`, set it to **Public** (needed for the free web page), and create it.
- Click **uploading an existing file**, drag in everything from this folder, and commit.
- The `.github` folder is hidden on Mac. In Finder press `Cmd + Shift + .` to show it, then drag it in too.

**3. Turn on the web page**
- In the repo go to **Settings → Pages**.
- Under *Branch* pick `main` and folder `/docs`, then **Save**.
- After a minute your dashboard is live at `https://YOUR-USERNAME.github.io/price-error-radar/`

**4. Turn on the 15-minute checks**
- Go to the **Actions** tab and click **I understand my workflows, enable them** if asked.
- Click **Oopsie → Run workflow** once to start it straight away.

**5. Phone alerts**
- Install **ntfy** (App Store or Google Play).
- Tap **+**, subscribe to a topic with a long secret name, for example `ksyusha-radar-8f3k2q`.
- In `config.yaml` replace `CHANGE-ME-to-a-secret-name` with that exact name and commit.
- Anyone who knows the name can read your alerts, so keep it hard to guess.

## Making it yours

Everything lives in `config.yaml`. You can edit it right on GitHub (click the file, then the pencil icon). Things you can change:

| Setting | What it does |
|---|---|
| `sources` | Turn sites on/off or add any other RSS feed |
| `error_keywords` | Words that mark a price error, in any language |
| `min_discount_percent` | Discount that counts as suspicious (default 70%) |
| `min_score` | How strict the dashboard is. 60 = only clear errors, 30 = more deals |
| `watchlist` | Products you always want to see, with an optional max price |
| `block_keywords` | Words that hide a deal |
| `alerts.min_score` | How strict phone alerts are |
| `keep_days` | How long deals stay on the page |

The look of the page is in `docs/index.html`.

## Good to know

- Shops often cancel orders at an error price. A deal only counts once it ships.
- GitHub sometimes runs scheduled jobs a few minutes late at busy times.
- GitHub pauses scheduled jobs in repos with no activity for 60 days. The bot's own commits normally count as activity, but if it ever stops, press **Run workflow** again.
- If a site blocks the checker, the page shows a yellow note and the other sites keep working. The Actions log shows which feed failed.
