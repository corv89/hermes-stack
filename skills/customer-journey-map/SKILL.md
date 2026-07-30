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

After the full map is built:

 - **Aha moment**: When the user first experiences core value
 - **Moments of truth**: Decision points where they commit or abandon
 - **Churn triggers**: Where users most commonly drop off

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
