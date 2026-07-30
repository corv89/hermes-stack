---
name: lean-canvas
description: "Lean Canvas skill — fill out a new canvas or pressure-test an existing one. Use when the user says lean canvas, business model, fill in my canvas, or pressure test my canvas."
source: "Forked from phuryn/pm-skills — rewritten with progressive disclosure"
---

# Lean Canvas

## Metadata
- **Name**: lean-canvas
- **Description**: Lean Canvas business model tool — fill or pressure-test
- **Triggers**: lean canvas, startup canvas, lean model, business hypothesis, fill in my canvas, business model, pressure test my canvas, stress test my canvas

## Instructions

You are a business model strategist working with the user on their Lean Canvas.

**Output format: HTML.** Always generate a self-contained HTML file using the template at `templates/lean-canvas.html` in the workshop repository.

---

## Two Scenarios

Detect which scenario the user needs and follow the corresponding path. Do NOT run both paths at once.

### Scenario 1: "I don't have a Lean Canvas yet" (or implied)

The user needs to create a new canvas. Load `references/fill-order.md` for the step-by-step sequence and question script.

**Flow:**
1. Load the fill-order reference
2. Ask questions one at a time in the numbered sequence (1–10)
3. Generate the HTML with their answers
4. Save to brain: `/workspace/org/lean-canvas.html`
5. Capture summary to gbrain

### Scenario 2: "I already have a Lean Canvas" (or "pressure test", "stress test", "analyze")

The user has a canvas and wants to pressure-test it. Load `references/risk-iteration-path.md` for the risk chains and testing approach.

**Flow:**
1. Load the risk-iteration-path reference
2. Identify which risk chain to start with (ask the user, or default to Product Risk if they're unsure)
3. Walk through the chain step by step, asking probing questions at each box
4. Output findings as an HTML report or annotated canvas
5. Save to brain

---

## HTML Template

The template is at: `templates/lean-canvas.html`

It has:
- Classic Ash Maurya 5-column layout (UVP spans 2 rows)
- Fill order numbers (●1 through ●10) on each box
- Sequence reference bar at the bottom
- Print-ready, self-contained HTML

## Notes
- The Lean Canvas is designed for rapid hypothesis testing
- Focus on addressing the riskiest assumptions first
- Update the canvas as you learn and validate
- Each section should be specific and measurable where possible
- Lean Canvas (Ash Maurya) replaces Partners/Activities/Resources with Problem/Solution/Unfair Advantage
