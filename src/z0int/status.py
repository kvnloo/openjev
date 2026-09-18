"""Compact product status for humans and agents."""

from __future__ import annotations

from typing import Any

from . import doctor, models_mgmt, paths
from .onboard import load_state


def run_status() -> dict[str, Any]:
    rep = doctor.run_doctor()
    state = load_state()
    plan = rep.models_plan or {}
    data = (rep.discoveries or {}).get("data") or {}
    el = (rep.discoveries or {}).get("evolution_lab") or {}
    omp = (rep.discoveries or {}).get("omp") or {}
    kerd = (rep.discoveries or {}).get("kerdoios") or {}
    specialists = []
    sp = paths.home() / "specialists"
    if sp.is_dir():
        specialists = sorted(p.name for p in sp.iterdir() if p.suffix in {".npz", ".json", ".pt"})
    # best-effort rollup from receipts + bridge stream
    try:
        from .receipt import summarize_tokenomics

        avoided = summarize_tokenomics()
    except Exception:
        avoided = None
    return {
        "schema": "z0int.status.v1",
        "ok": rep.ok,
        "hardware": rep.hardware,
        "models": {
            "plan": plan.get("recommendation"),
            "resident": plan.get("resident"),
            "on_demand": plan.get("on_demand"),
        },
        "evolution_lab": el,
        "omp": {"linked": omp.get("linked"), "missing": omp.get("missing_links")},
        "kerdoios": kerd,
        "data": data,
        "onboard_steps": state.get("steps") or {},
        "specialists": specialists,
        "z0int_home": str(paths.home()),
        "tokenomics": avoided,
        "unresolved": rep.unresolved,
    }


def format_human(st: dict[str, Any]) -> str:
    lines = ["z0int status", ""]
    hw = st.get("hardware") or {}
    lines.append("Hardware")
    gpu = hw.get("gpu_name") or "none"
    vram = hw.get("vram_gb")
    mark = "✓" if hw.get("gpu_name") else "○"
    lines.append(f"{mark} {gpu}" + (f" {vram:.0f} GB" if isinstance(vram, (int, float)) else ""))
    lines.append(f"{'✓' if hw.get('python') else '✗'} Python {hw.get('python')}")
    lines.append("")
    lines.append("Models")
    m = st.get("models") or {}
    lines.append(f"  {m.get('plan')}")
    lines.append("")
    lines.append("Evolution")
    el = st.get("evolution_lab") or {}
    lines.append(
        f"{'✓' if el.get('found') or el.get('importable') else '○'} evolution-lab "
        f"{el.get('path') or ''} {el.get('branch') or ''}"
    )
    lines.append("")
    lines.append("Data")
    data = st.get("data") or {}
    if not data:
        lines.append("○ no sources discovered yet")
    for k, v in data.items():
        if isinstance(v, dict) and v.get("path"):
            lines.append(f"✓ {k}: {v['path']}")
        else:
            lines.append(f"✓ {k}")
    lines.append("")
    lines.append("Live / hooks")
    omp = st.get("omp") or {}
    linked = omp.get("linked") or []
    lines.append(f"{'✓' if linked else '○'} OMP extensions: {', '.join(linked) or 'none'}")
    kerd = st.get("kerdoios") or {}
    lines.append(f"{'✓' if kerd.get('found') or kerd.get('importable') else '○'} Kerdoios (optional)")
    lines.append("")
    lines.append("Specialists")
    specs = st.get("specialists") or []
    if specs:
        for s in specs:
            lines.append(f"✓ {s}")
    else:
        lines.append("○ none under ~/.z0int/specialists yet")
    tok = st.get("tokenomics")
    if tok:
        lines.append("")
        lines.append("Tokenomics (receipts + bridge)")
        lines.append(f"  frontier tokens avoided ≈ {tok.get('frontier_tokens_avoided_est')} over {tok.get('rows')} rows")
    if st.get("unresolved"):
        lines.append("")
        lines.append("Unresolved")
        for u in st["unresolved"]:
            lines.append(f"  - {u}")
    lines.append("")
    lines.append(f"home: {st.get('z0int_home')}")
    return "\n".join(lines)
