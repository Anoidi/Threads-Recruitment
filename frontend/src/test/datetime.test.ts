import { describe, expect, it } from "vitest";
import {
  fromWarsawDateTimeInput,
  parseApiUtc,
  scheduleInputDefaults,
  toWarsawDateTimeInput,
} from "../utils/datetime";

describe("Europe/Warsaw date-time conversion", () => {
  it("treats API timestamps without an offset as UTC", () => {
    expect(parseApiUtc("2026-10-08T07:00:00").toISOString()).toBe("2026-10-08T07:00:00.000Z");
  });

  it("renders UTC in Warsaw with the correct summer and winter offset", () => {
    expect(toWarsawDateTimeInput("2026-07-01T10:00:00Z")).toBe("2026-07-01T12:00");
    expect(toWarsawDateTimeInput("2026-12-01T10:00:00Z")).toBe("2026-12-01T11:00");
  });

  it("converts Warsaw date-time input back to UTC across DST", () => {
    expect(fromWarsawDateTimeInput("2026-07-01T12:00")).toBe("2026-07-01T10:00:00.000Z");
    expect(fromWarsawDateTimeInput("2026-12-01T11:00")).toBe("2026-12-01T10:00:00.000Z");
  });

  it("builds both the minimum and default schedule values in Warsaw time", () => {
    expect(scheduleInputDefaults(new Date("2026-10-08T07:00:37Z"))).toEqual({
      min: "2026-10-08T09:00",
      defaultValue: "2026-10-09T09:00",
    });
  });
});
