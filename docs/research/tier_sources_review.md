# Tier Taxonomy — Source Review

This document reviews each tier against the actual papers, flags where
citations are solid, where they are inaccurate, and where we are making
our own contribution. Written after reading the papers directly.

---

## Tier A — ImportantMessage

**Claimed source:** AgentDojo (Debenedetti et al., arXiv:2406.13352, NeurIPS 2024)

**Verified:** YES — with one correction.

The AgentDojo paper tests **four** attack templates (not five as some summaries
suggest):
1. Ignore previous instructions
2. TODO format
3. InjecAgent template
4. ImportantMessage

ImportantMessage combines urgency framing with an explicit override command
and achieves **57.7% ASR against GPT-4o** (not 53.1% — that figure appears
in earlier drafts or summaries of the paper). This is the strongest
single-template result in the benchmark.

All 56 original payloads in this benchmark use this pattern — urgency
framing, fake authority claims, system override language in metadata fields.
The citation is solid.

---

## Tier B — Exact Tool API Syntax

**Claimed source:** AgentDojo "ToolKnowledge" attack

**Verified: INCORRECT ATTRIBUTION.**

The AgentDojo paper does discuss attacker knowledge levels, but "ToolKnowledge"
in that paper refers to knowing the **user's name and the model name** — not the
tool API schemas. Table 2 in the paper shows that knowing user/model names
yields slightly stronger attacks (~1.9% improvement). This is not the same
as knowing the MCP function signatures.

**What Tier B actually is:** Our own formulation for the MCP context. The
concept is logical — an attacker who has inspected the Stripe MCP server knows
the exact function names and parameter schemas. Instead of saying "redirect the
refund", they write `refunds.create(payment_intent='pi_test001', amount=2500)`.
This removes interpretive ambiguity.

**Simon Willison's blog** (simonwillison.net/2023/Apr/25/dual-llm-pattern and
/2023/May/2/prompt-injection-explained) discusses tool-enabled attacks and
confused deputy attacks but does not describe exact API syntax injection as a
distinct category.

**Greshake et al.** (arXiv:2302.12173) introduces indirect injection and
discusses attackers with knowledge of the system broadly, but does not define
a specific "tool knowledge" attack tier.

**Conclusion:** Tier B should be described as our own extension of the
attacker knowledge concept to the MCP tool surface, grounded in the general
principle that attackers with API access will use exact syntax. It is not
directly citable to a single paper. In the report, this should be framed
as a novel contribution: "We extend the attacker knowledge dimension from
AgentDojo to the MCP tool surface, constructing payloads that embed exact
function signatures rather than natural-language directives."

---

## Tier C — Value-Level Structural

**Claimed source:** CaMeL paper (Debenedetti et al., arXiv:2503.18813, 2025)

**Verified:** YES — confirmed by abstract and design.

The CaMeL abstract confirms the system is motivated by attacks where
"untrusted data retrieved by the LLM can never impact the program flow."
The symbolic variable mechanism (returning `$VAR1` instead of actual values)
is explicitly designed so that the privileged LLM never reads attacker-
controlled content directly. This only makes sense as a defence if value-level
attacks — where malicious content lives in typed fields, not instruction text —
are a real threat class.

The HTML version of the paper 404'd so we cannot quote specific sections, but
the architecture is designed around this problem. The attribution is correct
in concept.

**One caveat:** The CaMeL paper does not name this attack class "value-level
structural." That label is ours. The paper motivates symbolic variables as a
defence against prompt injection broadly, with the specific benefit of
preventing data-level manipulation as a consequence of the architecture.

---

## Tier D — Thought Injection

**Claimed source:** Design Patterns paper (Beurer-Kellner et al.,
arXiv:2506.08837, 2025)

**Verified: INCORRECT ATTRIBUTION.**

The Design Patterns paper covers six architectural patterns (Action-Selector,
Plan-Then-Execute, Map-Reduce, Dual LLM, Code-Then-Execute,
Context-Minimization). It discusses control flow manipulation as a general
vulnerability but **does not describe thought injection or chain-of-thought
trace fabrication as a named attack class**.

**What Tier D actually is:** A logical attack derived from the structure of
ReAct. The ReAct paper (Yao et al., arXiv:2210.03629) defines the
Thought/Action/Observation trace format. If that format is well-known, an
attacker can fabricate it. This is not attributed to a single paper in the
current literature — it is a logical consequence of ReAct's architecture.

The Design Patterns paper does discuss "control flow manipulation where tool
outputs influence subsequent agent actions" which is the closest published
description, but it does not name or specify thought injection as a distinct
technique.

**Conclusion:** Tier D should be framed in the report as a novel attack
class derived from the ReAct architecture, inspired by the Design Patterns
paper's discussion of control flow manipulation. It is architecture-specific
to ReAct and is one of the contributions of this extended benchmark. Do not
cite the Design Patterns paper as the source — cite it as motivation for
why this class exists, but frame the specific attack as our own.

---

## Tier E — Role-Tag Injection

**Claimed source:** ChatInject (Chang et al., arXiv:2509.22830, ICLR 2026)

**Verified:** YES — fully confirmed.

The paper explicitly describes forging role-based message structures using
special tokens (`<|user|>`, `<|assistant|>`, system tags). ASR improvements
confirmed:
- AgentDojo: 5.18% → 32.05%
- InjecAgent: 15.13% → 45.90%
- Multi-turn variant: 52.33% average

The mechanism is exactly as described: attackers embed role-tag markers in
tool output, causing the model to read fabricated dialogue as real
conversational history. The citation is solid.

---

## Spotlighting

**Claimed source:** Hines et al., arXiv:2403.14720, 2024 (Microsoft Research)

**Verified:** YES — fully confirmed.

Three variants confirmed:
1. **Delimiting** — special tokens wrap the input (~50% ASR reduction on
   GPT-3.5-Turbo)
2. **Datamarking** — "^" characters interleaved throughout text (~0-3% ASR)
3. **Encoding** — base64 transformation (~0% ASR, but performance cost on
   weaker models)

The paper also recommends dynamic marking tokens (regenerated each session)
to prevent an attacker who has seen the system prompt from knowing the
current delimiter. We are using the delimiting variant with static
`<external_content>` tags — simpler but potentially less robust if an
attacker can read the system prompt.

**Note:** This paper is not in `bib_fork.bib` yet. Add it before the run
if spotlighting results are reported.

---

## Simon Willison's actual contribution

Simon Willison's relevant work for this benchmark:

1. **Coined "prompt injection" as a term** (2022) in the context of LLM
   applications — credited as the origin of the term in the community.

2. **Dual LLM pattern** (simonwillison.net/2023/Apr/25/dual-llm-pattern/) —
   this IS cited correctly in `bib_fork.bib` as `willison2023dual`. His
   contribution is architectural (the defence), not an attack taxonomy.

3. **Discussed confused deputy attacks** — attackers using tool access to
   take actions the user didn't intend. This is the general class our
   benchmark tests but is not a tier-level distinction.

Willison does not propose a tier taxonomy, does not describe exact tool API
injection, and does not discuss value-level attacks. His citation belongs in
the architecture section (Dual LLM defence), not the attack taxonomy.

---

## Summary of what needs fixing in the report

| Claim | Status | Action needed |
|---|---|---|
| AgentDojo has 5-tier taxonomy | WRONG — 4 templates | Correct to 4 templates |
| ImportantMessage 53.1% ASR | WRONG — 57.7% | Correct the figure |
| Tier B from AgentDojo ToolKnowledge | WRONG | Frame as our own MCP-context extension |
| Tier D from Design Patterns paper | WRONG | Frame as novel, cite Design Patterns as motivation only |
| Tier C from CaMeL | CORRECT in concept | Keep, note label is ours |
| Tier E from ChatInject | CORRECT | Keep |
| Spotlighting from Hines et al. | CORRECT | Add to bib_fork.bib |
