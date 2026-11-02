import { describe, expect, it } from "vitest";
import { mapAnalytics } from "../api/client";
import type { Analytics } from "../types";

// The live API path used to drop `editorial_analysis`, so the analytics page
// never showed the comparable sample or the "insufficient data" verdict.
describe("live API mapping", () => {
  it("keeps the editorial analysis returned by the backend", () => {
    const mapped = mapAnalytics({
      range: "все сохранённые данные",
      sample_size: 18,
      segments: [{ content_type: "FAQ", language: "uk", posts: 3, engagement: 4.2 }],
      coverage: { published_posts: 18, posts_with_metrics: 18, posts_metrics_unavailable: 2, public_threads: "unavailable", public_topics: 0 },
      editorial_analysis: {
        comparable_posts: 2,
        insufficient_data: true,
        cohorts: [{ content_type: "FAQ", language: "uk", age_bucket: "24–48h", posts: 2, engagement_rate: 5, evidence: "limited" }],
        limitations: ["Сигналы кандидатов не равны найму."],
      },
    }) as Analytics;

    expect(mapped.editorial_analysis?.comparable_posts).toBe(2);
    expect(mapped.editorial_analysis?.insufficient_data).toBe(true);
    expect(mapped.coverage.posts_metrics_unavailable).toBe(2);
    expect(mapped.by_content_type[0].name).toBe("FAQ · UK");
  });

  it("survives a response without editorial analysis", () => {
    const mapped = mapAnalytics({ sample_size: 0 });
    expect(mapped.editorial_analysis).toBeUndefined();
    expect(mapped.coverage.public_threads).toBe("no_recent_data");
  });
});
