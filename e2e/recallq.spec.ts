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
  await page.route("**/api/search", (r) => {
    expect(r.request().headers()["x-gemini-api-key"]).toBeUndefined();
    return r.fulfill({
      json: {
        query: "Kadane",
        corpus_revision: "initial",
        match_state: "matches",
        results: [source],
      },
    });
  });
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
test("search needs no key; summary requests one and temporary key clears on refresh", async ({
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
  await expect(
    page.getByRole("heading", { name: "Moments to revisit" }),
  ).toBeVisible();
  // A real video poster is present before playback, not a blank iframe.
  await expect(page.getByAltText("Selected lecture thumbnail")).toBeVisible();
  await expect(page.getByAltText("Selected lecture thumbnail")).toHaveAttribute(
    "src",
    /N8vJ8RyQEes/,
  );
  await expect(page.getByTitle("Selected lecture video")).toHaveCount(0);
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
  await page
    .getByLabel("Gemini API key", { exact: true })
    .fill("test-key-not-real");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
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

test("five focused results, real font, and persistent animated themes", async ({
  page,
}, info) => {
  await mock(page);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.route("**/api/search", (route) => {
    expect(route.request().postDataJSON().limit).toBe(5);
    return route.fulfill({
      json: {
        query: "Kadane",
        corpus_revision: "initial",
        match_state: "matches",
        // Oversized response checks the UI's defensive result cap as well.
        results: Array.from({ length: 7 }, (_, index) => ({
          ...source,
          source_id: source.source_id.slice(0, -1) + index,
          start_sec: 340 + index * 80,
          end_sec: 410 + index * 80,
        })),
      },
    });
  });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator(".tiny-line")).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  expect(
    await page.evaluate(() =>
      [...document.fonts].some(
        (font) => font.family.includes("DM Sans") && font.status === "loaded",
      ),
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: `work/ui/${info.project.name}-home-dark.png`,
    fullPage: true,
  });
  await page.getByRole("textbox", { name: "Search lectures" }).fill("Kadane");
  await page.getByRole("button", { name: "Find a moment" }).click();
  await expect(page.locator("article.result")).toHaveCount(5);
  await expect(page.getByRole("button", { name: /Show more/ })).toHaveCount(0);
  await expect(page.getByText("5 top matches", { exact: true })).toBeVisible();
  await page.screenshot({
    path: `work/ui/${info.project.name}-results-dark.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.evaluate(() => {
    const native = document.startViewTransition.bind(document);
    document.startViewTransition = (update) => {
      document.documentElement.dataset.transitionTested = "yes";
      return native(update);
    };
  });
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator("html")).toHaveAttribute(
    "data-transition-tested",
    "yes",
  );
  await expect(page.locator("html")).not.toHaveClass(/theme-transition/);
  await page.screenshot({
    path: `work/ui/${info.project.name}-results-light.png`,
    fullPage: true,
  });
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator("html")).toHaveCSS(
    "background-color",
    "rgb(247, 244, 237)",
  );
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).not.toHaveClass(/theme-transition/);
  await page.setViewportSize({ width: 320, height: 760 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("reduced motion and blocked storage keep theme switching usable", async ({
  page,
}) => {
  await mock(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    Storage.prototype.setItem = () => {
      throw new Error("Storage unavailable");
    };
    document.startViewTransition = () => {
      throw new Error("Reduced motion must bypass animation");
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator(".theme-icon")).toHaveCSS("animation-name", "none");
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
