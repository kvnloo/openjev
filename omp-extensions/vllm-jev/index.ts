/**
 * vLLM DiffusionGemma structured reads — parallel to TypeSafe Jev and FlyForge.
 * Does not replace providers.judgmentProvider. Fail-open. Shadow-log only.
 *
 * Env: VLLM_UPSTREAM (default http://127.0.0.1:8000), VLLM_MODEL, VLLM_TOKENIZER,
 * VLLM_TIMEOUT_MS (default 2500), VLLM_JEV_SHADOW=0 to disable shadow calls.
 */
import { appendFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const UPSTREAM = (process.env.VLLM_UPSTREAM || "http://127.0.0.1:8000").replace(/\/$/, "");
const MODEL = process.env.VLLM_MODEL || "dgemma";
const TIMEOUT_MS = Number(process.env.VLLM_TIMEOUT_MS || "2500");
const SHADOW = process.env.VLLM_JEV_SHADOW !== "0";
const LOG = join(homedir(), ".omp/agent/extensions/vllm-jev/route.log");

type Option = { id: string; description: string };

function log(kind: string, detail: string): void {
	try {
		appendFileSync(LOG, `${new Date().toISOString()}\t${kind}\t${detail}\n`);
	} catch {
		/* ignore */
	}
}

async function chat(body: unknown, timeoutMs: number, signal?: AbortSignal): Promise<unknown> {
	const ac = new AbortController();
	const timer = setTimeout(() => ac.abort(), timeoutMs);
	if (signal) {
		if (signal.aborted) ac.abort();
		else signal.addEventListener("abort", () => ac.abort(), { once: true });
	}
	try {
		const res = await fetch(`${UPSTREAM}/v1/chat/completions`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
			signal: ac.signal,
		});
		if (!res.ok) {
			const text = await res.text().catch(() => "");
			throw new Error(`vLLM ${res.status} ${text.slice(0, 180)}`);
		}
		return await res.json();
	} finally {
		clearTimeout(timer);
	}
}

async function ping(timeoutMs = 400): Promise<boolean> {
	try {
		const res = await fetch(`${UPSTREAM}/v1/models`, { signal: AbortSignal.timeout(timeoutMs) });
		return res.ok;
	} catch {
		return false;
	}
}

function letterBody(state: string, question: string, options: Option[]) {
	const letters = "ABCDEFGHIJKLMNOP";
	const listed = options.slice(0, letters.length);
	const lines = listed.map((opt, i) => `  ${letters[i]}: ${opt.description}`).join("\n");
	return {
		model: MODEL,
		messages: [
			{
				role: "system",
				content:
					`Answer one decision question. Reply with exactly one option letter.\n\nQuestion decision: ${question}\n${lines}\n\nReply as "decision: label".`,
			},
			{ role: "user", content: JSON.stringify({ evidence: state }) },
		],
		max_tokens: 32,
		logprobs: true,
		top_logprobs: 8,
		chat_template_kwargs: { enable_thinking: false },
		vllm_xargs: {
			diffusion_max_steps: 1,
			diffusion_read_only: true,
		},
	};
}

export default function vllmJev(pi: ExtensionAPI) {
	pi.setLabel("vLLM Jev sidecar (parallel, fail-open)");
	const z = pi.zod;

	pi.registerTool({
		name: "vllm_decide",
		label: "vLLM Decide",
		description:
			"Jev-like typed choice via vLLM DiffusionGemma (PR #57250). Parallel to TypeSafe Jev and FlyForge recovery. Fail-open if the server is down.",
		parameters: z.object({
			state: z.string(),
			question: z.string(),
			options: z
				.array(z.object({ id: z.string(), description: z.string() }))
				.min(2)
				.max(16),
		}),
		loadMode: "essential",
		async execute(_id, params, signal) {
			const started = Date.now();
			try {
				const data = await chat(letterBody(params.state, params.question, params.options), TIMEOUT_MS, signal);
				const ms = Date.now() - started;
				log("decide", `ok ${ms}ms model=${MODEL}`);
				return {
					content: [{ type: "text", text: JSON.stringify({ ok: true, ms, backend: "vllm", model: MODEL, data }) }],
					details: { ok: true, ms, backend: "vllm" },
				};
			} catch (error) {
				const ms = Date.now() - started;
				const message = error instanceof Error ? error.message : String(error);
				log("decide", `fail ${ms}ms ${message}`);
				return {
					content: [
						{
							type: "text",
							text: JSON.stringify({ ok: false, ms, backend: "vllm", error: message, fail_open: true }),
						},
					],
					details: { ok: false, ms, fail_open: true },
				};
			}
		},
	});

	if (SHADOW) {
		pi.on("before_agent_start", event => {
			void (async () => {
				const started = Date.now();
				try {
					await chat(
						letterBody(
							event.prompt.slice(0, 800),
							"Which skill should handle this request?",
							[
								{ id: "shell", description: "Run a local command" },
								{ id: "none_of_these", description: "No listed skill fits" },
							],
						),
						TIMEOUT_MS,
					);
					log("shadow", `ok ${Date.now() - started}ms`);
				} catch (error) {
					log("shadow", `fail ${Date.now() - started}ms ${error instanceof Error ? error.message : String(error)}`);
				}
			})();
			return;
		});
	}

	pi.on("before_agent_start", event => ({
		systemPrompt: [
			...event.systemPrompt,
			"vLLM Jev sidecar is available via vllm_decide (DiffusionGemma structured reads). It runs in parallel with TypeSafe Jev and FlyForge recovery_advise. Fail-open if VLLM_UPSTREAM is down. Do not set /model to Jev or vLLM.",
		],
	}));

	pi.registerCommand("vllm", {
		description: "vLLM Jev sidecar status (parallel to TypeSafe and FlyForge)",
		async handler(_args, ctx) {
			const up = await ping();
			ctx.ui.notify(
				up
					? `vLLM sidecar up: ${MODEL} @ ${UPSTREAM} (timeout ${TIMEOUT_MS}ms, shadow=${SHADOW})`
					: `vLLM sidecar down: ${UPSTREAM} unreachable — fail-open. TypeSafe Jev and FlyForge still run.`,
				up ? "info" : "warning",
			);
		},
	});
}
