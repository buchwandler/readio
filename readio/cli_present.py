from __future__ import annotations

from . import api as public_api


def format_plan_human(plan: public_api.ResolvedPlan) -> str:
    """Format a resolved public plan for terminal output."""
    lines = [
        "Input",
        f"  Source:    {plan.input.source_path or '(stdin)'}",
        f"  Format:    {plan.input.format}",
        f"  SHA256:    {plan.input.source_sha256[:16]}...",
        "",
        "Planning",
        f"  Language:  {plan.planning.language}",
        f"  Unit:      {plan.planning.unit}",
        f"  Pause:     {plan.planning.pause_mode}",
        "",
        "Semantic plan",
        f"  Plan ID:   {plan.semantic_plan.plan_id or '(none)'}",
        f"  SHA256:    {plan.semantic_plan.sha256 or '(none)'}",
    ]
    if plan.render is not None:
        lines.extend(
            [
                "",
                "Render",
                f"  Engine:    {plan.render.engine}",
                f"  Target:    {plan.render.target.id}",
                f"  Voice:     {plan.render.target.voice or '(none)'}",
                f"  Render ID: {plan.render.render_id or '(none)'}",
            ]
        )
    if plan.output.format is not None:
        lines.extend(["", "Output", f"  Format:    {plan.output.format}"])
    if plan.diagnostics:
        lines.extend(["", "Diagnostics"])
        lines.extend(f"  [{item.code}] {item.message}" for item in plan.diagnostics)
    lines.append("")
    lines.append("Plan is executable." if plan.ok else "Plan has errors.")
    return "\n".join(lines)
