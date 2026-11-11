import fs from "node:fs";
import path from "node:path";
import {
  E2E_API_BASE_URL,
  ManagedBackendServer,
  createTempWorkDir,
  generatePdfFixtures,
  listUploadDirFiles,
  removeTempWorkDir,
  type FixtureManifest,
} from "./helpers/backend";
import { expect, gotoDashboard, test } from "./helpers/test-fixtures";

test.describe("Validation, Server Error Alerts, Browser 413, and Health States", () => {
  let workDir = "";
  let fixturesDir = "";
  let fixtures: FixtureManifest;
  let backend: ManagedBackendServer | null = null;

  test.beforeEach(() => {
    workDir = createTempWorkDir("docsense-pw-val-");
    fixturesDir = path.join(workDir, "fixtures");
    fixtures = generatePdfFixtures(fixturesDir);
  });

  test.afterEach(async () => {
    if (backend) {
      await backend.stop();
      backend = null;
    }
    removeTempWorkDir(workDir);
  });

  test("client validation blocks bad files, server 400/415/422 errors render alerts, and HealthBadge recovers from unreachable", async ({
    page,
    consoleGuard,
  }) => {
    consoleGuard.allowExpectedHttpErrors(/\b(400|415|422)\b/);
    consoleGuard.allowExpectedNetworkErrors();

    backend = new ManagedBackendServer(workDir);
    await backend.start();
    await gotoDashboard(page);
    await expect(page.getByText("API OK")).toBeVisible();

    // 1. Client-blocked uploads must NOT fire any POST /api/v1/documents
    let postCount = 0;
    const onRequest = (req: { url: () => string; method: () => string }) => {
      if (
        req.method() === "POST" &&
        req.url() === `${E2E_API_BASE_URL}/api/v1/documents`
      ) {
        postCount += 1;
      }
    };
    page.on("request", onRequest);

    const fileInput = page.locator("#pdf-upload-input");

    // Client-blocked .txt
    await fileInput.setInputFiles(fixtures["notes.txt"]);
    const uploadAlert = page.getByRole("alert").first();
    await expect(uploadAlert).toContainText("CLIENT_VALIDATION_ERROR");
    await expect(uploadAlert).toContainText(/only pdf files \(\.pdf\)/i);

    // Client-blocked 0-byte .pdf
    await fileInput.setInputFiles(fixtures["empty_zero.pdf"]);
    await expect(uploadAlert).toContainText("CLIENT_VALIDATION_ERROR");
    await expect(uploadAlert).toContainText(/empty \(0 bytes\)/i);

    // Client-blocked multi-file drop
    const simpleBytes = Array.from(fs.readFileSync(fixtures["simple.pdf"]));
    const multiDropTransfer = await page.evaluateHandle((bytes) => {
      const dt = new DataTransfer();
      dt.items.add(
        new File([new Uint8Array(bytes)], "first.pdf", {
          type: "application/pdf",
        }),
      );
      dt.items.add(
        new File([new Uint8Array(bytes)], "second.pdf", {
          type: "application/pdf",
        }),
      );
      return dt;
    }, simpleBytes);

    const dropzone = page.getByTestId("pdf-dropzone");
    await dropzone.dispatchEvent("dragenter", { dataTransfer: multiDropTransfer });
    await dropzone.dispatchEvent("dragover", { dataTransfer: multiDropTransfer });
    await dropzone.dispatchEvent("drop", { dataTransfer: multiDropTransfer });
    await multiDropTransfer.dispose();

    await expect(uploadAlert).toContainText("MULTIPLE_FILES_NOT_SUPPORTED");
    expect(postCount).toBe(0);
    page.off("request", onRequest);

    // 2. Server-side 400 (".pdf"), 415 (spoofed & corrupt), 422 (encrypted & zero-page)
    const serverCases: Array<{
      filePath: string;
      expectedStatus: number;
      expectedCode: string;
    }> = [
      {
        filePath: fixtures[".pdf"],
        expectedStatus: 400,
        expectedCode: "BAD_REQUEST",
      },
      {
        filePath: fixtures["spoofed.pdf"],
        expectedStatus: 415,
        expectedCode: "UNSUPPORTED_MEDIA_TYPE",
      },
      {
        filePath: fixtures["corrupt.pdf"],
        expectedStatus: 415,
        expectedCode: "UNSUPPORTED_MEDIA_TYPE",
      },
      {
        filePath: fixtures["encrypted.pdf"],
        expectedStatus: 422,
        expectedCode: "ENCRYPTED_PDF_NOT_SUPPORTED",
      },
      {
        filePath: fixtures["zeropage.pdf"],
        expectedStatus: 422,
        expectedCode: "EMPTY_PDF_NOT_SUPPORTED",
      },
    ];

    for (const testCase of serverCases) {
      const respPromise = page.waitForResponse(
        (res) =>
          res.url() === `${E2E_API_BASE_URL}/api/v1/documents` &&
          res.request().method() === "POST",
      );
      await fileInput.setInputFiles(testCase.filePath);
      const resp = await respPromise;
      expect(resp.status()).toBe(testCase.expectedStatus);
      await expect(page.getByRole("alert").first()).toContainText(
        testCase.expectedCode,
      );
    }

    expect(listUploadDirFiles(workDir)).toEqual([]);

    // 3. Unreachable backend -> red API Unreachable + network error alert -> restart + Retry -> API OK
    await backend.stop();
    await page.reload();

    const header = page.locator("header");
    await expect(header.getByText("API Unreachable")).toBeVisible();
    await expect(page.locator("main").getByRole("alert")).toContainText(
      /unable to reach the docsense backend api/i,
    );

    await backend.start();
    await header.getByRole("button", { name: "Retry" }).click();
    await expect(header.getByText("API OK")).toBeVisible();

    consoleGuard.assertClean();
  });

  test("real browser-to-server 413 FILE_TOO_LARGE with MAX_UPLOAD_SIZE_MB=1 and zero disk residue", async ({
    page,
    consoleGuard,
  }) => {
    consoleGuard.allowExpectedHttpErrors(/\b413\b/);

    backend = new ManagedBackendServer(workDir);
    await backend.start({ MAX_UPLOAD_SIZE_MB: "1" });
    await gotoDashboard(page);
    await expect(page.getByText("API OK")).toBeVisible();

    const respPromise = page.waitForResponse(
      (res) =>
        res.url() === `${E2E_API_BASE_URL}/api/v1/documents` &&
        res.request().method() === "POST",
    );
    await page.locator("#pdf-upload-input").setInputFiles(fixtures["oversize_1_1mb.pdf"]);

    const resp = await respPromise;
    expect(resp.status()).toBe(413);
    await expect(page.getByRole("alert").first()).toContainText("FILE_TOO_LARGE");
    expect(listUploadDirFiles(workDir)).toEqual([]);

    consoleGuard.assertClean();
  });

  test("HealthBadge displays amber API Degraded when EMBEDDING_MODEL is invalid/offline", async ({
    page,
    consoleGuard,
  }) => {
    backend = new ManagedBackendServer(workDir);
    await backend.start({
      EMBEDDING_MODEL: "invalid/nonexistent-docsense-model-e2e",
      HF_HUB_OFFLINE: "1",
    });

    await gotoDashboard(page);
    await expect(page.locator("header").getByText("API Degraded")).toBeVisible();

    consoleGuard.assertClean();
  });
});
