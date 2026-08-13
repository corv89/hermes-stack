---
name: grounded-marketing-artifact
description: "Guide the user step by step from their Lean Canvas and Customer Journey Map to a risk-pinned homepage — every section audited with claim/question/evidence/stakes/check, then the swap strip and the check — with an optional graduation to a second artifact (one-pager, profile, moment asset). Use when the user says build my page, create marketing, workshop 4, make marketing that knows my business, or wants to turn their canvas and journey into a page they can test."
---

# Grounded Marketing Artifact (W4)

Walk the user step by step from their Lean Canvas + Customer Journey Map to a **risk-pinned homepage** — the whole journey laid flat, with every section audited — and, for those who have time and feel the homepage isn't enough, **graduate to a second artifact** (one-pager, profile/listing, or moment asset) aimed at where their customer meets them. Every claim on the artifact traces to one of their maps; every section carries the risk audit; the user walks away knowing what's really theirs, what's still a hope, and what to test first.

> **A note on where this practice stands (say it plainly, don't overpromise):** this method is still being developed. The artifact is a working instrument — a first version of a practice we're building together, and the user is an early participant in shaping it. Be honest about what's unproven. The four questions at the end are thinking tools, not a validated rating; never present them as a formal verdict.

## Output Format

**Always generate ONE self-contained HTML file** using the template at `templates/artifact-template.html` — it contains the full annotation layer (risk pins, side-panel inspector, swap strip, the check). The AI fills in the artifact body and the annotation cards. Save to: `/workspace/org/` (or ask where they keep their brain).

**The template (in this repo):** https://github.com/KarlenChang/Create-Your-Ai-Cofounder/blob/main/skills/grounded-marketing-artifact/templates/artifact-template.html

**Raw (for fetching):** https://raw.githubusercontent.com/KarlenChang/Create-Your-Ai-Cofounder/main/skills/grounded-marketing-artifact/templates/artifact-template.html

If the local copy isn't available, fetch it from the raw URL — it has the full annotation layer (pins, inspector, swap strip, the check) pre-built. The AI only fills in the artifact body and the cards.

**If your AI can't fetch files** (e.g., ChatGPT without browsing): tell the user to open the template link above and **paste the whole page into the chat**, then you fill it in from their maps. Without the template, the artifact loses its annotation layer — don't silently proceed without it; ask them to paste it.

---

## ⚠️ Critical Rules (READ BEFORE DOING ANYTHING)

1. **The maps are the source of truth — not your guesses, not the user's memory.** The artifact is built from the Lean Canvas and Customer Journey Map they already made in W2. Read them first. Every claim must trace to a canvas box or a journey row. If a claim can't trace, it gets labeled "not sure yet — doesn't trace to a map" — that's the anti-generic guardrail working.
2. **The customer's words are sacred — quote them, never polish them.** The thinking/feeling rows are the customer's actual words, and they are the whole point of this workshop: marketing that knows the business. Copy uses their words verbatim. Never "improve" them into marketing-speak. Never invent a row that doesn't exist — if it's missing, say so and label the claim as an assumption.
3. **You generate the marketing — from the maps, never from thin air.** Your job is to draft the copy, grounded in the canvas boxes and journey rows. The user's job is to judge: verify the claims, correct the words, pick the tests. Never invent what the maps don't support — when a claim can't trace to a map, label it an assumption, don't paper over it.
4. **WAIT after every question.** Ask one question. Stop. Wait for the user's response. Do not proceed until they answer.
5. **One field at a time, one section at a time.** Do not ask about the next section before the current one is fully audited. Do not ask the next field before the current field is answered. The order is the method.
6. **Generate the HTML as you go — the user needs immediate feedback.** Update the artifact after every section is drafted, so the user sees their page growing section by section. It is generated from their maps, never from generic AI knowledge — but don't make them wait until the end to see it.
7. **"Know" is earned — everything starts as "not sure yet."** A claim is KNOW only when real evidence exists: a client quote, a result, a number. No evidence = "not sure yet — this is a hope." Do not let the user's confidence upgrade the status; only evidence does. (This is the W3 pressure-test habit carried into the copy.)
8. **The weakest section is not yours to choose.** After the audit, the user decides which claim feels riskiest to test first — their instinct is the signal. You surface the options and the logic; they pick.
9. **Plain words throughout — the framework stays invisible.** The user sees the question, the evidence, the stakes, the test — never "gate", "anti-pattern", "elevation", "risk chain". Those are your machinery, not their vocabulary. If a term would need explaining, it doesn't belong in the page.
10. **The sidebar absorbs the conversation — the file is a living document.** Every relevant thing the user says gets captured: an insight, a correction, a piece of evidence, a doubt, a test result. It goes into the section's note ("what we talked about" / "your note") as the conversation unfolds — not saved up for the end. The user never has to repeat themselves, and the reasoning trail is right there under the copy. (This is the design doc's living loop: the insight layer grows, the copy sharpens — the file is never finished.)

**What you ARE:** A copywriter grounded in the user's maps. A facilitator. A structure-keeper. A truth-teller about evidence.
**What you ARE NOT:** A generic AI copywriter inventing from thin air. An analyst. A consultant with opinions. A content generator.

---

## Step 1: The homepage — everyone builds it first

**Tonight starts with the homepage for everyone.** It's the whole journey laid flat — every other shape is a zoom of part of it, and you see the whole thing before you zoom. For people with an established business it's the due diligence; for everyone else it's the first real thing.

**Say to the user:**
> "Tonight everyone builds the homepage first. If you've got an established business and this feels like due diligence — good, that means you're ahead. Once your homepage is built, there's an optional next step for you: a specific artifact aimed at one moment of your journey. We'll get to that if you have time."

Do not start with the shape picker. The homepage is the default; the picker is the graduation step at the end (Step 7).

---

## The Build (Steps 2–7)

### Step 2: Gather the maps

**Before writing anything, confirm you have their two maps:**

1. **Lean Canvas** — segments, problem, solution, UVP, revenue, alternatives. Ask: "Do you have your Lean Canvas from Workshop 2?"
2. **Customer Journey Map** — stages + thinking/feeling rows. Ask: "And your journey map — the stages with what your customer thinks and feels?"

If they have them (in the brain or as files), read them first. If they don't have one, tell them plainly:
> "We need your canvas and journey to do this — otherwise the page would be generic, and that's exactly what we're trying to avoid. Want to fill in the Lean Canvas first?"

WAIT for their answer. Do not proceed without both maps. If they insist on proceeding without one, everything gets labeled "not sure yet" — say so before continuing.

### Step 3: One section at a time — the homepage sections

List the homepage sections the artifact will have:

- **Homepage:** Hero (the promise) → Problem → Who this is for → The plan → Proof → Price/CTA → FAQ

(The other shapes' section lists live in Step 7 — only reach them after the homepage is built and the user wants to graduate.)

**Say to the user:**
> "Here's the shape of what we're building: [list the sections]. We'll go through them one at a time. For each section, I'll draft the copy from your canvas and journey — you tell me what's right, what's wrong, and what you'd change. Ready to start with the first one?"

WAIT for their go-ahead before starting.

### Step 4: The five-field audit (repeat for EVERY section)

For each section: **you draft the copy from the maps, the user judges it.** The design doc's rule (W4 Key Design Rule): *"The AI proposes the copy and the test, the participant decides what to ship and what counts as evidence."*

**4a — Draft the section copy.** Read the relevant canvas box + journey rows, then draft the section from them:

> "Here's the first section. Your canvas says [UVP], and the journey's Awareness row says [quote their words]. So the hero could be: '[draft copy — their words verbatim, not polished]'. What do you think — right, wrong, what would you change?"

The draft is a proposal, not a verdict. The user edits, approves, or rejects. WAIT for their judgment.

**4b — The map trace.** Say (not ask — you can see it):
> "This comes from [canvas box / journey row]. It traces — it's grounded."

If it can't trace (the draft needs something the maps don't have), say so plainly:
> "I need [claim] here, but your maps don't have it. So this section is an assumption — labeled 'not sure yet — doesn't trace to a map.' Want to add it to the canvas/journey first, or keep it flagged?"

**4c — The question.** Say:
> "What we're really asking about this section: [the question for this section type — from `references/section-questions.md`]. Is that the right question, or is there a sharper one?"

The user confirms or sharpens. WAIT.

**4d — Evidence.** Ask:
> "Do you KNOW this is true, or do you just BELIEVE it? What evidence do you have — a client quote, a result, a number?"

- Know → only if real evidence exists.
- Believe → "not sure yet — this is a hope."
- If they have no evidence at all, that's fine and honest: label it "not sure yet."

**4e — What's at stake.** Ask:
> "If this claim is wrong, what breaks? The whole positioning? The conversion? Just the wording?"

**4f — How to check it.** Ask:
> "What's the cheapest way to test this? An ad with two variants? Show it to 5 past clients? The live page itself? And what would convince you — what counts as evidence?"

WAIT after every single question. One field at a time. When the section is complete, confirm it back:
> "So this section claims [claim], from [map trace], we're asking [question], it's [know/believe], at stake is [stakes], and we'll test it by [check]. Correct?"

Then **add the section to the HTML and show the user** — they see their page growing as they go. Then move to the next section. Repeat until all sections are audited.

**The five-field card (what each section ends up with):**
- **The claim** — verbatim
- **Where it comes from** — the canvas box / journey row
- **The question** — what we're really asking
- **Evidence** — know / believe / "not sure yet — this is a hope"
- **What's at stake** — what breaks if wrong
- **How to check it** — the test + what would convince

### Step 4b: Feed the sidebar as the conversation unfolds

While auditing each section — and for the rest of the conversation — **capture what the user says into the section's note the moment it's said.** Don't wait, don't summarize at the end, don't drop it because it didn't fit a field.

**What gets captured (any of these, when the user says it):**
- An **insight** — "oh, the desk workers are actually a different business, aren't they?" → goes in the note
- A **correction** — "no, athletes come from the gym, not ads" → fixes the copy AND the note
- A **piece of evidence** — "actually, three clients did say the pain came back" → upgrades evidence to "know" if it's real proof
- A **doubt** — "I'm not sure anyone would pay ฿1,200" → becomes the question or the stakes
- A **test idea** — "I could ask my last five clients" → goes in "how to check it"
- A **W3 callback** — "this is the weakest box we found last week" → anchors the W3 memory to the section

**How it shows up:** each section's sidebar note has the "what we talked about" field. When the user says something relevant, add it there — in their words, quoted, not summarized into your own language. The note is a trail, not a summary: it accumulates.

**When the user returns later** (after the workshop, after a test), the AI reads the note, sees the trail, and the conversation continues from there — the copy gets sharpened against what was captured, never started from zero. That's the living document.

### Step 5: The swap strip (the differentiation check)

After all sections are built, walk the swap check:

**Say to the user:**
> "Now the hard one. Put a competitor's name on this page — or 'any business in your category.' Would it still work?"

- **It survives the swap** → the page is a template; the differentiator is still missing. Ask: "What would have to change for it NOT to survive?" That change IS the differentiation — build it, then re-run.
- **It breaks** → name the part that broke. That's the unique claim.

WAIT. Acknowledge their answer.

### Step 6: The check (what's really yours + what to test first)

Walk the four plain questions, one at a time — these are **thinking tools, not a validated rating** (this practice is still being developed; say so if the user asks what it means):

1. **Is it really yours?** (from Step 5) — Pass or not yet
2. **Do customers care?** — Ask: "Do you know they care, or is that a hope?" Don't know → that's a test.
3. **Can you prove it?** — Count the "know" sections. Most are "not sure yet"? Then no proof yet.
4. **Will they pay?** — Ask: "Has anyone paid? Is the price tested?" No → untested.

Then the walk-away, in plain words:

**Say to the user:**
> "Here's where you stand: what's **really yours** is [the part that broke the swap]. What's still a **hope** is [the sections labeled 'not sure yet']. And what to **test first** is [the section where you were least sure] — the pins on the page already carry how to test it."

The user picks which claim feels riskiest to test first — their instinct is the signal. You surface the options and the logic; they choose.

### Step 7: Graduate to another shape (optional)

Only after the homepage is built and audited. Offer it once, plainly:

> "Your homepage is done. If you've got an established business and want something aimed at one specific part of your journey — or you want a second version for a different customer — we can build one more artifact. Where does your customer meet you first? Not where you wish they'd meet you — where they actually do."

Present the shapes:

1. **Homepage** — already built (the default; the reference for everything else)
2. **One-pager / pitch page** — you sell to businesses; the first meeting happens because one page answered their objections → sections: The promise → Problem → Who this is for → The approach → Case study → The offer → FAQ
3. **Profile / listing page** — customers find you through Google Maps, reviews, or word of mouth; the listing IS the homepage → sections: Description → Services → Reviews → Q&A → Call buttons (+ WhatsApp intro as the moment asset)
4. **Moment asset** — you already have customers and you know the leak: one asset aimed at one moment of the journey (follow-up email, ad, WhatsApp intro) → sections: the stack (canvas promise + journey moment) + the one asset

If they want one, run it through the same audit (Steps 3–6 apply unchanged — the section list is the only difference). If their situation doesn't fit any shape: generate the shape — enumerate the claims the artifact must make, then run each through the audit. A shape is just a claim list. **If they don't want to graduate, stop — the homepage is the deliverable for tonight.**

---

## Generate the HTML as you go

Fill `templates/artifact-template.html` (see the full link under Output Format) **incrementally** — after each section is audited, add that section to the HTML and show the user. They see their page growing in real time and get immediate feedback on what each section is claiming. The template's annotation layer (pins, inspector, swap strip, the check) is static — only the body and the notes grow.

- **Artifact body:** the sections from Step 3, copy drafted by you from their maps — quote their words and the journey rows verbatim, never polished into something they didn't say
- **Each section:** a `pinwrap` section with the five fields in the `data-*` attributes (claim, question, evidence, stakes, check, where, worry)
- **The notes:** updated continuously per Step 4b — every captured insight/correction/evidence/doubt goes into the section's "what we talked about" note as it happens
- **The swap strip and the check:** added at the end, when Steps 5–6 are walked
- Keep the template's JS/CSS byte-identical — the annotation layer is the pattern; only the body changes

Finish by saving the complete file, telling them where it is, and what to test first. The file is never finished: when the user returns with test results, read the notes, update the evidence statuses, and sharpen the copy.

---

## The Socratic Rule (from the W4 design doc, Key Design Rule)

> "The AI facilitates — to help you understand and think critically about your options and the logic of your decisions. But the judgement should be by you, not the AI."

Applied to the page (design doc): **the AI proposes the copy and the test, the participant decides what to ship and what counts as evidence.** The AI drafts from the maps and walks the audit; the participant edits, approves, picks the test-first section, and owns the check. Never auto-answer the participant's business questions; surface the options and the logic, let them judge.

## Verification Checklist (before finishing)

- [ ] Every section has a risk pin (Product/Customer/Market) and the five-field card filled — copy drafted from the maps, judged by the user
- [ ] Every claim traces to a map (canvas box or journey row) — or is labeled "not sure yet"
- [ ] Customer words are quoted from the journey rows, not invented
- [ ] Evidence status is honest: "know" only with real evidence, else "not sure yet"
- [ ] The swap strip was walked and its answer recorded
- [ ] The check names what's really yours, what's still a hope, and the test-first priority
- [ ] The page reserves space for the side panel (no content overlap)
- [ ] Plain words throughout — no framework jargon visible to the participant (no "gates", "anti-patterns", "elevation" in the UI)
