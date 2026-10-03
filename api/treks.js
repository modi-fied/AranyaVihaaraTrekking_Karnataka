// GET /api/treks -> list of open treks with start coordinates.
// Takes a few seconds to build, so Vercel's CDN keeps it for 12 hours.
import { handle, loadTreks } from "../lib/aranya.js";

export const maxDuration = 60;

export function GET(request) {
  return handle(request, 12 * 3600, async () => {
    const treks = await loadTreks();
    if (!treks.length) throw new Error("no treks found");
    return treks;
  });
}
