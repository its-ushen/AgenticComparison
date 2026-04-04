# Architecture Rationale

This document explains why each of the four architectures was built, what
problem it solves, and what it still leaves unresolved. The architectures
form a progression — each one addresses a specific limitation of the previous.

---

## ReAct

ReAct is the baseline because it is the most commonly deployed pattern in
production agent systems. The reasoning loop is simple: the model generates a
thought, calls a tool, reads the result, and feeds that result directly back
into the next thought. Planning and execution happen in the same context window
on every turn.

The problem this creates for security is that the reasoning loop has no
boundary between trusted and untrusted content. A tool response containing
injected instructions enters the model's context in exactly the same position
as any other input. The model cannot structurally distinguish between its own
prior reasoning and attacker-controlled metadata in a tool result. Whatever is
in the tool result influences the next thought.

ReAct is included because excluding it would make the benchmark unrepresentative
— most real payment agents are built this way. The results establish the
baseline attack surface before any architectural defence is applied.

---

## Plan-Then-Execute

PTE was added to address ReAct's core vulnerability: the tight coupling between
reasoning and tool responses. The insight is that if the agent commits to a
complete execution plan before making any tool calls, injected content in tool
results cannot change what actions are taken — the plan is already fixed.

The planner LLM receives only the user's request and the list of available
tools. It has never seen any external data. It produces a JSON plan specifying
which tools to call, in what order, and with what parameters. The Python
executor then runs this plan mechanically: it resolves `$variable` references
from prior step outputs and calls tools in sequence. No LLM is involved in
execution — it is entirely deterministic. An injected instruction in a tool
response cannot redirect a Python for loop.

The limitation is the rigidity of planning upfront. For the 56 payloads in
this benchmark the operations are simple enough that a planner can enumerate
all steps in advance. In a more complex environment — where the agent needs to
handle unexpected API responses, conditional branching, or large unstructured
data — locking a plan before execution is restrictive. The planner must
anticipate every possible outcome, which is not always feasible.

The synthesizer LLM, which generates the final response from the execution log,
does see raw tool output. This means injected content can still cause response
manipulation — the agent may report incorrect information to the user even when
its tool calls were correct. This is a known residual vulnerability of the
PTE architecture.

---

## Dual LLM

Dual LLM was added to recover the flexibility that PTE sacrifices. Rather than
locking a plan, the orchestrator LLM behaves like a ReAct agent — it calls
tools, reads results, and decides what to do next adaptively. The key addition
is a quarantine LLM that sits between tool results and the orchestrator.

Every tool response passes through the quarantine before the orchestrator sees
it. The quarantine strips the entire metadata object and any free-text fields
likely to contain injected content, returning only structured typed fields:
IDs, amounts, statuses, booleans, and URLs. The orchestrator is architecturally
prevented from reading injected instruction text.

This preserves flexibility — the orchestrator can handle complex multi-step
tasks, adapt to unexpected results, and make decisions based on what the API
returns — while blocking the instruction-based attacks that compromise ReAct.

The residual vulnerability is that the quarantine must pass some string fields
through to be useful. Fields like `description`, `name`, and `email` are
user-writable through normal Stripe API usage and are passed through the
quarantine as legitimate data. An attacker who controls these fields can place
value-implying content in them that reaches the orchestrator unchanged. This
is the Tier C attack class: no instruction text, just data in a trusted field
that causes the orchestrator to reason incorrectly.

---

## Schema Dual LLM

Schema Dual LLM addresses the residual Tier C vulnerability in Dual LLM while
preserving the orchestrator's flexibility.

The key observation is that the Dual LLM quarantine LLM is doing something
Python can do better for structured API responses. The quarantine LLM was
designed for cases where tool results are unstructured — emails, documents,
web pages — and an LLM is needed to parse natural language and extract
structured fields. For Stripe's MCP tools, every tool returns documented,
fixed JSON. We know exactly what `paymentIntents.read` returns because we
wrote the tool. A quarantine LLM is unnecessary overhead for this case and
introduces its own attack surface: instruction text in tool results could
influence the quarantine LLM's extraction output.

Schema Dual LLM replaces the quarantine LLM with a Python allowlist. For each
Stripe tool, a hardcoded list of safe typed fields specifies exactly what the
orchestrator is allowed to see. The extraction is deterministic: `description`,
`name`, `email`, and `metadata` are never in any allowlist, so they never reach
the orchestrator regardless of their content. There is no LLM in the extraction
path that could be manipulated.

This is inspired by the CaMeL system (Debenedetti et al., 2025), which
addresses the same problem using a Q-LLM with Pydantic schemas. CaMeL uses an
LLM because it is designed for general agents that handle unstructured data.
For a payment agent backed by a structured API with a known schema, the Python
version achieves the same security property without the LLM overhead and
without the additional attack surface.

The orchestrator remains fully flexible — it reads the extracted fields, decides
what tool to call next, and adapts based on results, exactly like the Dual LLM
orchestrator. The difference is that the data the orchestrator reads is strictly
typed: only IDs, amounts, statuses, booleans, and structured numeric values.
Nothing an attacker can write a sentence into.

---

## Summary

| Architecture | Protects plan? | Protects tool call args? | Blocks Tier C? | Flexible? |
|---|:---:|:---:|:---:|:---:|
| ReAct | No | No | No | Yes |
| PTE | Yes | Yes (Python) | Partial* | No |
| Dual LLM | N/A | Yes (quarantine) | No | Yes |
| Schema Dual LLM | N/A | Yes (Python allowlist) | **Yes** | Yes |

\* PTE blocks tool call manipulation (plan is locked) but the Synthesizer still
sees raw tool output and can be influenced to report incorrect values.

The progression is: each architecture adds one layer of structural protection.
Schema Dual LLM is the only architecture where attacker-controlled string
content structurally cannot reach the LLM that makes tool call decisions.
