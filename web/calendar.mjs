export function localDayKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-");
}

export function lastLocalDays(today = new Date(), count = 21) {
  return Array.from({length: count}, (_, index) => {
    const date = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    date.setDate(date.getDate() - (count - 1 - index));
    return date;
  });
}

export function completedDayKeys(history) {
  return new Set(history.filter(item => item.status === "completed" && item.ended_at && item.answered > 0)
    .map(item => localDayKey(item.ended_at)));
}

export function scrollDelta(event, element) {
  if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return 0;
  const unit = event.deltaMode === 1 ? 18 : event.deltaMode === 2 ? element.clientWidth : 1;
  const delta = event.deltaY * unit;
  const max = element.scrollWidth - element.clientWidth;
  return (delta > 0 && element.scrollLeft < max - 1) || (delta < 0 && element.scrollLeft > 1) ? delta : 0;
}
