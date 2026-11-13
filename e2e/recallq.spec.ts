import { test, expect, type Page } from "@playwright/test";
const source = {
  source_id: "f5c3d89a-4a82-4bae-a97a-c3cbeac7a837",
  video_id: "N8vJ8RyQEes",
  video_title: "Kadane’s Algorithm: Introduction & Intuition",
  start_sec: 340,
  end_sec: 410,
  playback_sec: 335,
  excerpt: "Keep the best sum ending at the current position.",
  watch_url: "https://www.youtube.com/watch?v=N8vJ8RyQEes&t=335s",
};
async function mock(page: Page) {
  await page.route("**/api/corpus", (r) =>
    r.fulfill({
      json: {
        title: "DSA",
        indexed_videos: 3,
        total_videos: 125,
        quality_status: "Audio verification pending.",
        index_connected: true,
      },
    }),
  );
  await page.route("**/api/search", (r) =>
    r.fulfill({
      json: {
        query: "Kadane",
        corpus_revision: "initial",
        match_state: "matches",
        results: [source],
      },
    }),
  );
  await page.route("**/www.youtube.com/**", (r) =>
    r.fulfill({
      body: "<html><body>Player placeholder for automated UI testing</body></html>",
      contentType: "text/html",
    }),
  );
}
async function connect(page: Page, remember = false) {
  await page.getByRole("button", { name: "Connect Gemini" }).click();
  await page
    .getByLabel("Gemini API key", { exact: true })
    .fill("test-key-not-real");
  if (remember) await page.getByLabel("Remember on this device").check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
}
test("temporary key clears on refresh; search and summary are independent", async ({
  page,
}) => {
  await mock(page);
  let summaries = 0;
  await page.route("**/api/summarize", (r) => {
    summaries++;
    return r.fulfill({
      json: {
        status: "answered",
        summary: "Keep track of the best ending sum [1].",
        citations: [{ ...source, number: 1 }],
      },
    });
  });
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search lectures" }).fill("Kadane");
  await page.getByRole("button", { name: "Find a moment" }).click();
  await page
    .getByLabel("Gemini API key", { exact: true })
    .fill("test-key-not-real");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Moments to revisit" }),
  ).toBeVisible();
  // The native player is already present before any result is played.
  await expect(page.getByTitle("Selected lecture video")).toBeVisible();
  await expect(page.getByTitle("Selected lecture video")).toHaveAttribute(
    "src",
    /autoplay=0/,
  );
  expect(summaries).toBe(0);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  await page.getByRole("button", { name: /Play Kadane/ }).click();
  await expect(page.getByTitle("Selected lecture video")).toHaveAttribute(
    "src",
    /start=335/,
  );
  await expect(page.getByTitle("Selected lecture video")).toHaveAttribute(
    "src",
    /autoplay=1/,
  );
  await page.getByRole("button", { name: "Summarize these moments" }).click();
  await expect(
    page.getByRole("button", { name: "Play source 1" }),
  ).toBeVisible();
  expect(summaries).toBe(1);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Connect Gemini" }),
  ).toBeVisible();
});
test("remembered key persists and Forget key removes it", async ({ page }) => {
  await mock(page);
  await page.goto("/");
  await connect(page, true);
  await page.reload();
  await page.getByRole("button", { name: "Key connected" }).click();
  await page.getByRole("button", { name: "Forget key" }).click();
  expect(
    await page.evaluate(() => localStorage.getItem("recallq.gemini-key")),
  ).toBeNull();
  await expect(
    page.getByRole("button", { name: "Connect Gemini" }),
  ).toBeVisible();
});
test("quota failure preserves results and mobile has no overflow", async ({
  page,
}) => {
  await mock(page);
  await page.route("**/api/summarize", (r) =>
    r.fulfill({
      status: 429,
      json: {
        error: {
          code: "GEMINI_QUOTA",
          message: "Your Gemini quota is unavailable.",
        },
      },
    }),
  );
  await page.goto("/");
  await connect(page);
  await page.getByRole("textbox", { name: "Search lectures" }).fill("Kadane");
  await page.getByRole("button", { name: "Find a moment" }).click();
  await page.getByRole("button", { name: "Summarize these moments" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "Your Gemini quota is unavailable.",
  );
  await expect(
    page.getByRole("heading", { name: "Moments to revisit" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
