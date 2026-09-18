/**
 * Shadow only. Native Jev remains the teacher.
 * Logs wave-5 next-action coverage cascade + jev-distill fly.
 * Never injects skill_relevance.
 *
 * Cascade (safe coverage):
 *   high-conf EXECUTE/DELEGATE → route=local (still log-only)
 *   else → route=escalate (escalate_to=jev, fallback=openjev)
 */
import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const LOG = join(homedir(), ".z0int", "shadow", "jev-fly.jsonl");
const PY =
	process.env.EVOLUTION_LAB_PYTHON ||
	"/workspace/evolution-lab/.venv/bin/python";
const CWD = process.env.EVOLUTION_LAB_ROOT || "/workspace/evolution-lab";

type PyResult = { code: number; stdout: string; stderr: string };
type Jsonish = Record<string, unknown> & { ok?: boolean; error?: string };

async function runPy(args: string[], timeoutMs: number): Promise<PyResult> {
	const { promise, resolve } = Promise.withResolvers<PyResult>();
	const child = spawn(PY, args, { cwd: CWD, stdio: ["ignore", "pipe", "pipe"] });
	let stdout = "";
	let stderr = "";
	const timer = setTimeout(() => {
		child.kill("SIGKILL");
	}, timeoutMs);
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
		resolve({ code: 1, stdout: "", stderr: String(err) });
	});
	return promise;
}

function parseJson(r: PyResult): Jsonish {
	if (r.code !== 0) return { ok: false, error: "nonzero" };
	try {
		const value: unknown = JSON.parse(r.stdout);
		if (value && typeof value === "object") return value as Jsonish;
		return { ok: false, error: "json" };
	} catch {
		return { ok: false, error: "json" };
	}
}

async function shadow(prompt: string): Promise<void> {
	const [decideRaw, jevRaw] = await Promise.all([
		runPy(["-m", "evolution_lab", "next-action-decide", prompt], 8000),
		runPy(["-m", "evolution_lab", "jev-predict", prompt], 8000),
	]);
	const decision = parseJson(decideRaw);
	const jevOut = parseJson(jevRaw);
	const next =
		decision.prediction && typeof decision.prediction === "object"
			? (decision.prediction as Jsonish)
			: decision;
	const route = typeof decision.route === "string" ? decision.route : null;
	let teacher: Jsonish | null = null;
	if (route === "escalate" && jevOut.ok) {
		teacher = { source: jevOut.source, label: jevOut.label, p: jevOut.p };
	} else if (route === "local") {
		teacher = { source: "next_action_local", label: decision.label, p: decision.p };
	}
	const nextOk = Boolean(next.ok);
	const jevOk = Boolean(jevOut.ok);
	const row = {
		ts: Date.now() / 1000,
		prompt: prompt.slice(0, 400),
		decision: decision.ok
			? {
					route: decision.route,
					reason: decision.reason,
					escalate_to: decision.escalate_to,
					fallback: decision.fallback,
					label: decision.label,
					p: decision.p,
					margin: decision.margin,
				}
			: decision,
		next_action: next,
		jev: jevOut,
		teacher,
		disagree: nextOk && jevOk ? next.label !== jevOut.label : null,
	};
	mkdirSync(join(homedir(), ".z0int", "shadow"), { recursive: true });
	appendFileSync(LOG, JSON.stringify(row) + "\n");
}

export default function flyforgeJevShadow(pi: ExtensionAPI) {
	pi.setLabel("Fly shadow (coverage cascade, log only)");
	pi.on("before_agent_start", async event => {
		const prompt =
			event && typeof event === "object" && "prompt" in event
				? String((event as { prompt?: unknown }).prompt ?? "").trim()
				: "";
		if (!prompt || prompt.startsWith("/")) return;
		try {
			await shadow(prompt);
		} catch {
			return;
		}
	});
}

export { shadow };
