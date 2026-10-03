# Aranya Vihaara Trek Map (Karnataka)

Made by [Atharva Modi](https://github.com/modi-fied).

**This is not a booking website.** Select your dates to check availability, then book on the official Aranya Vihaara site.
Only treks whose tickets are sold online by the Karnataka Forest Department are shown.

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

The booking site only accepts connections from Indian IP addresses, so the live parts run on Vercel's Mumbai region.

| Part | Where it lives | What it does |
|---|---|---|
| Map page | `docs/index.html`, served by GitHub Pages | The page people use. The local app and Vercel serve the same file. |
| Live data | `api/` + `lib/aranya.js`, on Vercel (free, Mumbai region set in `vercel.json`) | Trek list, closed dates and seat counts. A page on GitHub Pages can't call the booking site directly. |
| Saved trek list | `docs/treks.json` | Backup used if the live trek list can't be loaded. Refresh it with `python trek_map.py --export docs/treks.json`. |

**One-time setup:**

1. **Vercel:** sign in at https://vercel.com with GitHub, then **Add New → Project**, import this repo, and click **Deploy** (no settings to change). Vercel redeploys on every push. The project's own `*.vercel.app` address also shows the full working map.
2. **Point the page at Vercel:** in `docs/index.html`, set `BACKEND_URL` to the project's `*.vercel.app` address. Commit and push.
3. **GitHub Pages:** on GitHub, open **Settings → Pages**. Under *Build and deployment*, choose **Deploy from a branch**, branch **main**, folder **/docs**, then **Save**. After a minute or two the site appears at the address above.

If you move the page to another address, add that address to `ALLOWED_ORIGINS` in `lib/aranya.js`.
