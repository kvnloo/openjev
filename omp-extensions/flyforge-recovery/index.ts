/**
 * FlyForge recovery controller for OMP (Hermes-compatible contract).
 * Resident python --worker: one process, stdin JSONL. Do not spawn per call.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createInterface, type Interface } from "node:readline";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const backend = fileURLToPath(new URL("./recovery.py", import.meta.url));
const DEFAULT_FAMILY = process.env.FLYFORGE_RECOVERY_FAMILY || "local_plasticity";
const WORKER_TIMEOUT_MS = 35_000;

type RecoveryResponse = {
	ok: boolean;
	error?: string;
	action?: string;
	guidance?: string;
	family?: string;
	source?: string;
	observed?: { harness: string; kind: string; tool?: string | null; fields: Record<string, unknown> };
	actions?: string[];
	locked_splits?: boolean;
	repo?: string;
};

type Pending = {
	resolve: (value: RecoveryResponse) => void;
	reject: (error: Error) => void;
	timer: unknown;
};

let worker: ChildProcess | null = null;
let lines: Interface | null = null;
const pending: Pending[] = [];

function failPending(error: Error): void {
	while (pending.length > 0) {
		const item = pending.shift();
		if (!item) break;
		clearTimeout(item.timer);
		item.reject(error);
	}
}

function startWorker(): ChildProcess {
	if (worker && worker.exitCode === null && worker.stdin) return worker;
	const child = spawn("/usr/bin/python3", ["-u", backend, "--worker"], {
		stdio: ["pipe", "pipe", "pipe"],
	});
	worker = child;
	lines = createInterface({ input: child.stdout! });
	lines.on("line", line => {
		const item = pending.shift();
		if (!item) return;
		clearTimeout(item.timer);
		try {
			item.resolve(JSON.parse(line) as RecoveryResponse);
		} catch (error) {
			item.reject(error instanceof Error ? error : new Error(String(error)));
		}
	});
	child.on("exit", code => {
		if (worker === child) {
			worker = null;
			lines?.close();
			lines = null;
		}
		failPending(new Error(`recovery worker exited ${code}`));
	});
	return child;
}

function request(params: Record<string, unknown>, signal?: AbortSignal): Promise<RecoveryResponse> {
	const child = startWorker();
	const stdin = child.stdin;
	if (!stdin) return Promise.reject(new Error("recovery worker has no stdin"));
	const { promise, resolve, reject } = Promise.withResolvers<RecoveryResponse>();
	const timer = setTimeout(() => {
		const index = pending.findIndex(item => item.reject === reject);
		if (index >= 0) pending.splice(index, 1);
		reject(new Error("recovery worker timed out"));
	}, WORKER_TIMEOUT_MS);
	const item: Pending = { resolve, reject, timer };
	pending.push(item);
	if (signal?.aborted) {
		pending.pop();
		clearTimeout(timer);
		reject(new Error("recovery backend cancelled or timed out"));
		return promise;
	}
	signal?.addEventListener(
		"abort",
		() => {
			const index = pending.indexOf(item);
			if (index >= 0) pending.splice(index, 1);
			clearTimeout(timer);
			reject(new Error("recovery backend cancelled or timed out"));
		},
		{ once: true },
	);
	stdin.write(`${JSON.stringify(params)}\n`);
	return promise;
}

function textFromContent(content: Array<{ type: string; text?: string }>): string {
	return content
		.filter(block => block.type === "text" && block.text)
		.map(block => block.text || "")
		.join("\n")
		.slice(0, 4000);
}

export default function flyforgeRecovery(pi: ExtensionAPI) {
	startWorker();
	const z = pi.zod;
	pi.setLabel("FlyForge recovery controller");

	const eventSchema = z
		.object({
			kind: z.string().optional(),
			type: z.string().optional(),
			tool: z.string().optional(),
			tool_name: z.string().optional(),
			message: z.string().optional(),
			error: z.string().optional(),
			is_error: z.boolean().optional(),
		})
		.optional();

	const fieldsSchema = z
		.object({
			sandbox_alive: z.number().optional(),
			retry: z.number().int().optional(),
			budget: z.number().optional(),
			transient: z.number().optional(),
			hard: z.number().optional(),
			unfamiliar: z.number().optional(),
			policy_hit: z.number().optional(),
			already_ok: z.number().optional(),
			cue: z.number().optional(),
			cue_seen: z.number().optional(),
			last_action: z.enum(["retry", "restart_sandbox", "escalate", "noop", "page_human"]).optional(),
		})
		.optional();

	const parameters = z.object({
		action: z.enum(["plan", "advise", "observe", "status"]).default("plan"),
		harness: z.enum(["omp", "hermes", "pi", "cursor"]).default("omp"),
		family: z.enum(["local_plasticity", "rule"]).default(DEFAULT_FAMILY as "local_plasticity" | "rule"),
		event: eventSchema,
		fields: fieldsSchema,
		retry: z.number().int().min(0).max(4).optional(),
		budget: z.number().min(0).max(1).optional(),
	});

	pi.registerTool({
		name: "recovery_advise",
		label: "Recovery Advise",
		description:
			"FlyForge Hermes recovery controller. Map a sanitized operational event to one bounded action: retry, restart_sandbox, escalate, noop, or page_human. Never pass secrets, tokens, credentials, or vault payloads.",
		parameters,
		loadMode: "essential",
		async execute(_id, params, signal) {
			const data = await request(
				{
					action: params.action === "plan" ? "plan" : params.action,
					harness: params.harness,
					family: params.family,
					event: params.event,
					fields: params.fields,
					retry: params.retry,
					budget: params.budget,
				},
				signal,
			);
			if (!data.ok) {
				return {
					content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
					details: data,
					isError: true,
				};
			}
			return {
				content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
				details: data,
			};
		},
	});

	pi.on("before_agent_start", event => ({
		systemPrompt: [
			...event.systemPrompt,
			"FlyForge recovery controller is available via recovery_advise. On tool/process failures, prefer a typed recovery handoff (sanitized event -> bounded action) over prose improvisation. Secrets fail closed; never pass tokens/credentials into recovery_advise.",
		],
	}));

	pi.on("tool_result", async event => {
		if (!event.isError) return;
		const message = textFromContent(event.content);
		try {
			const data = await request({
				action: "plan",
				harness: "omp",
				family: DEFAULT_FAMILY,
				event: {
					kind: "tool_error",
					tool: event.toolName,
					message,
					is_error: true,
				},
			});
			if (!data.ok || !data.action) return;
			const hint = [
				"",
				"[recovery_controller]",
				`suggested_action=${data.action}`,
				data.guidance || "",
				"Call recovery_advise for a typed handoff before widening scope.",
				"[/recovery_controller]",
			].join("\n");
			return {
				content: [...event.content, { type: "text", text: hint }],
			};
		} catch {
			return;
		}
	});

	pi.registerCommand("recovery", {
		description: "FlyForge recovery controller status and smoke test",
		async handler(_args, ctx) {
			try {
				const status = await request({ action: "status" });
				if (!status.ok) {
					ctx.ui.notify(status.error || "recovery status failed", "error");
					return;
				}
				ctx.ui.notify(
					`Recovery: repo=${status.repo} splits=${status.locked_splits ? "locked" : "missing"} family=${DEFAULT_FAMILY}`,
					status.locked_splits ? "info" : "warning",
				);
				const smoke = await request({
					action: "plan",
					harness: "omp",
					event: { kind: "tool_error", tool: "bash", message: "connection reset by peer" },
				});
				if (smoke.ok && smoke.action) {
					ctx.ui.notify(`Smoke: transient failure -> ${smoke.action}`, "info");
				}
			} catch (error) {
				ctx.ui.notify(String(error), "error");
			}
		},
	});
}
