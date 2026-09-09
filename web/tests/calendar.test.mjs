import assert from "node:assert/strict";
import {completedDayKeys, lastLocalDays, localDayKey, scrollDelta} from "../calendar.mjs";

assert.deepEqual(lastLocalDays(new Date(2026, 2, 1), 3).map(localDayKey), ["2026-02-27", "2026-02-28", "2026-03-01"]);
assert.equal(lastLocalDays(new Date(2026, 2, 29), 2)[1].getDate(), 29);
assert.deepEqual([...completedDayKeys([
  {status: "completed", ended_at: "2026-03-29T23:30:00Z", answered: 1},
  {status: "completed", ended_at: "2026-03-29T23:30:00Z", answered: 0},
  {status: "active", ended_at: "2026-03-29T23:30:00Z", answered: 2},
])], [localDayKey("2026-03-29T23:30:00Z")]);
const el = {clientWidth: 100, scrollWidth: 500, scrollLeft: 0};
assert.equal(scrollDelta({deltaX: 0, deltaY: -10, deltaMode: 0}, el), 0);
assert.equal(scrollDelta({deltaX: 0, deltaY: 2, deltaMode: 1}, el), 36);
console.log("Calendar helpers: local days, month crossing, completion evidence and wheel bounds passed");
