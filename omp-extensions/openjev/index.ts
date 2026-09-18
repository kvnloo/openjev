/**
 * OpenJev (z0int) typed decisions for OMP. Parallel to TypeSafe Jev and FlyForge.
 * Resident Python worker loads Qwen3.5-4B once. Fail-open. Does not replace judgmentProvider.
 *
 * Env: OPENJEV_PYTHON, OPENJEV_MODEL, OPENJEV_REVISION, OPENJEV_TIMEOUT_MS,
 * OPENJEV_MAX_TOKENS, CUDA_VISIBLE_DEVICES, HF_HOME.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { appendFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { createInterface, type Interface } from "node:readline";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const here = dirname(fileURLToPath(import.meta.url));
const workerPath = join(here, "worker.py");
const PYTHON = process.env.OPENJEV_PYTHON || "/home/kvn/tmp/openjev/.venv/bin/python";
const MODEL = process.env.OPENJEV_MODEL || "Qwen/Qwen3.5-4B";
const REVISION = process.env.OPENJEV_REVISION || "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a";
const TIMEOUT_MS = Number(process.env.OPENJEV_TIMEOUT_MS || "120000");
const LOG = join(homedir(), ".omp/agent/extensions/openjev/route.log");

type Option = { id: string; description: string };
type OpenJevResponse = {
	ok: boolean;
	error?: string;
	fail_open?: boolean;
	ready?: boolean;
	id?: string;
	option_ids?: string[];
	probabilities?: number[];
	forward_seconds?: number;
	total_seconds?: number;
	model?: unknown;
};

type Pending = {
	resolve: (value: OpenJevResponse) => void;
	reject: (error: Error) => void;
	timer: unknown;
};

let worker: ChildProcess | null = null;
let lines: Interface | null = null;
const pending: Pending[] = [];

function log(kind: string, detail: string): void {
	try {
		appendFileSync(LOG, `${new Date().toISOString()}\t${kind}\t${detail}\n`);
	} catch {
		/* ignore */
	}
}

function failPending(error: Error): void {
	while (pending.length > 0) {
		const item = pending.shift();
		if (!item) break;
		clearTimeout(item.timer as NodeJS.Timeout);
		item.reject(error);
	}
}

function startWorker(): ChildProcess {
	if (worker && worker.exitCode === null && worker.stdin) return worker;
	const child = spawn(PYTHON, ["-u", workerPath, "--worker"], {
		stdio: ["pipe", "pipe", "pipe"],
		env: {
			...process.env,
			OPENJEV_MODEL: MODEL,
			OPENJEV_REVISION: REVISION,
			CUDA_VISIBLE_DEVICES: process.env.CUDA_VISIBLE_DEVICES || "0",
			HF_HOME: process.env.HF_HOME || "/mnt/zer0models/zer0-models/huggingface",
		},
	});
	worker = child;
	lines = createInterface({ input: child.stdout! });
	lines.on("line", line => {
		const item = pending.shift();
		if (!item) {
			log("orphan", line.slice(0, 180));
			return;
		}
		clearTimeout(item.timer as NodeJS.Timeout);
		try {
			item.resolve(JSON.parse(line) as OpenJevResponse);
		} catch (error) {
			item.reject(error instanceof Error ? error : new Error(String(error)));
		}
	});
	child.stderr?.on("data", chunk => log("stderr", String(chunk).slice(0, 400)));
	child.on("exit", code => {
		if (worker === child) {
			worker = null;
			lines?.close();
			lines = null;
		}
		failPending(new Error(`openjev worker exited ${code}`));
	});
	return child;
}

function request(params: Record<string, unknown>, signal?: AbortSignal): Promise<OpenJevResponse> {
	const child = startWorker();
	const stdin = child.stdin;
	if (!stdin) return Promise.reject(new Error("openjev worker has no stdin"));
	const { promise, resolve, reject } = Promise.withResolvers<OpenJevResponse>();
	const timer = setTimeout(() => {
		const index = pending.findIndex(item => item.reject === reject);
		if (index >= 0) pending.splice(index, 1);
		reject(new Error("openjev worker timed out"));
	}, TIMEOUT_MS);
	const item: Pending = { resolve, reject, timer };
	pending.push(item);
	if (signal?.aborted) {
		pending.pop();
		clearTimeout(timer);
		reject(new Error("openjev cancelled"));
		return promise;
	}
	signal?.addEventListener(
		"abort",
		() => {
			const index = pending.indexOf(item);
			if (index >= 0) pending.splice(index, 1);
			clearTimeout(timer);
			reject(new Error("openjev cancelled"));
		},
		{ once: true },
	);
	stdin.write(`${JSON.stringify(params)}\n`);
	return promise;
}

export default function openjevExtension(pi: ExtensionAPI) {
	const z = pi.zod;
	pi.setLabel("OpenJev typed decisions (z0int)");
	startWorker();

	pi.registerTool({
		name: "openjev_decide",
		label: "OpenJev Decide",
		description:
			"Typed option probabilities from local OpenJev (Qwen3.5-4B direct logits). Parallel to TypeSafe Jev. Fail-open if the SLM worker is down.",
		parameters: z.object({
			state: z.union([z.string(), z.record(z.string(), z.unknown()), z.array(z.unknown())]),
			question: z.string(),
			options: z
				.array(z.object({ id: z.string(), description: z.string() }))
				.min(2)
				.max(16),
			id: z.string().optional(),
		}),
		loadMode: "essential",
		async execute(_id, params, signal) {
			const started = Date.now();
			try {
				const data = await request(
					{
						id: params.id || "omp",
						state: params.state,
						question: params.question,
						options: params.options,
					},
					signal,
				);
				const ms = Date.now() - started;
				log("decide", `${data.ok ? "ok" : "fail"} ${ms}ms`);
				return {
					content: [{ type: "text", text: JSON.stringify({ ...data, ms, backend: "openjev" }) }],
					details: { ok: data.ok, ms, backend: "openjev" },
					isError: !data.ok,
				};
			} catch (error) {
				const ms = Date.now() - started;
				const message = error instanceof Error ? error.message : String(error);
				log("decide", `fail ${ms}ms ${message}`);
				return {
					content: [
						{
							type: "text",
							text: JSON.stringify({ ok: false, ms, backend: "openjev", error: message, fail_open: true }),
						},
					],
					details: { ok: false, ms, fail_open: true },
				};
			}
		},
	});

	pi.on("before_agent_start", event => ({
		systemPrompt: [
			...event.systemPrompt,
			"OpenJev is available via openjev_decide (local Qwen3.5-4B direct option logits). Parallel to TypeSafe Jev and FlyForge recovery_advise. Fail-open if the worker is down. Do not set /model to Jev or OpenJev.",
		],
	}));

	pi.registerCommand("openjev", {
		description: "OpenJev worker status (local SLM, parallel to TypeSafe)",
		async handler(_args, ctx) {
			try {
				const status = await request({ action: "status" });
				if (!status.ok) {
					ctx.ui.notify(status.error || "OpenJev worker not ready — fail-open", "warning");
					return;
				}
				ctx.ui.notify(`OpenJev ready: ${MODEL} @ ${REVISION.slice(0, 12)}`, "info");
			} catch (error) {
				ctx.ui.notify(`OpenJev down: ${String(error)} — fail-open. TypeSafe Jev still runs.`, "warning");
			}
		},
	});
}
