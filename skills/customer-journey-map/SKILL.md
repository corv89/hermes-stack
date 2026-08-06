---
name: customer-journey-map
description: "Create an end-to-end customer journey map with stages, touchpoints, emotions, pain points, and opportunities. Use when mapping the customer experience, identifying friction points, improving onboarding, or visualizing the user journey."
source: "Forked from phuryn/pm-skills"
---

# Customer Journey Map

## Output Format

**Always generate two files:**

1. **HTML file** — `customer-journey-map.html` — visual grid, self-contained, print-ready. Use the template at `templates/customer-journey-map.html`.
2. **Markdown file** — `customer-journey-map.md` — structured text version. Use the template at `templates/customer-journey-map-template.md`.

The HTML is what they look at. The markdown is what the agent reads and searches. Both must contain the same data.

Save both to: `/workspace/org/`

Map the end-to-end customer experience from awareness through advocacy, identifying emotions, pain points, and improvement opportunities at each stage.

## Context

You are creating a customer journey map for **$ARGUMENTS**.

If the user provides files (interview transcripts, survey data, analytics, support tickets, or existing journey maps), read them first. Use web search to understand the product if a URL is provided.

## Instructions

1. **Define the persona**: Who is traveling this journey? Use a specific persona with JTBD, not a generic user.

---

## ⚠️ Critical Rules (READ BEFORE DOING ANYTHING)

1. **You do NOT have the answers.** Only the user knows their customers. Never generate, guess, or fill in the journey map on their behalf.
2. **WAIT after every question.** Ask one question. Stop. Wait for the user's response. Do not proceed until they answer.
3. **Never skip ahead.** Do not ask about Stage 2 before the user has answered Stage 1. One stage at a time, one field at a time.
4. **Do NOT generate the HTML or markdown until every field has been answered by the user.** The map is built from their answers, not from your assumptions.
5. **If the user doesn't know, write "TBD" and move on.** Don't fill in a guess. Don't rephrase your guess as a question.
6. **If the user gives a vague answer, ask a follow-up to get specifics.** Don't take "they feel fine" as final — ask "fine like satisfied, or fine like indifferent?"

**What you ARE:** An interviewer. A facilitator. A structure-keeper.
**What you ARE NOT:** An analyst. A consultant with opinions. A content generator.

---

2. **Map the journey stages** (adapt to the product):

 | Stage | Description |
 |---|---|
 | **Awareness** | How do they first learn about the product? |
 | **Consideration** | What do they evaluate? What alternatives do they compare? |
 | **Acquisition** | How do they sign up or purchase? |
 | **Onboarding** | First experience with the product — time to value |
 | **Engagement** | Regular usage — building habits |
 | **Retention** | What keeps them coming back? What might cause churn? |
 | **Advocacy** | When and why do they recommend the product to others? |

---

### Step A: Light Map (Touchpoints + Actions Only)

Start here. This is the foundation — get the skeleton down before adding depth.

**Say to the user:**
> "First, let's just map where your customer shows up and what they do. Don't worry about feelings or problems yet — we'll get to those. Just tell me: for each stage, where do they encounter you, and what action do they take?"

**For each of the 7 stages, ask only two questions:**

1. **Touchpoint** — Where does the customer interact with you? (ad, website, store, referral, app, email, conversation, etc.)
2. **User action** — What do they actually do? (click, call, visit, sign up, buy, share, etc.)

**Light Map table:**

 | Stage | Touchpoint | User Action |
 |---|---|---|
 | **Awareness** | | |
 | **Consideration** | | |
 | **Acquisition** | | |
 | **Onboarding** | | |
 | **Engagement** | | |
 | **Retention** | | |
 | **Advocacy** | | |

**Rules for the agent during Step A:**
- Keep it fast. One touchpoint and one action per stage is enough.
- Don't let the user overthink — if they're stuck, suggest a common touchpoint and ask "does that sound right?"
- If a stage doesn't apply (e.g., no advocacy yet), write "TBD" and move on.
- Save the light map. This is the skeleton everything else builds on.

---

### Step B: Deep Map (Full Detail)

Now layer on the rest. The light map is the skeleton — this is the meat.

**Say to the user:**
> "Good — now we know where they show up and what they do. Let's figure out what they're thinking and feeling at each stage, and where things break down."

**For each stage, add these fields:**

 - **Touchpoints**: Where the user interacts with the product, brand, or team
 - **User actions**: What they do at this stage
 - **Thoughts & questions**: What's on their mind
 - **Emotions**: How they feel (excited, confused, frustrated, delighted)
 - **Pain points**: Friction, confusion, drop-off risks
 - **Opportunities**: How to improve the experience at this point

**Full journey map table:**

 | Stage | Touchpoint | User Action | Emotion | Pain Point | Opportunity |
 |---|---|---|---|---|---|

---

### Step C: Identify Critical Moments

After the full map is built. **The critical moments come from the user's map — not from you.** You introduce each concept, then ask them to find it in their own journey. One moment type at a time. WAIT after each answer.

**Say to the user:**
> "We've got the full map. Now let's find the moments that actually decide whether this works. There are three kinds. I'll explain each one — you tell me where it shows up in your map."

**C1 — The Aha moment** (when the customer first experiences the core value)

Explain it: "The aha moment is the first time your customer feels the value you promised — the moment they 'get it.' For the massage therapist, it was the first session where the pain actually moved."

Then **ask**: "Looking at your map — where is that moment for your customer? Which stage, and what exactly happens for them?"

WAIT for their answer. Acknowledge it before moving on ("So the aha is the first session where the pain moved — got it.").

**C2 — Moments of truth** (decision points where they commit or abandon)

Explain it: "Moments of truth are the forks in the road — where the customer decides to go deeper or walk away. Booking the second session. Renewing. Choosing you over the cheaper option."

Then **ask**: "Where are the forks in your map? Where does your customer decide to continue — or leave?"

WAIT for their answer. Acknowledge it.

**C3 — Churn triggers** (where customers most commonly drop off)

Explain it: "Churn triggers are where you actually lose people. For the therapist it was the 60% who never booked Session 3."

Then **ask**: "Where do you lose people in your map? Which stage — and what's happening at that moment?"

WAIT for their answer. Acknowledge it.

**Rules for the agent during Step C:**
- You introduce the concept. **They find the moment.** The moment comes from their map, never from your imagination.
- One moment type at a time. Never list all three and ask "which of these apply?"
- If they're stuck, point back at rows they already gave you in Step A or B — their own words: "You said they book online but never show up — is that the fork?" Never supply a moment they didn't describe.
- If they genuinely don't know, write "TBD" and move on.

### Step D: Recommend Prioritized Improvements

 - Which pain points have the highest impact on conversion or retention?
 - What quick wins can improve the experience immediately?
 - What requires deeper investment but has the biggest payoff?

Think step by step. Save as a markdown document.

---

## Final Output

When the journey map is complete, generate **both files**:

1. **`customer-journey-map.html`** — Fill in the HTML template with all data. Each cell in the grid gets the corresponding content. The visual grid is what the user reviews.
2. **`customer-journey-map.md`** — Fill in the markdown template with the same data. This is the searchable, agent-readable version.

Tell the user:
> "I've saved your journey map in two formats — an HTML file you can open in your browser to see the full grid, and a markdown file your AI co-founder can search and reference."
