/**
 * Shadow only. Native Jev remains the teacher.
 * Logs wave-5 next-action champion + jev-distill fly. Never injects skill_relevance.
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

function runPy(args: string[], timeoutMs: number): Promise<{ code: number; stdout: string; stderr: string }> {
	return new Promise(resolve => {
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
	});
}

async function shadow(prompt: string): Promise<void> {
	const [next, jev] = await Promise.all([
		runPy(["-m", "evolution_lab", "next-action-predict", prompt], 8000),
		runPy(["-m", "evolution_lab", "jev-predict", prompt], 8000),
	});
	const parse = (r: { code: number; stdout: string }) => {
		if (r.code !== 0) return { ok: false, error: "nonzero" };
		try {
			return JSON.parse(r.stdout);
		} catch {
			return { ok: false, error: "json" };
		}
	};
	const row = {
		ts: Date.now() / 1000,
		prompt: prompt.slice(0, 400),
		next_action: parse(next),
		jev: parse(jev),
		disagree:
			parse(next).ok && parse(jev).ok ? parse(next).label !== parse(jev).label : null,
	};
	mkdirSync(join(homedir(), ".z0int", "shadow"), { recursive: true });
	appendFileSync(LOG, JSON.stringify(row) + "\n");
}

export default function flyforgeJevShadow(pi: ExtensionAPI) {
	pi.setLabel("Fly shadow (log only)");
	pi.on("before_agent_start", async event => {
		const prompt = String((event as { prompt?: string }).prompt || "").trim();
		if (!prompt || prompt.startsWith("/")) return;
		try {
			await shadow(prompt);
		} catch {
			return;
		}
	});
}

export { shadow };
