# E-ASP — Explainable Answer Set Programming

E-ASP is an interactive debugger and explanation tool for [Answer Set
Programming](https://en.wikipedia.org/wiki/Answer_set_programming) (ASP)
programs. Given an ASP program, it answers the questions a programmer
actually asks while developing:

- **Why is this literal true (or false) in the answer set?** E-ASP computes a
  minimal set of rules, facts and literals that justify the truth value, and
  lets you follow the explanation *chain*: every literal in an explanation
  can be explained in turn, one step at a time.
- **Why is my program unsatisfiable?** E-ASP isolates a minimal core of
  conflicting rules and completes it with the rules defining the atoms the
  conflict depends on.
- **Why is there no answer set with a better cost?** For optimization
  problems (weak constraints, `#minimize`/`#maximize`), E-ASP explains the
  cost paid at each optimization level.
- **What is this aggregate doing?** `#count`/`#sum` aggregates appearing in
  an explanation can be expanded interactively, showing every ground
  instance with the truth value of each element.

The explanations are computed by *instrumenting* the program: every rule
gets a guard atom that allows the solver to switch it off, the inspected
answer set is frozen through support atoms, and clingo is asked for a
minimal unsatisfiable core over those guards. On top of the symbolic
explanation, an optional LLM integration (via OpenRouter) turns the
generated output into a discursive, natural-language explanation.

The solving backend is the [`clingo` Python
package](https://potassco.org/clingo/) — no external solver binary is
needed.

## Requirements

- Python ≥ 3.10
- Packages in `requirements.txt`: `streamlit`, `clingo`, `openai`
  (the OpenAI client is only used to talk to OpenRouter)

## Running the app

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/streamlit run streamlit_app.py
```

Streamlit opens the app in the browser. Typical workflow:

1. **Editor** — write or upload a program (`.lp`, `.asp`, `.txt`), choose
   the debugging modes (*Rules*: rules can be blamed; *Literals*:
   answer-set literals can be blamed) and how many answer sets to compute,
   then press **Explain**.
2. **Answer Sets** — pick one of the computed answer sets and **Inspect**
   it. For unsatisfiable programs you are taken directly to the
   explanation of the incoherence.
3. **Inspection** — select any literal (true or false) and ask for its
   explanation; for optimization problems the *Cost Inspection* section
   explains why no cheaper model exists at a given level.
4. **Explanation** — the result is split into rules, input facts and
   literals. Aggregate rules can be expanded; literals can be explained
   further (*Next Literal Explanation*), building the explanation chain.

### Rule annotations

Two annotations can be appended after the final dot of a rule (see the
*Rule annotations* panel in the editor):

```prolog
busy(D) :- assigned(P,D). @correct   % trusted: never blamed, blame flows through it
:- busy(D), holiday(D).   @ignore    % excluded entirely, like commenting it out
```

### Supported subset

Normal, choice and disjunctive rules, constraints, intervals (`a(1..4)`),
string constants, `#count`/`#sum` aggregates, weak constraints and
`#minimize`/`#maximize`, `#const`, comments, and rules spanning several
lines. Known limitations (`#min`/`#max`, pooling, conditional literals in
bodies, ...) are listed in the editor's *Supported ASP subset and
limitations* panel.

## LLM explanations (OpenRouter)

The *LLM explanation* panel can send the generated context (program,
answer sets, explanation chain, aggregate expansions) to a model served by
[OpenRouter](https://openrouter.ai) and display a discursive explanation
in the language of your choice. Without a key you can still **preview the
prompt** that would be sent.

The API key is looked up in this order:

1. the *API Key* field in the panel (per-session);
2. the `OPENROUTER_API_KEY` environment variable;
3. Streamlit secrets — create `.streamlit/secrets.toml`:

```toml
openrouter_api_key = "sk-or-..."
```

Do not commit `secrets.toml`. The model list and the temperature are
configurable in the panel.

## Configuration files

- `.streamlit/config.toml` — Streamlit visual theme.

## Testing

Tests are data-driven: each JSON file in `tests/fixtures/debugger_cases/`
describes a program plus the expected answer sets, inspectable atoms,
explanations, aggregate expansions, or unsat cores. Run them with:

```bash
.venv/bin/python -m unittest discover -s tests
```

To add a case, copy one of the templates from `tests/fixtures/templates/`
into `tests/fixtures/debugger_cases/`, rename it and fill in the expected
values (see `tests/README.md` for the field reference). Cases with
`"skip": true` are ignored. Note that tests require the `clingo` package,
since they run the real solver.

## Credits

E-ASP was originally developed in Java/JavaFX
([original repository](https://github.com/MarcoMochi/E-ASP)). This
project is a full Python port of that tool; portions are derived from
the original code base, Copyright (c) 2024 Marco Mochi, released under
the MIT License (see `LICENSE`).

## Project structure

```
streamlit_app.py        Streamlit entry point
easp/
  asp_parser.py         text-level ASP helpers (statement normalization,
                        body/cost extraction, variable and aggregate parsing)
  clingo_runner.py      clingo API wrapper: solving, incoherence checks,
                        grounding, assignment-order tracking (propagator)
  debugger.py           core engine: program instrumentation, minimal
                        unsatisfiable cores, aggregate expansion, costs
  services.py           Justifier facade + service classes used by the UI
  models.py             QueryAtom, Response, UnsatisfiableCore, CostLevel
  llm_explainer.py      OpenRouter client and prompt builder
  config.py             legacy settings (config.json)
  ui/                   Streamlit state, actions and page components
tests/                  data-driven unit tests (JSON fixtures + runner)
```
