# Aranya Vihaara Trek Map (Karnataka)

- Shows every open trek from the [Aranya Vihaara](https://aranyavihaara.karnataka.gov.in) website on a map.
- Pick your dates and number of people, and each trek is coloured by whether tickets are available.
- It only looks at availability. It does not book anything or ask for your personal details.
- This is an unofficial tool and is not affiliated with the Karnataka Forest Department.

## Use it online

Open **https://modi-fied.github.io/AranyaVihaaraTrekking_Karnataka/** in any browser, on a phone or a computer. Nothing to install.

## Or run it on your own Windows computer

### Step 1: Install Python (one time only)

- Go to https://www.python.org/downloads/ and click the big yellow **Download Python** button.
- Open the downloaded file.
- On the first screen, **tick the box "Add python.exe to PATH"** at the bottom. This is important.
- Click **Install Now** and wait for it to finish.

### Step 2: Download this project

- On this GitHub page, click the green **Code** button, then **Download ZIP**.
- Right-click the downloaded ZIP file and choose **Extract All**.

### Step 3: Start the map

- Open the extracted folder.
- Double-click **Start_Trek_Map.bat**.
- A black window opens, and the map opens in your web browser after a few seconds.
- The first start takes a little longer while it collects the list of treks.
- **Keep the black window open** while you use the map. Closing it stops the map.

### Step 4: When you are done

- Close the black window (or press **Ctrl + C** inside it).

## If something goes wrong

- **Black window flashes and closes, or says "python is not recognized":** Python is not installed properly. Do Step 1 again and make sure the "Add python.exe to PATH" box is ticked.
- **Windows shows a blue "Windows protected your PC" box:** click **More info**, then **Run anyway**.
- **Map does not open by itself:** look in the black window for a web address like `http://127.0.0.1:8765/` and type it into your browser.
- **Map shows an error about reaching the site:** check your internet connection. The Aranya Vihaara website may also be down for a while, so try again later.

## Setting up the website (maintainer notes)

The website has three parts:

| Part | Where it lives | What it does |
|---|---|---|
| Map page | `docs/index.html`, served by GitHub Pages | The page people use. The local app serves the same file. |
| Trek list | `docs/treks.json` | List of open treks with map coordinates. A GitHub Action (`.github/workflows/refresh-treks.yml`) refreshes it every day. |
| Live seat checks | `worker/worker.js` + `wrangler.toml`, on Cloudflare Workers (free) | Talks to the booking site for the page. A page on GitHub Pages can't call the booking site directly. |

**One-time setup:**

1. **Cloudflare Worker:** in the Cloudflare dashboard, create a Worker connected to this GitHub repo. Cloudflare reads `wrangler.toml` and redeploys `worker/worker.js` on every push. The Worker name in `wrangler.toml` must match the name in Cloudflare.
2. **Point the page at the Worker:** in `docs/index.html`, set `WORKER_URL` to the Worker's `*.workers.dev` address. Commit and push.
3. **GitHub Pages:** on GitHub, open **Settings → Pages**. Under *Build and deployment*, choose **Deploy from a branch**, branch **main**, folder **/docs**, then **Save**. After a minute or two the site appears at the address above.
4. **Trek list refresh:** on GitHub, open **Actions → Refresh trek list → Run workflow** once to check that it works. If it fails because the booking site blocks GitHub's servers, refresh the list from your own computer instead:
   `python trek_map.py --export docs/treks.json`, then commit and push.

If you move the site to another address, add that address to `ALLOWED_ORIGINS` in `worker/worker.js` and deploy the Worker again.
