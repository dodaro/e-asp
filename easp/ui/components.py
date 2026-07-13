from __future__ import annotations

from html import escape
from typing import Iterable

import streamlit as st

from easp.models import QueryAtom, Response
from easp.services import Justifier
from easp.ui import actions
from easp.ui.state import (
    PAGE_ANSWER_SETS,
    PAGE_COST_EXPLANATION,
    PAGE_EDITOR,
    PAGE_EXPLANATION,
    PAGE_INSPECTION,
    PAGE_UNSAT,
    current_page,
    go_home,
    navigate_back,
)


RULE_TYPE = 0
FACT_TYPE = 1
LITERAL_TYPE = 2
AGGREGATE_TYPE = 3


def render_current_page() -> None:
    pages = {
        PAGE_EDITOR: render_editor,
        PAGE_ANSWER_SETS: render_answer_sets,
        PAGE_INSPECTION: render_inspection,
        PAGE_EXPLANATION: render_explanation,
        PAGE_COST_EXPLANATION: render_cost_explanation,
        PAGE_UNSAT: render_unsat,
    }
    pages.get(current_page(), render_editor)()


def render_editor() -> None:
    _render_title("Editor")

    editor_column, options_column = st.columns([0.68, 0.32], gap="large")
    with editor_column:
        st.write("Upload your program or write it from scratch.")
        st.caption(
            "Uploading only loads a copy into the editor — the original file "
            "on your computer is never modified. Use **Download** to save "
            "your work."
        )
        uploaded = st.file_uploader("Open ASP file", type=["lp", "asp", "txt"])
        # Load the file content only ONCE per uploaded file: while the file
        # stays in the uploader every rerun would otherwise overwrite the
        # user's edits with the original content.
        if uploaded is not None and st.session_state.get("_last_upload_id") != uploaded.file_id:
            st.session_state._last_upload_id = uploaded.file_id
            st.session_state.program = uploaded.getvalue().decode("utf-8")
            st.session_state.file_name = uploaded.name
            st.session_state.program_editor = st.session_state.program

        st.text_area(
            "Program source",
            height=560,
            key="program_editor",
            placeholder="ASP program here...",
            label_visibility="collapsed",
        )
        st.session_state.program = st.session_state.program_editor

    with options_column:
        st.subheader("Configuration")
        with st.form("debug_options"):
            include_rules = st.toggle("Rules", value=True)
            include_literals = st.toggle("Literals", value=True)
            answer_count = st.number_input(
                "Answer sets",
                min_value=1,
                max_value=100,
                value=1,
                step=1,
            )
            if st.form_submit_button("Explain", type="primary", width="stretch"):
                actions.explain_program(include_rules, include_literals, int(answer_count))

        # NOTE: st.download_button snapshots its payload when it is rendered,
        # i.e. at the previous rerun. Text typed in the editor reaches the
        # server on the first interaction (e.g. clicking outside the text
        # area), which triggers a rerun that refreshes the payload. Keeping
        # the button disabled while the program is empty prevents the
        # confusing "downloads an empty file" case.
        program_text = st.session_state.program or ""
        st.download_button(
            "Download",
            program_text,
            file_name=st.session_state.file_name or "program.lp",
            mime="text/plain",
            width="stretch",
            disabled=not program_text.strip(),
            help="Saves the current editor content. If you just typed, click anywhere first so the editor content is committed.",
        )

        st.divider()
        st.subheader("Instructions")

        with st.expander("Rule annotations: @ignore and @correct"):
            st.markdown(
                """
Annotations are written **after the final dot of a rule, on the same
statement** (not on their own line, and not inside a `%` comment):

- `@ignore` — the rule is excluded entirely, from both solving and
  debugging: like commenting it out, but stating the intention.
- `@correct` — the rule stays active and contributes to the answer sets,
  but it is *trusted*: it can never be blamed in an explanation, so the
  blame flows through it to its premises. Use it on rules you know are
  right to focus the debugger on the rest of the program.
                """
            )
            st.code(
                "% under suspicion: instrumented and blamable as usual\n"
                "assigned(P,D) : day(D) :- patient(P).\n"
                "\n"
                "% I know this rule is right: never blame it\n"
                "busy(D) :- assigned(P,D). @correct\n"
                "\n"
                "% temporarily out of the picture (solving included)\n"
                ":- busy(D), holiday(D). @ignore\n",
                language="prolog",
            )

        with st.expander("Supported ASP subset and limitations"):
            st.markdown(
                """
##### Supported constructs:
- normal, choice and disjunctive rules;
- constraints;
- intervals (`a(1..4)`);
- string constants (`p("text")`); 
- `#count` and `#sum` aggregates (with interactive expansion);
- weak constraints and `#minimize`/`#maximize`; 
- `#const`; 
- inline `%` comments; 
- rules written across several lines (they are merged automatically).

##### Limitations:
- `#min`/`#max` aggregates are treated as plain rules (no expansion).
- Pooling (`a(1;2)`) and conditional literals in rule bodies (`p : q`)
  are not handled by the explanation machinery.
- `#script`/`#external`/theory directives are ignored.
- Classical negation (`-a`) is untested.
- Explanations follow the solver's assignment order: rules involving
  atoms assigned *after* the inspected literal are not considered.
                """
            )
        with st.expander("Privacy and LLM usage"):
            st.markdown(
                """
E-ASP is free to use. 
The optional LLM explanation feature sends the explanation context to an external third-party service. 
Do not use this feature with sensitive or confidential data. 
Service availability and usage limits may apply.
                """
            )



def render_answer_sets() -> None:
    _render_title("Answer Sets")
    _render_navigation(back=True)

    answer_sets: list[str] = st.session_state.answer_sets
    if not answer_sets:
        st.info("No answer set available.")
        return

    selected = st.selectbox(
        "Select the answer set to explain",
        range(len(answer_sets)),
        format_func=lambda index: f"Answer set {index + 1}",
        index=min(st.session_state.selected_answer_set, len(answer_sets) - 1),
        width=250,
    )
    st.session_state.selected_answer_set = selected

    with st.container(border=True):
        st.code(", ".join([e if i == 0 or i % 10 != 0 else "\n" + e for i, e in enumerate(str(answer_sets[selected]).split(", "))]), language="prolog")
        st.space()
        if st.button("Inspect", type="primary", width=250):
            actions.inspect_answer_set(selected)


def render_inspection() -> None:
    _render_title("Inspection")
    _render_navigation(back=True)

    atoms: list[QueryAtom] = st.session_state.answer_atoms
    if not atoms:
        st.info("No literal available.")
        return

    justifier: Justifier = st.session_state.justifier
    literal = st.selectbox("Select literal to explain", atoms, format_func=str)
    if st.button("Explain literal", type="primary", width=250):
        actions.explain_literal(literal)

    # For optimization problems the user can also ask why there is no answer
    # set with a better cost at a given level (as in the Java version, the
    # section is always visible when the program has weak constraints).
    if justifier.opt_problem():
        st.divider()
        st.subheader("Cost Inspection")
        levels = justifier.request_cost_level()
        if levels:
            level = st.selectbox("Cost level", levels, format_func=str)
            if st.button("Explain optimality", width="stretch"):
                actions.explain_optimality(level)
        else:
            st.caption("No cost level available for this answer set.")


def render_explanation() -> None:
    _render_title("Explanation")
    _render_navigation(back=True, home=True)
    _render_chain(st.session_state.chain)
    render_response_groups(st.session_state.responses, allow_literal_explain=True)


def render_cost_explanation() -> None:
    _render_title("Optimality Explanation")
    _render_navigation(back=True, home=True)
    render_response_groups(st.session_state.responses, allow_literal_explain=False)


def render_unsat() -> None:
    _render_title("Unsatisfiable Program")
    _render_navigation(home=True)
    render_response_groups(st.session_state.responses, allow_literal_explain=False)


def __render_rules_and_literals(rules: list, literals: list, allow_literal_explain: bool) -> None:
    if len(rules) > 0:
        with st.container(border=True):
            st.html(f"<h2>Rules ({len(rules)})</h2>")
            _render_rule_group(rules)

    if len(literals) > 0:
        with st.container(border=True):
            st.html(f"<h2>Selected Literals ({len(literals)})</h2>")
            _render_code_group(literals)

            if allow_literal_explain and literals:
                st.divider()
                selected = st.selectbox(
                    "Next literal",
                    literals,
                    format_func=lambda item: item.rule,
                )
                if st.button(
                        "Explain selected literal",
                        type="primary",
                        width="stretch",
                ):
                    actions.explain_next_literal(selected.rule)

def render_response_groups(responses: list[Response], *, allow_literal_explain: bool) -> None:
    rules = [response for response in responses if response.type in {RULE_TYPE, AGGREGATE_TYPE}]
    facts = [response for response in responses if response.type == FACT_TYPE]
    literals = [response for response in responses if response.type == LITERAL_TYPE]

    if len(facts) != 0:
        rules_column, details_column = st.columns([0.65, 0.35], gap="large")
        with rules_column:
            __render_rules_and_literals(rules, literals, allow_literal_explain)
        with details_column:
            with st.container(border=True):
                st.html(f"<h2>Input Facts ({len(facts)})</h2>")
                _render_code_group(facts)
    else:
        __render_rules_and_literals(rules, literals, allow_literal_explain)


    render_llm_explanation_panel()


def render_aggregate(rule: str) -> None:
    st.code(rule, language="prolog")
    justifier: Justifier = st.session_state.justifier
    try:
        aggregates = justifier.expand_aggregate(rule)
    except Exception as exc:
        st.warning(str(exc))
        return

    if not aggregates:
        return

    for key, values in aggregates.items():
        title = _aggregate_title(key, justifier.truth_aggregate(rule, key))
        with st.expander(title):
            _render_aggregate_values(values)


def render_llm_explanation_panel() -> None:
    st.subheader("Natural-language explanation")

    generate_column, settings_column = st.columns(2, width=550)
    with settings_column:
        with st.popover("Settings", width=250):
            st.caption("Configure the language model and its output.")
            api_key = st.text_input(
                "API Key (optional)",
                value=st.session_state.api_key,
            )
            language = st.selectbox(
                "Preferred language",
                st.session_state.languages,
                index=st.session_state.languages.index("English"),
            )
            model = st.selectbox("Select model", st.session_state.llm_models)
            temperature = st.slider(
                "Temperature",
                min_value=0.0,
                max_value=1.5,
                value=0.7,
                step=0.1,
                key="llm_temperature",
            )
            explanation_mode = st.selectbox("Explanation mode", options=["Oriented to ASP users", "Oriented to domain experts"], index=0)

            if st.button("Preview prompt", width=250):
                actions.prepare_discursive_prompt(language, explanation_mode == "Oriented to ASP users")

    with generate_column:
        if st.button("Generate explanation", type="primary", width=250):
            clean_api_key = api_key.strip() if api_key is not None and api_key.strip() != "" else None
            st.session_state.api_key = clean_api_key
            with st.spinner("Generating explanation...", show_time=True):
                actions.generate_discursive_explanation(
                    clean_api_key,
                    model,
                    float(temperature),
                    language,
                    explanation_mode == "Oriented to ASP users"
                )

    if st.session_state.llm_error:
        st.error(
            "An error occurred during LLM explanation. The number of daily "
            "free tokens might be expired, try with another model or use "
            "your own api key. If you used a custom api key, please check it is valid."
        )

    if st.session_state.llm_explanation:
        with st.container(border=True):
            st.markdown(st.session_state.llm_explanation)
    else:
        st.caption("Generate a plain-language explanation of the result.")

    if st.session_state.llm_prompt:
        with st.expander("Advanced: LLM prompt"):
            st.text_area(
                "Prompt prepared for the LLM",
                value=st.session_state.llm_prompt,
                height=260,
                disabled=True,
                label_visibility="collapsed",
            )


def _render_title(title: str) -> None:
    st.header(title)


def _render_navigation(*, back: bool = False, home: bool = False) -> None:
    if not (back or home):
        return

    columns = st.columns([1, 1, 5])
    if back:
        with columns[0]:
            if st.button("Back", key=f"back_{current_page()}"):
                navigate_back()
                st.rerun()
    if home:
        with columns[1 if back else 0]:
            if st.button("Home", key=f"home_{current_page()}"):
                go_home()
                st.rerun()


def _render_chain(chain: list[QueryAtom]) -> None:
    if not chain:
        return

    st.markdown("Chain: " + ", ".join([f":violet[{str(chain[i])}]" if i % 2 == 0 else str(chain[i]) for i in range(len(chain))]))

def _render_rule_group(responses: list[Response]) -> None:
    if not responses:
        st.caption("Empty")
        return
    for response in responses:
        if response.type == AGGREGATE_TYPE:
            render_aggregate(response.rule)
        else:
            st.code(response.rule, language="prolog")


def _render_code_group(responses: list[Response]) -> None:
    if not responses:
        st.caption("Empty")
        return
    for response in responses:
        st.code(response.rule, language="prolog")


def _render_aggregate_values(values: dict[str, list[str]]) -> None:
    if not values:
        st.caption("Empty")
        return

    for set_id, atoms in values.items():
        label = _aggregate_group_label(set_id)
        text = _atom_list_text(atoms)
        st.markdown(
            f'<div class="easp-atom-row"><strong>{escape(label)}:</strong> {escape(text)}</div>',
            unsafe_allow_html=True,
        )


def _aggregate_title(key: str, message: str) -> str:
    cleaned_key = _clean_piece(key)
    cleaned_message = _clean_piece(message)
    if not cleaned_key:
        return cleaned_message or "Aggregate details"
    if not cleaned_message or cleaned_message == cleaned_key:
        return cleaned_key
    if cleaned_message.startswith(cleaned_key):
        return cleaned_message
    return f"{cleaned_key}: {cleaned_message}"


def _aggregate_group_label(set_id: str) -> str:
    cleaned = _clean_piece(set_id)
    if cleaned.startswith("<") and cleaned.endswith(">"):
        return cleaned
    return f"Group {cleaned}" if cleaned else "Group"


def _atom_list_text(atoms: Iterable[str]) -> str:
    cleaned_atoms = [_clean_piece(atom) for atom in atoms]
    visible_atoms = [atom for atom in cleaned_atoms if atom]
    return ", ".join(visible_atoms) if visible_atoms else "No atoms"


def _clean_piece(value: str) -> str:
    """Trim whitespace and leading/trailing commas from debugger output."""
    return value.strip().strip(",").strip()
