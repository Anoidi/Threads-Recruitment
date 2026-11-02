export const WARSAW_TIME_ZONE = "Europe/Warsaw";

const OFFSET_SUFFIX = /(Z|[+-]\d{2}:?\d{2})$/i;

export function parseApiUtc(value: string): Date {
  const normalized = OFFSET_SUFFIX.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) throw new RangeError(`Invalid API date-time: ${value}`);
  return date;
}

function partsInWarsaw(value: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: WARSAW_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(value);
  const get = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find(part => part.type === type)?.value);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute"), second: get("second") };
}

const pad = (value: number) => String(value).padStart(2, "0");

export function toWarsawDateTimeInput(value: string | Date): string {
  const date = typeof value === "string" ? parseApiUtc(value) : value;
  const parts = partsInWarsaw(date);
  return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
}

function warsawOffsetMs(value: Date): number {
  const parts = partsInWarsaw(value);
  return Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second) - Math.floor(value.getTime() / 1000) * 1000;
}

export function fromWarsawDateTimeInput(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) throw new RangeError(`Invalid Warsaw date-time input: ${value}`);
  const [, year, month, day, hour, minute] = match.map(Number);
  const wallClockAsUtc = Date.UTC(year, month - 1, day, hour, minute);
  let instant = wallClockAsUtc;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    instant = wallClockAsUtc - warsawOffsetMs(new Date(instant));
  }
  return new Date(instant).toISOString();
}

export function formatWarsawDateTime(
  value?: string,
  options: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" },
): string {
  return value
    ? new Intl.DateTimeFormat("ru-RU", { ...options, timeZone: WARSAW_TIME_ZONE }).format(parseApiUtc(value))
    : "—";
}

export function scheduleInputDefaults(now = new Date()): { min: string; defaultValue: string } {
  return {
    min: toWarsawDateTimeInput(now),
    defaultValue: toWarsawDateTimeInput(new Date(now.getTime() + 86_400_000)),
  };
}
