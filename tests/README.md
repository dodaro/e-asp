# Debugger Unit Tests

These tests are data-driven: add one JSON file for each ASP program in
`tests/fixtures/debugger_cases/`, then run:

```bash
python -m unittest discover -s tests
```

The templates in `tests/fixtures/templates/` are not executed. Copy one of them
into `tests/fixtures/debugger_cases/`, rename it, and fill in the expected
values.

## Case Fields

- `program`: ASP source code to test.
- `debug_rules`: enable rule debugging. Defaults to `true`.
- `debug_answer_set`: enable answer-set literal debugging. Defaults to `true`.
- `answer_set_count`: number of answer sets to request. Defaults to `1`.
- `answer_set_index`: answer set used for atom inspection. Defaults to `0`.
- `expected_answer_sets`: list of expected answer sets. Each answer set can be a
  string such as `"a, b"` or a list such as `["a", "b"]`.
- `expected_atoms`: atoms expected in the inspection page. Values can be strings
  or objects with `atom`, `text`, and `value`.
- `explanations`: literal explanations to run with expected debugger responses.
- `aggregate_expansions`: aggregate expansion checks produced by
  `Justifier.expand_aggregate(...)`. Element labels expose their grounded
  source bindings, for example `<D=2, PH=1>`.
- `satisfiable: false` and `expected_unsat_responses`: use these for
  unsatisfiable-program debugging.

For unsatisfiable programs with aggregates, `expected_unsat_responses` should
usually contain the grounded aggregate rules returned by the debugger, for
example `:- v(1), #count{C : color(1,C)} != 1.`.

Response types can be written as `rule`, `fact`, `literal`, `aggregate`, or as
their numeric values `0`, `1`, `2`, `3`.

Atom values can be written as `true`, `false`, `undefined`, or `not_set`.

By default, answer sets and responses are compared without caring about order.
Set `answer_sets_ordered`, `responses_ordered`, or `unsat_responses_ordered` to
`true` when order is part of what you want to verify.
