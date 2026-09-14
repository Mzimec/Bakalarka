# AI Credits

Large language models were used as development assistants during this project.

The following models were used:

- ChatGPT 5.5
- ChatGPT 5.6 Sol
- Claude 5 Sonnet

Their use was primarily focused on code generation, code review, documentation,
testing, and language/style improvements.

---

## 1. Cards

LLMs generated a significant part of the code in the `cards` directory.

This mainly included:

- `CardDefinition` instances,
- `AbilityDefinition` instances,
- card-specific effects and operations,
- supporting definitions required by individual cards.

The generated code was integrated into the existing engine architecture and
rules systems.

---

## 2. Console

LLMs helped complete the existing command-builder skeleton.

Their contributions included:

- adding additional commands,
- improving command parsing,
- improving the interaction flow,
- making console usage more convenient and consistent.

---

## 3. Bin Scripts

LLMs generated or pre-generated several executable scripts in the `bin`
directory.

These scripts are used for tasks such as:

- running single matches,
- running tournaments,
- starting interactive games,
- configuring agents and decks.

---

## 4. LLM Policy

LLMs helped create `llm_policy.py` and integrate it with `modular_agent.py`.

This work included connecting LLM-based decision making with the existing modular
agent architecture.

---

## 5. Code Quality and Efficiency Revision

LLMs were used to review parts of the codebase for:

- code quality,
- unnecessary duplication,
- architecture consistency,
- possible bugs,
- performance issues,
- opportunities for simplification.

Suggested changes were reviewed and integrated into the project where
appropriate.

---

## 6. Comment Generation

Claude was used to pre-generate a large portion of the code documentation and
comments.

These comments were then reviewed and adjusted where necessary.

---

## 7. Test Generation

A large portion of the automated tests was generated or pre-generated with the
help of LLMs.

This included tests for:

- game rules,
- card behavior,
- action generation,
- mana solving,
- continuous effects,
- replacement effects,
- combat,
- integration between subsystems.

---

## 8. Documentation, Translation and Style

LLMs were used to help with:

- translation,
- English grammar and stylistic corrections,
- Markdown documentation,
- diagrams in Markdown files,
- restructuring technical descriptions for readability.

The technical content and final project structure were reviewed and adjusted
during development.
