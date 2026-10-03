// GET /api/blocked?district=17&trek=85 -> ["02-08-2026", ...]
import { handle, check, isId, blockedDates } from "../lib/aranya.js";

export function GET(request) {
  return handle(request, 3600, ({ district, trek }) => {
    check(isId(district) && isId(trek), "bad district/trek");
    return blockedDates(district, trek);
  });
}
