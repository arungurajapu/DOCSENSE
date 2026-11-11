import { test as base, expect, type Page } from "@playwright/test";

export interface ConsoleGuard {
  allowExpectedHttpErrors: (statusPattern: RegExp) => void;
  allowExpectedNetworkErrors: () => void;
  assertClean: () => void;
}

interface E2EFixtures {
  consoleGuard: ConsoleGuard;
}

export const test = base.extend<E2EFixtures>({
  consoleGuard: async ({ page }, provide) => {
    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    const allowedHttpPatterns: RegExp[] = [];
    let allowNetworkFailure = false;

    const isAllowedError = (text: string): boolean => {
      if (/cors/i.test(text)) {
        return false;
      }
      if (
        allowNetworkFailure &&
        /Failed to load resource: net::ERR_CONNECTION_REFUSED|ERR_FAILED/i.test(
          text,
        )
      ) {
        return true;
      }
      if (/Failed to load resource: the server responded with a status of/i.test(text)) {
        return allowedHttpPatterns.some((pattern) => pattern.test(text));
      }
      return false;
    };

    const onPageError = (error: Error) => {
      pageErrors.push(error.stack ?? error.message);
    };

    const onConsole = (msg: { type: () => string; text: () => string }) => {
      if (msg.type() === "error") {
        const text = msg.text();
        if (!isAllowedError(text)) {
          consoleErrors.push(text);
        }
      }
    };

    page.on("pageerror", onPageError);
    page.on("console", onConsole);

    const guard: ConsoleGuard = {
      allowExpectedHttpErrors(statusPattern: RegExp) {
        allowedHttpPatterns.push(statusPattern);
      },
      allowExpectedNetworkErrors() {
        allowNetworkFailure = true;
      },
      assertClean() {
        expect(pageErrors, "Uncaught browser pageerror detected").toEqual([]);
        expect(consoleErrors, "Unexpected browser console.error detected").toEqual(
          [],
        );
      },
    };

    await provide(guard);

    page.off("pageerror", onPageError);
    page.off("console", onConsole);
    guard.assertClean();
  },
});

export { expect };

export async function gotoDashboard(page: Page): Promise<void> {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "DocSense", level: 1 }),
  ).toBeVisible();
}
