/**
 * OpenJev 0.6B test lane (z0int). Same worker as 4B, smaller SLM for latency.
 * Does not replace judgmentProvider or the 4B openjev_decide path.
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
const MODEL = "Qwen/Qwen3-0.6B";
const REVISION = "c1899de289a04d12100db370d81485cdf75e47ca";
const TIMEOUT_MS = Number(process.env.OPENJEV_06B_TIMEOUT_MS || "120000");
const LOG = join(homedir(), ".omp/agent/extensions/openjev-06b/route.log");

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

export default function openjev06bExtension(pi: ExtensionAPI) {
	const z = pi.zod;
	pi.setLabel("OpenJev 0.6B test lane (z0int)");

	pi.registerTool({
		name: "openjev_decide_06b",
		label: "OpenJev Decide 0.6B",
		description:
			"Faster OpenJev test lane: Qwen3-0.6B direct option logits. Same API as openjev_decide (4B). Fail-open. Do not load both SLMs at once on 12GB.",
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
						id: params.id || "omp-06b",
						state: params.state,
						question: params.question,
						options: params.options,
					},
					signal,
				);
				const ms = Date.now() - started;
				log("decide", `${data.ok ? "ok" : "fail"} ${ms}ms`);
				return {
					content: [{ type: "text", text: JSON.stringify({ ...data, ms, backend: "openjev-06b" }) }],
					details: { ok: data.ok, ms, backend: "openjev-06b" },
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
							text: JSON.stringify({ ok: false, ms, backend: "openjev-06b", error: message, fail_open: true }),
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
			"OpenJev 0.6B test lane: openjev_decide_06b (Qwen3-0.6B). Faster/weaker than openjev_decide (4B). Do not load both workers on 12GB VRAM.",
		],
	}));

	pi.registerCommand("openjev06", {
		description: "OpenJev 0.6B worker status",
		async handler(_args, ctx) {
			try {
				const status = await request({ action: "status" });
				if (!status.ok) {
					ctx.ui.notify(status.error || "OpenJev 0.6B not ready — fail-open", "warning");
					return;
				}
				ctx.ui.notify(`OpenJev 0.6B ready: ${MODEL} @ ${REVISION.slice(0, 12)}`, "info");
			} catch (error) {
				ctx.ui.notify(`OpenJev 0.6B down: ${String(error)}`, "warning");
			}
		},
	});
}
