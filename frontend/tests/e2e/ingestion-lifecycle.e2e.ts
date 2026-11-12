import fs from "node:fs";
import path from "node:path";
import {
  E2E_API_BASE_URL,
  ManagedBackendServer,
  createTempWorkDir,
  generatePdfFixtures,
  removeTempWorkDir,
  verifyDocumentDeleted,
  type FixtureManifest,
} from "./helpers/backend";
import { expect, gotoDashboard, test } from "./helpers/test-fixtures";

test.describe("Ingestion Lifecycle, Pagination, Filters, and Delete", () => {
  let workDir = "";
  let fixturesDir = "";
  let fixtures: FixtureManifest;
  let backend: ManagedBackendServer;

  test.beforeEach(async () => {
    workDir = createTempWorkDir("docsense-pw-lifecycle-");
    fixturesDir = path.join(workDir, "fixtures");
    fixtures = generatePdfFixtures(fixturesDir);
    backend = new ManagedBackendServer(workDir);
    await backend.start();
  });

  test.afterEach(async () => {
    await backend.stop();
    removeTempWorkDir(workDir);
  });

  test("file picker, drag-drop, polling settlement, scanned failure, 21-doc pagination, filters, and keyboard delete", async ({
    page,
    consoleGuard,
  }) => {
    await gotoDashboard(page);
    await expect(page.getByText("API OK")).toBeVisible();
    await expect(page.getByText("No documents uploaded yet")).toBeVisible();

    // 1. File picker upload of valid multi-page PDF -> 202 -> settles to Ready without trailing poll spam
    const upload202Promise = page.waitForResponse(
      (res) =>
        res.url() === `${E2E_API_BASE_URL}/api/v1/documents` &&
        res.request().method() === "POST",
    );
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.getByRole("button", { name: "Choose PDF file" }).click(),
    ]);
    await fileChooser.setFiles(fixtures["multipage.pdf"]);

    const uploadResp = await upload202Promise;
    expect(uploadResp.status()).toBe(202);
    await expect(page.getByRole("status").filter({ hasText: /queued for ingestion/i })).toBeVisible();

    const multiCard = page.getByRole("article", { name: "multipage.pdf" });
    await expect(multiCard).toBeVisible();
    await expect(multiCard.getByText("Ready", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("Auto-refreshing (2s)")).toHaveCount(0);

    // Verify chunk_count > 0 and UTC timestamps
    const multiChunksDd = multiCard
      .locator("dt", { hasText: /^Chunks$/ })
      .locator("xpath=following-sibling::dd[1]");
    const chunkCountText = (await multiChunksDd.innerText()).trim();
    expect(Number(chunkCountText)).toBeGreaterThan(0);

    const createdDd = multiCard
      .locator("dt", { hasText: /^Created$/ })
      .locator("xpath=following-sibling::dd[1]");
    await expect(createdDd).toHaveText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$/);

    // Verify zero trailing poll spam once settled
    let trailingPolls = 0;
    const onRequest = (req: { url: () => string; method: () => string }) => {
      if (
        req.method() === "GET" &&
        req.url().startsWith(`${E2E_API_BASE_URL}/api/v1/documents`)
      ) {
        trailingPolls += 1;
      }
    };
    page.on("request", onRequest);
    await page.waitForTimeout(3_000);
    page.off("request", onRequest);
    expect(trailingPolls).toBe(0);

    // 2. Drag-and-drop valid PDF onto dropzone -> 202 -> settles to Ready
    const simpleBytes = Array.from(fs.readFileSync(fixtures["simple.pdf"]));
    const dropTransfer = await page.evaluateHandle(
      ({ bytes, filename }) => {
        const dt = new DataTransfer();
        const file = new File([new Uint8Array(bytes)], filename, {
          type: "application/pdf",
        });
        dt.items.add(file);
        return dt;
      },
      { bytes: simpleBytes, filename: "simple-drop.pdf" },
    );

    const drop202Promise = page.waitForResponse(
      (res) =>
        res.url() === `${E2E_API_BASE_URL}/api/v1/documents` &&
        res.request().method() === "POST",
    );
    const dropzone = page.getByTestId("pdf-dropzone");
    await dropzone.dispatchEvent("dragenter", { dataTransfer: dropTransfer });
    await dropzone.dispatchEvent("dragover", { dataTransfer: dropTransfer });
    await dropzone.dispatchEvent("drop", { dataTransfer: dropTransfer });
    await dropTransfer.dispose();

    const dropResp = await drop202Promise;
    expect(dropResp.status()).toBe(202);
    const dropCard = page.getByRole("article", { name: "simple-drop.pdf" });
    await expect(dropCard).toBeVisible();
    await expect(dropCard.getByText("Ready", { exact: true })).toBeVisible({
      timeout: 30_000,
    });

    // 3. Scanned zero-text PDF -> settles to Failed with EMPTY_PDF_NOT_SUPPORTED & Reprocess button
    const chooseBtn = page.getByRole("button", { name: "Choose PDF file" });
    await chooseBtn.focus();
    await expect(chooseBtn).toBeFocused();
    const [scannedChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      chooseBtn.press("Enter"),
    ]);
    await scannedChooser.setFiles(fixtures["scanned.pdf"]);

    const scannedCard = page.getByRole("article", { name: "scanned.pdf" });
    await expect(scannedCard).toBeVisible();
    await expect(scannedCard.getByText("Failed", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await expect(scannedCard.getByRole("note")).toContainText(
      "EMPTY_PDF_NOT_SUPPORTED",
    );
    await expect(
      scannedCard.getByRole("button", { name: "Reprocess scanned.pdf" }),
    ).toBeVisible();

    // 4. Upload 18 more PDFs via API so total = 21 documents for pagination testing
    const simplePdfBuffer = fs.readFileSync(fixtures["simple.pdf"]);
    const uploadedIds: string[] = [];
    for (let i = 1; i <= 18; i += 1) {
      const form = new FormData();
      form.append(
        "file",
        new Blob([simplePdfBuffer], { type: "application/pdf" }),
        `extra-${String(i).padStart(2, "0")}.pdf`,
      );
      const resp = await fetch(`${E2E_API_BASE_URL}/api/v1/documents`, {
        method: "POST",
        body: form,
      });
      expect(resp.status).toBe(202);
      const body = (await resp.json()) as { id: string };
      uploadedIds.push(body.id);
    }

    // Wait for all 18 API-uploaded docs to reach ready state before testing pagination/filters
    for (const id of uploadedIds) {
      const deadline = Date.now() + 30_000;
      let status = "pending";
      while (Date.now() < deadline) {
        const res = await fetch(`${E2E_API_BASE_URL}/api/v1/documents/${id}`);
        const payload = (await res.json()) as { status: string };
        status = payload.status;
        if (status === "ready" || status === "failed") {
          break;
        }
        await page.waitForTimeout(100);
      }
      expect(status).toBe("ready");
    }

    await page.reload();
    await expect(page.getByText("21 documents")).toBeVisible();
    await expect(page.getByText("Showing 1–20 of 21")).toBeVisible();

    // Verify status filter tabs & empty state ("No pending documents")
    await page.getByRole("tab", { name: "Pending" }).click();
    await expect(page.getByText("No pending documents")).toBeVisible();

    await page.getByRole("tab", { name: "Failed" }).click();
    await expect(page.getByText("1 document")).toBeVisible();
    await expect(page.getByRole("article", { name: "scanned.pdf" })).toBeVisible();

    await page.getByRole("tab", { name: "Ready" }).click();
    await expect(page.getByText("20 documents")).toBeVisible();

    await page.getByRole("tab", { name: "All" }).click();
    await expect(page.getByText("Showing 1–20 of 21")).toBeVisible();

    // Navigate to page 2 -> Showing 21-21 of 21
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByText("Showing 21–21 of 21")).toBeVisible();
    const page2Articles = page.locator("ul > li > article");
    await expect(page2Articles).toHaveCount(1);

    const solePage2Article = page2Articles.first();
    const deletedDocId = (await solePage2Article.locator("p code").innerText()).trim();
    const deletedFilename = (
      await solePage2Article.locator("h3").innerText()
    ).trim();

    // 5. Keyboard-accessible Delete dialog: focus Cancel -> Escape close -> Confirm delete
    await solePage2Article
      .getByRole("button", { name: `Delete ${deletedFilename}` })
      .click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const cancelBtn = dialog.getByRole("button", { name: "Cancel" });
    await expect(cancelBtn).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(solePage2Article).toBeVisible();

    // Reopen and confirm delete -> 204 -> steps back to page 1 (Showing 1-20 of 20)
    await solePage2Article
      .getByRole("button", { name: `Delete ${deletedFilename}` })
      .click();
    await expect(dialog).toBeVisible();

    const delete204Promise = page.waitForResponse(
      (res) =>
        res.url() === `${E2E_API_BASE_URL}/api/v1/documents/${deletedDocId}` &&
        res.request().method() === "DELETE",
    );
    await dialog.getByRole("button", { name: "Confirm delete" }).click();
    const deleteResp = await delete204Promise;
    expect(deleteResp.status()).toBe(204);

    await expect(dialog).toHaveCount(0);
    await expect(page.getByText("20 documents")).toBeVisible();
    await expect(page.locator("ul > li > article")).toHaveCount(20);
    await expect(page.getByText("Showing 1–20 of 20")).toHaveCount(0);

    // 6. Cross-check API 404 and zero file/SQLite/ChromaDB leftovers after stopping backend
    const getDeletedResp = await fetch(
      `${E2E_API_BASE_URL}/api/v1/documents/${deletedDocId}`,
    );
    expect(getDeletedResp.status).toBe(404);

    await backend.stop();
    const verified = verifyDocumentDeleted(workDir, deletedDocId);
    expect(verified).toEqual({
      pdf_exists: false,
      db_doc_exists: false,
      db_chunk_count: 0,
      chroma_vector_count: 0,
    });

    consoleGuard.assertClean();
  });
});
