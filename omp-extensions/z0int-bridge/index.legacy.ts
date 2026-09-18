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
const closingTraces = new Set<string>();

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
	const cf =
		pf.counterfactual && typeof pf.counterfactual === "object"
			? (pf.counterfactual as Jsonish)
			: null;
	const wr =
		pf.work_requirement && typeof pf.work_requirement === "object"
			? (pf.work_requirement as Jsonish)
			: null;
	const baselineIn =
		typeof pf.baseline_input_tokens === "number"
			? pf.baseline_input_tokens
			: typeof wr?.estimated_input_tokens === "number"
				? (wr.estimated_input_tokens as number)
				: typeof cf?.estimated_input_tokens === "number"
					? (cf.estimated_input_tokens as number)
					: null;
	const baselineOut =
		typeof pf.baseline_output_tokens === "number"
			? pf.baseline_output_tokens
			: typeof wr?.estimated_output_tokens === "number"
				? (wr.estimated_output_tokens as number)
				: typeof cf?.estimated_output_tokens === "number"
					? (cf.estimated_output_tokens as number)
					: null;
	const avoided =
		typeof pf.estimated_frontier_tokens_avoided === "number"
			? pf.estimated_frontier_tokens_avoided
			: 0;
	const isLocal = route === "local";
	// Prefer concrete placement from Kerdoios when residual is planned.
	let planProvider: string | null = null;
	let planModel: string | null = null;
	if (plan && Array.isArray(plan.placements) && plan.placements.length > 0) {
		const top = plan.placements[0] as Jsonish;
		if (typeof top.provider === "string") planProvider = top.provider;
		if (typeof top.model === "string") planModel = top.model;
	}
	const receipt: Jsonish = {
		schema: "z0int.decision_receipt.v1",
		trace_id: traceId,
		session_id: sessionId ?? process.env.OMP_SESSION_ID ?? null,
		capability_id: capabilityId,
		provider: isLocal ? "local_mb" : planProvider || (plan ? "kerdoios_plan" : "frontier"),
		model: isLocal ? "mb_local" : planModel,
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
	provider: string | null;
	model: string | null;
	usage_source: "provider_usage" | "char_proxy";
} {
	// Prefer real provider usage on the last assistant message when present.
	let provider: string | null = null;
	let model: string | null = null;
	let usageIn: number | null = null;
	let usageOut: number | null = null;
	for (let i = (messages || []).length - 1; i >= 0; i--) {
		const m = messages[i];
		if (!m || typeof m !== "object") continue;
		const role = String((m as { role?: unknown }).role || "");
		if (role && role !== "assistant") continue;
		const p = (m as { provider?: unknown }).provider;
		const mo = (m as { model?: unknown }).model;
		if (typeof p === "string" && p) provider = p;
		if (typeof mo === "string" && mo) model = mo;
		const usage = (m as { usage?: unknown }).usage;
		if (usage && typeof usage === "object") {
			const u = usage as {
				input?: unknown;
				output?: unknown;
				input_tokens?: unknown;
				output_tokens?: unknown;
				totalTokens?: unknown;
			};
			const inn = u.input ?? u.input_tokens;
			const out = u.output ?? u.output_tokens;
			if (typeof inn === "number") usageIn = inn;
			if (typeof out === "number") usageOut = out;
			if (usageIn != null || usageOut != null) break;
		}
		// also accept nested message wrapper
		const nested = (m as { message?: unknown }).message;
		if (nested && typeof nested === "object") {
			const nm = nested as {
				role?: unknown;
				provider?: unknown;
				model?: unknown;
				usage?: unknown;
			};
			if (String(nm.role || "assistant") === "assistant") {
				if (typeof nm.provider === "string") provider = nm.provider;
				if (typeof nm.model === "string") model = nm.model;
				const usage2 = nm.usage;
				if (usage2 && typeof usage2 === "object") {
					const u2 = usage2 as { input?: unknown; output?: unknown };
					if (typeof u2.input === "number") usageIn = u2.input;
					if (typeof u2.output === "number") usageOut = u2.output;
					if (usageIn != null || usageOut != null) break;
				}
			}
		}
	}
	if (usageIn != null || usageOut != null) {
		const input_tokens = Math.max(0, Math.round(usageIn || 0));
		const output_tokens = Math.max(0, Math.round(usageOut || 0));
		return {
			input_tokens,
			output_tokens,
			measured: input_tokens + output_tokens,
			provider,
			model,
			usage_source: "provider_usage",
		};
	}
	// Fallback proxy when harness does not expose provider usage: char/4.
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
	return {
		input_tokens,
		output_tokens,
		measured: input_tokens + output_tokens,
		provider,
		model,
		usage_source: "char_proxy",
	};
}

async function closeOpenTurn(opts: {
	traceId?: string;
	measured?: number;
	inputTokens?: number;
	outputTokens?: number;
	success?: boolean;
	testPass?: boolean;
	toolOk?: boolean;
	executionCompleted?: boolean;
	verifiedSuccess?: boolean;
	source?: string;
	provider?: string;
	model?: string;
}): Promise<Jsonish> {
	const last = readLast();
	const traceId = opts.traceId || (last && typeof last.trace_id === "string" ? last.trace_id : null);
	if (!traceId) return { ok: false, error: "no_open_trace" };
	if (last && last.trace_id === traceId && last.closed === true) {
		return { ok: true, already_closed: true, trace_id: traceId };
	}
	if (closingTraces.has(traceId)) {
		return { ok: true, already_closed: true, trace_id: traceId };
	}
	closingTraces.add(traceId);

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
	// Evidence split: execution vs verified. Never gold from bare success/toolOk.
	const execDone = opts.executionCompleted ?? true;
	args.push("--execution-completed", execDone ? "true" : "false");
	if (opts.verifiedSuccess != null) {
		args.push("--verified-success", opts.verifiedSuccess ? "true" : "false");
	}
	if (opts.testPass != null) args.push("--test-pass", opts.testPass ? "true" : "false");
	// tool_ok / success only when explicitly provided — soft tier, not gold
	if (opts.toolOk != null) args.push("--tool-ok", opts.toolOk ? "true" : "false");
	if (opts.success != null && opts.verifiedSuccess == null && opts.testPass == null) {
		// legacy: map bare success to soft flag only (not verified)
		args.push("--success", opts.success ? "true" : "false");
	}

	const r = await runCmd(Z0_PY, args, Z0_ROOT, 5000);
	const closed = parseJson(r);
	const ok = !closed.error && closed.ok !== false;
	try {
		append(HEART, {
			schema: "z0int.bridge_close_heart.v1",
			ts: Date.now() / 1000,
			trace_id: traceId,
			ok,
			source: opts.source || "bridge_agent_end",
			measured: opts.measured ?? null,
			provider: opts.provider ?? null,
			model: opts.model ?? null,
			execution_completed: execDone,
			verified_success: opts.verifiedSuccess ?? null,
			outcome_tier: (closed.outcome_join as Jsonish | undefined)?.outcome_tier ?? null,
			error: closed.error ?? (r.code !== 0 ? r.stderr.slice(0, 200) : null),
		});
	} catch {
		/* */
	}
	if (ok) {
		try {
			if (existsSync(LAST)) {
				const cur = readLast();
				if (cur && cur.trace_id === traceId) {
					writeFileSync(LAST, "");
				}
			}
			writeLast({
				schema: "z0int.open_turn.v1",
				trace_id: traceId,
				closed: true,
				closed_ts: Date.now() / 1000,
				measured: opts.measured ?? null,
				source: opts.source || "bridge_agent_end",
				provider: opts.provider ?? null,
				model: opts.model ?? null,
			});
		} catch {
			/* */
		}
	}

	// Kerdoios observed economics (best-effort).
	// Only mark completed when verified — bare turn_end is execution, not verified task.
	try {
		const cap =
			(last && typeof last.capability_id === "string" && last.capability_id) ||
			"coding.next_action";
		const kProvider = opts.provider || "omp_bridge";
		const kModel = opts.model || "unknown";
		const kArgs = [
			"-m",
			"kerdoios",
			"record",
			"--provider",
			kProvider,
			"--model",
			kModel,
			"--task-type",
			"coding",
			"--capability-id",
			cap,
			"--cost",
			"0",
		];
		const verified =
			opts.verifiedSuccess === true || opts.testPass === true;
		if (verified) kArgs.push("--completed");
		if (opts.inputTokens != null) kArgs.push("--input-tokens", String(opts.inputTokens));
		if (opts.outputTokens != null) kArgs.push("--output-tokens", String(opts.outputTokens));
		await runCmd(KERD_PY, kArgs, KERD_ROOT, 5000);
	} catch {
		/* */
	}
	return closed;
}


async function closeFromMessages(messages: unknown[], source: string): Promise<Jsonish> {
	const est = estimateMeasuredFromMessages(messages);
	for (let i = 0; i < 20; i++) {
		const last = readLast();
		if (last && last.closed !== true && typeof last.trace_id === "string") break;
		await new Promise((r) => setTimeout(r, 50));
	}
	const last = readLast();
	if (!last || typeof last.trace_id !== "string") return { ok: false, error: "no_open_trace" };
	if (last.closed === true) return { ok: true, already_closed: true, trace_id: last.trace_id };
	const tid = last.trace_id as string;
	if (closingTraces.has(tid)) return { ok: true, already_closed: true, trace_id: tid };
	return closeOpenTurn({
		measured: est.measured,
		inputTokens: est.input_tokens,
		outputTokens: est.output_tokens,
		executionCompleted: true,
		// verified_success stays null until async join (CI/tests/user)
		source,
		provider: est.provider || undefined,
		model: est.model || undefined,
	});
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

	// turn_end is awaited by the harness — preferred close path (print-mode safe).
	pi.on("turn_end", async (event) => {
		try {
			const msg =
				event && typeof event === "object" && "message" in event
					? (event as { message?: unknown }).message
					: null;
			const messages = msg ? [msg] : [];
			await closeFromMessages(messages, "bridge_turn_end");
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
			await closeFromMessages(messages, "bridge_agent_end");
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
				let verified = false;
				for (let i = 0; i < parts.length; i++) {
					if (parts[i] === "--measured" && parts[i + 1]) measured = Number(parts[++i]);
					if (parts[i] === "--verified") verified = true;
				}
				const closed = await closeOpenTurn({
					measured,
					executionCompleted: true,
					verifiedSuccess: verified ? true : undefined,
					testPass: verified ? true : undefined,
					source: "z0int-close-cmd",
				});
				let tier: unknown = null;
				const oj = closed.outcome_join;
				if (oj && typeof oj === "object" && "outcome_tier" in oj) {
					tier = (oj as { outcome_tier?: unknown }).outcome_tier;
				}
				let trace: unknown = closed.trace_id;
				const rec = closed.receipt;
				if (!trace && rec && typeof rec === "object" && "trace_id" in rec) {
					trace = (rec as { trace_id?: unknown }).trace_id;
				}
				ctx.ui.notify(
					`z0int close: ${JSON.stringify({
						ok: !closed.error,
						trace,
						saved: closed.actual_tokens_saved,
						measured: closed.measured_frontier_tokens,
						tier,
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
