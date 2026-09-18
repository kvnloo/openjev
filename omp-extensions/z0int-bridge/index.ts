/**
 * z0int ↔ Kerdoios bridge (log-only host adapter).
 *
 * Boundary:
 *   z0int decides what does NOT need a frontier LLM (preflight).
 *   Kerdoios decides where residual cognition runs (ExecutionPlan only).
 *   Harness executes. This extension does not inject skill_relevance and
 *   does not force model switches yet — it measures the loop.
 *
 * Surfaces:
 *   before_agent_start → preflight (+ kerdoios plan when route=model)
 *   after turn (best-effort) → receipt with counterfactual tokens avoided
 *
 * Logs under ~/.z0int/stream and ~/.z0int/shadow.
 */
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const STREAM = join(homedir(), ".z0int", "stream", "bridge.jsonl");
const SHADOW = join(homedir(), ".z0int", "shadow", "preflight.jsonl");
const PY =
	process.env.EVOLUTION_LAB_PYTHON ||
	"/workspace/evolution-lab/.venv/bin/python";
const EL_ROOT = process.env.EVOLUTION_LAB_ROOT || "/workspace/evolution-lab";
const KERD_PY =
	process.env.KERDOIOS_PYTHON ||
	process.env.EVOLUTION_LAB_PYTHON ||
	"/workspace/evolution-lab/.venv/bin/python";
const KERD_ROOT =
	process.env.KERDOIOS_ROOT ||
	"/home/kvn/.hermes/profiles/chiefstaff/plugins/kerdoios";

type PyResult = { code: number; stdout: string; stderr: string };
type Jsonish = Record<string, unknown>;

async function runCmd(
	cmd: string,
	args: string[],
	cwd: string,
	timeoutMs: number,
): Promise<PyResult> {
	const { promise, resolve } = Promise.withResolvers<PyResult>();
	const child = spawn(cmd, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
	let stdout = "";
	let stderr = "";
	const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
	child.stdout.on("data", d => {
		stdout += String(d);
	});
	child.stderr.on("data", d => {
		stderr += String(d);
	});
	child.on("close", code => {
		clearTimeout(timer);
		resolve({ code: code ?? 1, stdout, stderr });
	});
	child.on("error", err => {
		clearTimeout(timer);
		resolve({ code: 1, stdout, stderr: String(err) });
	});
	return promise;
}

function parseJson(r: PyResult): Jsonish {
	const t = r.stdout.trim();
	if (!t) return { ok: false, error: r.stderr || "empty" };
	try {
		const start = t.indexOf("{");
		const end = t.lastIndexOf("}");
		if (start < 0 || end < start) return { ok: false, error: "no_json" };
		return JSON.parse(t.slice(start, end + 1)) as Jsonish;
	} catch (e) {
		return { ok: false, error: String(e), raw: t.slice(0, 200) };
	}
}

function append(path: string, row: Jsonish): void {
	mkdirSync(join(path, ".."), { recursive: true });
	appendFileSync(path, JSON.stringify(row) + "\n");
}

async function preflight(prompt: string): Promise<Jsonish> {
	const r = await runCmd(PY, ["-m", "evolution_lab", "preflight", prompt], EL_ROOT, 8000);
	return parseJson(r);
}

async function kerdoiosPlan(capabilityId: string, work: Jsonish | null): Promise<Jsonish | null> {
	if (!work) return null;
	const args = [
		"-m",
		"kerdoios",
		"plan",
		"--capability-id",
		capabilityId,
		"--observed",
		"--workers",
		"1",
		"--mode",
		String(work.mode || "balanced"),
	];
	if (typeof work.coding === "number") {
		args.push("--coding", String(work.coding));
	}
	if (typeof work.reasoning === "number") {
		args.push("--reasoning", String(work.reasoning));
	}
	const r = await runCmd(KERD_PY, args, KERD_ROOT, 12000);
	if (r.code !== 0) {
		return { ok: false, error: r.stderr || r.stdout || `exit_${r.code}` };
	}
	return parseJson(r);
}

async function turnBridge(prompt: string, sessionId: string | undefined): Promise<void> {
	const t0 = Date.now();
	const pf = await preflight(prompt);
	const capabilityId = typeof pf.capability_id === "string" ? pf.capability_id : "coding.next_action";
	const route = typeof pf.route === "string" ? pf.route : "model";
	let plan: Jsonish | null = null;
	if (route === "model") {
		const wr =
			pf.work_requirement && typeof pf.work_requirement === "object"
				? (pf.work_requirement as Jsonish)
				: null;
		plan = await kerdoiosPlan(capabilityId, wr);
	}
	const latencyMs = Date.now() - t0;
	const traceId = randomUUID().replaceAll("-", "");
	const row = {
		schema: "z0int.bridge.v1",
		ts: Date.now() / 1000,
		trace_id: traceId,
		session_id: sessionId ?? process.env.OMP_SESSION_ID ?? null,
		prompt: prompt.slice(0, 400),
		preflight: pf,
		kerdoios_plan: plan,
		latency_ms: latencyMs,
		// Log-only: host still runs its normal model path until promote gate.
		execution: "log_only",
		receipt: {
			route,
			capability_id: capabilityId,
			estimated_frontier_tokens_avoided: pf.estimated_frontier_tokens_avoided ?? 0,
			provider: route === "local" ? "z0int" : null,
			model: route === "local" ? "mb_local" : null,
			input_tokens: route === "local" ? 0 : null,
			output_tokens: route === "local" ? 0 : null,
		},
	};
	append(STREAM, row);
	append(SHADOW, {
		ts: row.ts,
		trace_id: traceId,
		route,
		capability_id: capabilityId,
		avoided: pf.estimated_frontier_tokens_avoided ?? 0,
		plan_ok: plan ? plan.ok !== false && !plan.error : null,
	});
}

export default function z0intBridge(pi: ExtensionAPI) {
	pi.setLabel("z0int preflight → Kerdoios residual (log-only)");
	pi.on("before_agent_start", async (event, ctx) => {
		const prompt =
			event && typeof event === "object" && "prompt" in event
				? String((event as { prompt?: unknown }).prompt ?? "").trim()
				: "";
		if (!prompt || prompt.startsWith("/")) return;
		const sessionId =
			ctx && typeof ctx === "object" && "sessionId" in ctx
				? String((ctx as { sessionId?: unknown }).sessionId ?? "")
				: process.env.OMP_SESSION_ID;
		try {
			await turnBridge(prompt, sessionId || undefined);
		} catch {
			return;
		}
	});
}

export { preflight, turnBridge };
