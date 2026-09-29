# Aranya Vihaara Trek Map (Karnataka)

- Shows every open trek from the [Aranya Vihaara](https://aranyavihaara.karnataka.gov.in) website on a map.
- Pick your dates and number of people, and each trek is coloured by whether tickets are available.
- It only looks at availability. It does not book anything or ask for your personal details.

## How to run it on Windows

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
