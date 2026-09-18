/**
 * z0int ↔ Kerdoios bridge (log-only host adapter).
 *
 * before_agent_start → preflight (+ kerdoios plan when route=model)
 *   writes ~/.z0int/stream/bridge.jsonl + dual-write decision receipt
 * agent_end → close last open trace (measured tokens + outcome + kerdoios record)
 */
import { randomUUID } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const Z0 = join(homedir(), ".z0int");
const STREAM = join(Z0, "stream", "bridge.jsonl");
const SHADOW = join(Z0, "shadow", "preflight.jsonl");
const OPEN = join(Z0, "stream", "open_turns.jsonl");
const LAST = join(Z0, "stream", "last_open.json");
const HEART = join(Z0, "stream", "bridge_heart.jsonl");

const PY =
	process.env.EVOLUTION_LAB_PYTHON ||
	"/workspace/evolution-lab/.venv/bin/python";
const EL_ROOT = process.env.EVOLUTION_LAB_ROOT || "/workspace/evolution-lab";
const Z0_PY =
	process.env.Z0INT_PYTHON ||
	"/home/kvn/tmp/openjev/.venv/bin/python";
const Z0_ROOT = process.env.Z0INT_ROOT || "/home/kvn/tmp/openjev";
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
	return await new Promise((resolve) => {
		const child = spawn(cmd, args, { cwd, env: process.env });
		let stdout = "";
		let stderr = "";
		const t = setTimeout(() => {
			try {
				child.kill("SIGKILL");
			} catch {
				/* */
			}
			resolve({ code: 124, stdout, stderr: stderr + "\ntimeout" });
		}, timeoutMs);
		child.stdout.on("data", (d) => {
			stdout += String(d);
		});
		child.stderr.on("data", (d) => {
			stderr += String(d);
		});
		child.on("close", (code) => {
			clearTimeout(t);
			resolve({ code: code ?? 1, stdout, stderr });
		});
		child.on("error", (err) => {
			clearTimeout(t);
			resolve({ code: 1, stdout, stderr: String(err) });
		});
	});
}

function parseJson(r: PyResult): Jsonish {
	const raw = (r.stdout || "").trim();
	if (!raw) return { ok: false, error: r.stderr || `exit_${r.code}` };
	try {
		return JSON.parse(raw) as Jsonish;
	} catch {
		// last JSON object in stream
		const lines = raw.split("\n").filter(Boolean);
		for (let i = lines.length - 1; i >= 0; i--) {
			try {
				return JSON.parse(lines[i]!) as Jsonish;
			} catch {
				/* */
			}
		}
		return { ok: false, error: "json_parse", raw: raw.slice(0, 400) };
	}
}

function append(path: string, row: Jsonish): void {
	mkdirSync(dirname(path), { recursive: true });
	appendFileSync(path, JSON.stringify(row) + "\n", "utf8");
}

function writeLast(row: Jsonish): void {
	mkdirSync(dirname(LAST), { recursive: true });
	writeFileSync(LAST, JSON.stringify(row, null, 2), "utf8");
}

function readLast(): Jsonish | null {
	if (!existsSync(LAST)) return null;
	try {
		return JSON.parse(readFileSync(LAST, "utf8")) as Jsonish;
	} catch {
		return null;
	}
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
	if (typeof work.coding === "number") args.push("--coding", String(work.coding));
	if (typeof work.reasoning === "number") args.push("--reasoning", String(work.reasoning));
	const r = await runCmd(KERD_PY, args, KERD_ROOT, 12000);
	if (r.code !== 0) return { ok: false, error: r.stderr || r.stdout || `exit_${r.code}` };
	return parseJson(r);
}

async function dualWriteReceipt(receipt: Jsonish): Promise<void> {
	const payload = JSON.stringify(receipt);
	await runCmd(
		Z0_PY,
		[
			"-c",
			"import json,sys; from z0int.receipt import append_receipt; append_receipt(json.loads(sys.argv[1]))",
			payload,
		],
		Z0_ROOT,
		3000,
	);
}

async function turnBridge(prompt: string, sessionId: string | undefined): Promise<Jsonish> {
	const t0 = Date.now();
	const pf = await preflight(prompt);
	const capabilityId =
		typeof pf.capability_id === "string" ? pf.capability_id : "coding.next_action";
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
	const baselineIn =
		typeof pf.baseline_input_tokens === "number"
			? pf.baseline_input_tokens
			: typeof (pf.work_requirement as Jsonish | null)?.estimated_input_tokens === "number"
				? ((pf.work_requirement as Jsonish).estimated_input_tokens as number)
				: null;
	const baselineOut =
		typeof pf.baseline_output_tokens === "number"
			? pf.baseline_output_tokens
			: typeof (pf.work_requirement as Jsonish | null)?.estimated_output_tokens === "number"
				? ((pf.work_requirement as Jsonish).estimated_output_tokens as number)
				: null;
	const avoided =
		typeof pf.estimated_frontier_tokens_avoided === "number"
			? pf.estimated_frontier_tokens_avoided
			: 0;
	const isLocal = route === "local";
	const receipt: Jsonish = {
		schema: "z0int.decision_receipt.v1",
		trace_id: traceId,
		session_id: sessionId ?? process.env.OMP_SESSION_ID ?? null,
		capability_id: capabilityId,
		provider: isLocal ? "local_mb" : plan ? "kerdoios_plan" : "frontier",
		model: isLocal ? "mb_local" : null,
		prediction:
			typeof pf.label === "string"
				? pf.label
				: typeof pf.prediction === "string"
					? pf.prediction
					: null,
		confidence:
			typeof pf.p === "number" ? pf.p : typeof pf.confidence === "number" ? pf.confidence : null,
		action_taken: route,
		route,
		execution: "log_only",
		outcome: null,
		input_tokens: isLocal ? 0 : null,
		output_tokens: isLocal ? 0 : null,
		baseline_input_tokens: baselineIn,
		baseline_output_tokens: baselineOut,
		estimated_frontier_tokens_avoided: avoided,
		measured_frontier_tokens: null,
		latency_ms: latencyMs,
		fallbacks: 0,
		ts: Date.now() / 1000,
	};
	const row = {
		schema: "z0int.bridge.v1",
		ts: receipt.ts,
		trace_id: traceId,
		session_id: receipt.session_id,
		prompt: prompt.slice(0, 400),
		preflight: pf,
		kerdoios_plan: plan,
		latency_ms: latencyMs,
		execution: "log_only",
		receipt,
	};
	append(STREAM, row);
	try {
		await dualWriteReceipt(receipt);
	} catch {
		/* bridge.jsonl still stands */
	}
	append(SHADOW, {
		ts: row.ts,
		trace_id: traceId,
		route,
		capability_id: capabilityId,
		avoided,
		plan_ok: plan ? plan.ok !== false && !plan.error : null,
	});
	const open = {
		schema: "z0int.open_turn.v1",
		trace_id: traceId,
		session_id: receipt.session_id,
		capability_id: capabilityId,
		route,
		baseline_input_tokens: baselineIn,
		baseline_output_tokens: baselineOut,
		provider: receipt.provider,
		ts: receipt.ts,
	};
	append(OPEN, open);
	writeLast(open);
	return row;
}

function estimateMeasuredFromMessages(messages: unknown[]): {
	input_tokens: number;
	output_tokens: number;
	measured: number;
} {
	// Cheap proxy when harness does not expose provider usage: char/4.
	// Still proves close path; replace when OMP surfaces real usage.
	let userChars = 0;
	let asstChars = 0;
	for (const m of messages || []) {
		if (!m || typeof m !== "object") continue;
		const role = String((m as { role?: unknown }).role || "");
		const content = (m as { content?: unknown }).content;
		let text = "";
		if (typeof content === "string") text = content;
		else if (Array.isArray(content)) {
			for (const c of content) {
				if (c && typeof c === "object" && "text" in c) text += String((c as { text: unknown }).text || "");
			}
		}
		if (role === "user") userChars += text.length;
		else if (role === "assistant") asstChars += text.length;
	}
	const input_tokens = Math.max(1, Math.round(userChars / 4));
	const output_tokens = Math.max(1, Math.round(asstChars / 4));
	return { input_tokens, output_tokens, measured: input_tokens + output_tokens };
}

async function closeOpenTurn(opts: {
	traceId?: string;
	measured?: number;
	inputTokens?: number;
	outputTokens?: number;
	success?: boolean;
	testPass?: boolean;
	toolOk?: boolean;
	source?: string;
	provider?: string;
	model?: string;
}): Promise<Jsonish> {
	const last = readLast();
	const traceId = opts.traceId || (last && typeof last.trace_id === "string" ? last.trace_id : null);
	if (!traceId) return { ok: false, error: "no_open_trace" };

	const args = [
		"-m",
		"z0int",
		"receipt",
		"close",
		traceId,
		"--source",
		opts.source || "bridge_agent_end",
	];
	if (opts.measured != null) args.push("--measured", String(opts.measured));
	if (opts.inputTokens != null) args.push("--input-tokens", String(opts.inputTokens));
	if (opts.outputTokens != null) args.push("--output-tokens", String(opts.outputTokens));
	if (opts.provider) args.push("--provider", opts.provider);
	if (opts.model) args.push("--model", opts.model);
	if (opts.success != null) args.push("--success", opts.success ? "true" : "false");
	if (opts.testPass != null) args.push("--test-pass", opts.testPass ? "true" : "false");
	if (opts.toolOk != null) args.push("--tool-ok", opts.toolOk ? "true" : "false");

	const r = await runCmd(Z0_PY, args, Z0_ROOT, 5000);
	const closed = parseJson(r);

	// Kerdoios observed economics (best-effort)
	try {
		const cap =
			(last && typeof last.capability_id === "string" && last.capability_id) ||
			"coding.next_action";
		const kArgs = [
			"-m",
			"kerdoios",
			"record",
			"--provider",
			opts.provider || "omp_bridge",
			"--model",
			opts.model || "session",
			"--task-type",
			"coding",
			"--capability-id",
			cap,
			"--cost",
			"0",
		];
		if (opts.success !== false) kArgs.push("--completed");
		if (opts.inputTokens != null) kArgs.push("--input-tokens", String(opts.inputTokens));
		if (opts.outputTokens != null) kArgs.push("--output-tokens", String(opts.outputTokens));
		await runCmd(KERD_PY, kArgs, KERD_ROOT, 5000);
	} catch {
		/* */
	}
	return closed;
}

export default function z0intBridge(pi: ExtensionAPI) {
	pi.setLabel("z0int preflight → Kerdoios residual → close (log-only)");

	pi.on("before_agent_start", async (event, ctx) => {
		const prompt =
			event && typeof event === "object" && "prompt" in event
				? String((event as { prompt?: unknown }).prompt ?? "").trim()
				: "";
		const sessionId =
			ctx && typeof ctx === "object" && "sessionId" in ctx
				? String((ctx as { sessionId?: unknown }).sessionId ?? "")
				: process.env.OMP_SESSION_ID;
		// Sync heartbeat first: proves the handler ran even if later work fails.
		try {
			append(HEART, {
				schema: "z0int.bridge_heart.v1",
				ts: Date.now() / 1000,
				session_id: sessionId || null,
				prompt_len: prompt.length,
				prompt_head: prompt.slice(0, 120),
				skipped: !prompt || prompt.startsWith("/"),
			});
		} catch {
			/* */
		}
		if (!prompt || prompt.startsWith("/")) return;
		// Await open so agent_end can close the same turn (print-mode races otherwise).
		try {
			await turnBridge(prompt, sessionId || undefined);
		} catch {
			return;
		}
	});

	pi.on("agent_end", async (event) => {
		try {
			if (event && typeof event === "object" && "willContinue" in event && (event as { willContinue?: boolean }).willContinue) {
				return;
			}
			const messages =
				event && typeof event === "object" && "messages" in event
					? ((event as { messages?: unknown[] }).messages || [])
					: [];
			const est = estimateMeasuredFromMessages(messages);
			// Brief poll: before_agent_start may still be writing last_open on short turns.
			for (let i = 0; i < 20; i++) {
				if (existsSync(LAST)) break;
				await new Promise((r) => setTimeout(r, 50));
			}
			await closeOpenTurn({
				measured: est.measured,
				inputTokens: est.input_tokens,
				outputTokens: est.output_tokens,
				success: true,
				toolOk: true,
				source: "bridge_agent_end",
			});
		} catch {
			return;
		}
	});

	pi.registerCommand("z0int-close", {
		description: "Close last open z0int turn with measured tokens + outcome",
		async handler(args, ctx) {
			try {
				const parts = String(args || "").trim().split(/\s+/).filter(Boolean);
				let measured: number | undefined;
				let success = true;
				for (let i = 0; i < parts.length; i++) {
					if (parts[i] === "--measured" && parts[i + 1]) measured = Number(parts[++i]);
					if (parts[i] === "--fail") success = false;
				}
				const closed = await closeOpenTurn({
					measured,
					success,
					testPass: success,
					toolOk: success,
					source: "z0int-close-cmd",
				});
				ctx.ui.notify(
					`z0int close: ${JSON.stringify({
						ok: !closed.error,
						trace: closed.trace_id || closed.receipt && (closed.receipt as Jsonish).trace_id,
						saved: closed.actual_tokens_saved,
						measured: closed.measured_frontier_tokens,
					})}`,
					closed.error ? "error" : "info",
				);
			} catch (e) {
				ctx.ui.notify(String(e), "error");
			}
		},
	});
}

export { preflight, turnBridge, closeOpenTurn };
