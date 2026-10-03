// GET /api/availability?district=17&trek=85&date=dd-mm-yyyy -> {slots: [...], max_per_booking}
import { handle, check, isId, availability } from "../lib/aranya.js";

export function GET(request) {
  return handle(request, 120, ({ district, trek, date }) => {
    check(isId(district) && isId(trek), "bad district/trek");
    check(/^\d\d-\d\d-\d{4}$/.test(date), "bad date");
    return availability(district, trek, date);
  });
}
