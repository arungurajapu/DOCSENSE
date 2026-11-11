import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(SCRIPT_DIR, "..");
const OPENAPI_PATH = path.resolve(FRONTEND_DIR, "..", "contracts", "openapi.json");
const OUTPUT_PATH = path.resolve(
  FRONTEND_DIR,
  "src",
  "types",
  "openapi.generated.ts",
);

function normalizeLineEndings(content) {
  return content.replace(/\r\n/g, "\n");
}

async function generateContractTypes() {
  if (!fs.existsSync(OPENAPI_PATH)) {
    throw new Error(`OpenAPI contract not found at ${OPENAPI_PATH}`);
  }

  const ast = await openapiTS(pathToFileURL(OPENAPI_PATH), {
    silent: true,
  });
  return normalizeLineEndings(`${COMMENT_HEADER}${astToString(ast)}`);
}

async function main() {
  const args = new Set(process.argv.slice(2));
  const shouldWrite = args.has("--write");
  const shouldCheck = args.has("--check") || !shouldWrite;

  const expected = await generateContractTypes();

  if (shouldWrite) {
    fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
    fs.writeFileSync(OUTPUT_PATH, expected, "utf8");
    console.log(
      `Generated ${path.relative(FRONTEND_DIR, OUTPUT_PATH)} from ${path.relative(FRONTEND_DIR, OPENAPI_PATH)}.`,
    );
    return;
  }

  if (shouldCheck) {
    if (!fs.existsSync(OUTPUT_PATH)) {
      console.error(
        `Missing generated contract file at ${path.relative(FRONTEND_DIR, OUTPUT_PATH)}. Run "npm run generate:contracts".`,
      );
      process.exitCode = 1;
      return;
    }

    const actual = normalizeLineEndings(fs.readFileSync(OUTPUT_PATH, "utf8"));
    if (actual !== expected) {
      console.error(
        `Contract mismatch: ${path.relative(FRONTEND_DIR, OUTPUT_PATH)} is out of sync with ${path.relative(FRONTEND_DIR, OPENAPI_PATH)}. Run "npm run generate:contracts".`,
      );
      process.exitCode = 1;
      return;
    }

    console.log(
      `Contract verified: ${path.relative(FRONTEND_DIR, OUTPUT_PATH)} matches ${path.relative(FRONTEND_DIR, OPENAPI_PATH)}.`,
    );
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
