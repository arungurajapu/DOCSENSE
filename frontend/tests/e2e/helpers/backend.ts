import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const FRONTEND_DIR = fs.existsSync(
  path.resolve(process.cwd(), "tests", "e2e", "helpers", "backend_runner.py"),
)
  ? process.cwd()
  : path.resolve(process.cwd(), "frontend");
const HELPERS_DIR = path.resolve(FRONTEND_DIR, "tests", "e2e", "helpers");
const REPO_ROOT = path.resolve(FRONTEND_DIR, "..");
const BACKEND_DIR = path.resolve(REPO_ROOT, "backend");
const PYTHON_EXE = path.resolve(
  BACKEND_DIR,
  ".venv",
  "Scripts",
  "python.exe",
);
const RUNNER_SCRIPT = path.resolve(HELPERS_DIR, "backend_runner.py");
const TOOLS_SCRIPT = path.resolve(HELPERS_DIR, "backend_tools.py");

export const E2E_API_PORT = 8100;
export const E2E_API_BASE_URL = `http://127.0.0.1:${E2E_API_PORT}`;

export interface FixtureManifest {
  "multipage.pdf": string;
  "simple.pdf": string;
  "scanned.pdf": string;
  "encrypted.pdf": string;
  "zeropage.pdf": string;
  "corrupt.pdf": string;
  "spoofed.pdf": string;
  "oversize_1_1mb.pdf": string;
  "empty_zero.pdf": string;
  ".pdf": string;
  "notes.txt": string;
}

export interface SeededRecoveryIds {
  ready_id: string;
  pending_id: string;
  processing_id: string;
  deleting_id: string;
}

export interface DeletedVerificationResult {
  pdf_exists: boolean;
  db_doc_exists: boolean;
  db_chunk_count: number;
  chroma_vector_count: number;
}

export function createTempWorkDir(prefix = "docsense-pw-"): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

export function removeTempWorkDir(dirPath: string): void {
  fs.rmSync(dirPath, { recursive: true, force: true });
}

export function generatePdfFixtures(outDir: string): FixtureManifest {
  const stdout = execFileSync(
    PYTHON_EXE,
    [TOOLS_SCRIPT, "generate-fixtures", outDir],
    {
      cwd: BACKEND_DIR,
      encoding: "utf8",
    },
  );
  return JSON.parse(stdout) as FixtureManifest;
}

export function seedRecoveryState(workDir: string): SeededRecoveryIds {
  const stdout = execFileSync(
    PYTHON_EXE,
    [TOOLS_SCRIPT, "seed-recovery", workDir],
    {
      cwd: BACKEND_DIR,
      encoding: "utf8",
    },
  );
  return JSON.parse(stdout) as SeededRecoveryIds;
}

export function verifyDocumentDeleted(
  workDir: string,
  docId: string,
): DeletedVerificationResult {
  const stdout = execFileSync(
    PYTHON_EXE,
    [TOOLS_SCRIPT, "verify-deleted", workDir, docId],
    {
      cwd: BACKEND_DIR,
      encoding: "utf8",
    },
  );
  return JSON.parse(stdout) as DeletedVerificationResult;
}

export function listUploadDirFiles(workDir: string): string[] {
  const uploadDir = path.join(workDir, "uploads");
  if (!fs.existsSync(uploadDir)) {
    return [];
  }
  return fs.readdirSync(uploadDir);
}

export class ManagedBackendServer {
  readonly workDir: string;
  readonly port: number;
  readonly baseUrl: string;
  private proc: ChildProcess | null = null;
  private stopFile: string | null = null;
  private output = "";

  constructor(workDir: string, port = E2E_API_PORT) {
    this.workDir = workDir;
    this.port = port;
    this.baseUrl = `http://127.0.0.1:${port}`;
  }

  get logs(): string {
    return this.output;
  }

  async start(
    envOverrides: Record<string, string> = {},
    startupTimeoutMs = 60_000,
  ): Promise<void> {
    if (this.proc) {
      throw new Error("ManagedBackendServer is already running.");
    }

    fs.mkdirSync(this.workDir, { recursive: true });
    const dbPath = path.join(this.workDir, "db", "docsense.db");
    const dbUrl = `sqlite:///${dbPath.replace(/\\/g, "/")}`;
    const chromaDir = path.join(this.workDir, "chroma");
    const uploadDir = path.join(this.workDir, "uploads");
    const stopFile = path.join(
      this.workDir,
      `.uvicorn_stop_${Date.now()}_${Math.random().toString(16).slice(2)}.flag`,
    );
    this.stopFile = stopFile;
    this.output = "";

    const env: NodeJS.ProcessEnv = { ...process.env };
    for (const key of [
      "DATABASE_URL",
      "CHROMA_PERSIST_DIR",
      "CHROMA_COLLECTION_NAME",
      "UPLOAD_DIR",
      "CORS_ORIGINS",
      "MAX_UPLOAD_SIZE_MB",
      "EMBEDDING_MODEL",
      "HF_HUB_OFFLINE",
    ]) {
      delete env[key];
    }

    Object.assign(env, {
      PYTHONUNBUFFERED: "1",
      DATABASE_URL: dbUrl,
      CHROMA_PERSIST_DIR: chromaDir,
      UPLOAD_DIR: uploadDir,
      CORS_ORIGINS: '["http://localhost:3100","http://127.0.0.1:3100"]',
      ...envOverrides,
    });

    const child = spawn(
      PYTHON_EXE,
      ["-u", RUNNER_SCRIPT, String(this.port), stopFile],
      {
        cwd: BACKEND_DIR,
        env,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    this.proc = child;

    child.stdout?.on("data", (chunk: Buffer) => {
      this.output += chunk.toString("utf8");
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      this.output += chunk.toString("utf8");
    });

    let exitCode: number | null = null;
    child.once("exit", (code) => {
      exitCode = code ?? -1;
    });

    const deadline = Date.now() + startupTimeoutMs;
    let lastError = "";
    while (Date.now() < deadline) {
      if (exitCode !== null) {
        this.proc = null;
        throw new Error(
          `Backend exited early during startup (code=${exitCode}).\n--- Backend Logs ---\n${this.output}`,
        );
      }
      try {
        const resp = await fetch(`${this.baseUrl}/api/v1/health`, {
          signal: AbortSignal.timeout(1000),
        });
        if (resp.status === 200 || resp.status === 503) {
          return;
        }
        lastError = `HTTP ${resp.status}`;
      } catch (err) {
        lastError = err instanceof Error ? err.message : String(err);
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }

    await this.stop();
    throw new Error(
      `Backend failed to become ready within ${startupTimeoutMs}ms (${lastError}).\n--- Backend Logs ---\n${this.output}`,
    );
  }

  async stop(timeoutMs = 20_000): Promise<void> {
    const child = this.proc;
    if (!child) {
      return;
    }
    this.proc = null;

    const stopFile = this.stopFile;
    if (stopFile) {
      try {
        fs.mkdirSync(path.dirname(stopFile), { recursive: true });
        fs.writeFileSync(stopFile, "stop", "utf8");
      } catch {
        // Ignore stop file write failure if directory already removed.
      }
    }

    await new Promise<void>((resolve) => {
      if (child.exitCode !== null) {
        resolve();
        return;
      }

      const timer = setTimeout(() => {
        child.kill("SIGKILL");
      }, timeoutMs);

      child.once("close", () => {
        clearTimeout(timer);
        resolve();
      });
    });

    if (stopFile) {
      try {
        fs.rmSync(stopFile, { force: true });
      } catch {
        // Ignore cleanup failure.
      }
    }
  }
}
