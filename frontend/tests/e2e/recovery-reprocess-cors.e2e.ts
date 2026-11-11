import {
  E2E_API_BASE_URL,
  ManagedBackendServer,
  createTempWorkDir,
  removeTempWorkDir,
  seedRecoveryState,
  type SeededRecoveryIds,
} from "./helpers/backend";
import { expect, gotoDashboard, test } from "./helpers/test-fixtures";

test.describe("Startup Recovery, UI Reprocess, and Live CORS", () => {
  let workDir = "";
  let seededIds: SeededRecoveryIds;
  let backend: ManagedBackendServer;

  test.beforeEach(async () => {
    workDir = createTempWorkDir("docsense-pw-recovery-");
    seededIds = seedRecoveryState(workDir);
    backend = new ManagedBackendServer(workDir);
    await backend.start();
  });

  test.afterEach(async () => {
    await backend.stop();
    removeTempWorkDir(workDir);
  });

  test("startup recovery reconciles seeded rows, UI reprocess settles to Ready, and CORS headers match", async ({
    page,
    consoleGuard,
  }) => {
    const observedCorsHeaders: Array<{
      url: string;
      method: string;
      allowOrigin: string | undefined;
      allowCredentials: string | undefined;
    }> = [];

    page.on("response", (response) => {
      if (response.url().startsWith(E2E_API_BASE_URL)) {
        const headers = response.headers();
        observedCorsHeaders.push({
          url: response.url(),
          method: response.request().method(),
          allowOrigin: headers["access-control-allow-origin"],
          allowCredentials: headers["access-control-allow-credentials"],
        });
      }
    });

    await gotoDashboard(page);
    await expect(page.getByText("API OK")).toBeVisible();

    // Deleting row must be absent; ready, pending, and failed processing rows present (3 docs total)
    await expect(page.getByText("3 documents")).toBeVisible();
    await expect(
      page.getByRole("article", { name: "seeded-deleting.pdf" }),
    ).toHaveCount(0);

    const readyCard = page.getByRole("article", { name: "seeded-ready.pdf" });
    await expect(readyCard).toBeVisible();
    await expect(readyCard.getByText("Ready", { exact: true })).toBeVisible();

    const pendingCard = page.getByRole("article", { name: "seeded-pending.pdf" });
    await expect(pendingCard).toBeVisible();
    await expect(pendingCard.getByText("Ready", { exact: true })).toBeVisible({
      timeout: 30_000,
    });

    const procCard = page.getByRole("article", { name: "seeded-processing.pdf" });
    await expect(procCard).toBeVisible();
    await expect(procCard.getByText("Failed", { exact: true })).toBeVisible();
    await expect(procCard.getByRole("note")).toContainText(
      "Ingestion interrupted by server restart. Please retry reprocessing.",
    );

    // Click Reprocess in UI -> 202 -> settles cleanly to Ready
    const reprocess202Promise = page.waitForResponse(
      (res) =>
        res.url() ===
          `${E2E_API_BASE_URL}/api/v1/documents/${seededIds.processing_id}/reprocess` &&
        res.request().method() === "POST",
    );
    await procCard
      .getByRole("button", { name: "Reprocess seeded-processing.pdf" })
      .click();

    const reprocessResp = await reprocess202Promise;
    expect(reprocessResp.status()).toBe(202);

    await expect(procCard.getByText("Ready", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    const procChunksDd = procCard
      .locator("dt", { hasText: /^Chunks$/ })
      .locator("xpath=following-sibling::dd[1]");
    expect(Number((await procChunksDd.innerText()).trim())).toBeGreaterThan(0);

    // Verify live cross-origin headers on observed API requests
    expect(observedCorsHeaders.length).toBeGreaterThanOrEqual(3);
    const hasHealthCors = observedCorsHeaders.some(
      (item) =>
        item.method === "GET" &&
        item.url.endsWith("/api/v1/health") &&
        item.allowOrigin === "http://localhost:3100" &&
        item.allowCredentials === "true",
    );
    const hasListCors = observedCorsHeaders.some(
      (item) =>
        item.method === "GET" &&
        item.url.includes("/api/v1/documents") &&
        item.allowOrigin === "http://localhost:3100" &&
        item.allowCredentials === "true",
    );
    const hasReprocessCors = observedCorsHeaders.some(
      (item) =>
        item.method === "POST" &&
        item.url.endsWith(`/api/v1/documents/${seededIds.processing_id}/reprocess`) &&
        item.allowOrigin === "http://localhost:3100" &&
        item.allowCredentials === "true",
    );

    expect(hasHealthCors).toBe(true);
    expect(hasListCors).toBe(true);
    expect(hasReprocessCors).toBe(true);

    consoleGuard.assertClean();
  });
});
