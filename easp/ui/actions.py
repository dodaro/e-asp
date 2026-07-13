from __future__ import annotations

import os

import streamlit as st

from easp.llm_explainer import (
    AggregateDetail,
    AggregateGroup,
    ExplanationContext,
    LlmExplanationError,
    OpenRouterClient,
    build_discursive_prompt,
)
from easp.models import CostLevel, QueryAtom, Response
from easp.services import (
    ComputeAnswerSetsService,
    DebugProgramService,
    ExplainAtomService,
    ExplainCostService,
    Justifier,
    RetrieveAtomsService,
)
from easp.ui.state import (
    PAGE_ANSWER_SETS,
    PAGE_COST_EXPLANATION,
    PAGE_EXPLANATION,
    PAGE_INSPECTION,
    PAGE_UNSAT,
    clear_llm_explanation,
    current_page,
    navigate,
    reset_explanation_state,
)


def explain_program(include_rules: bool, include_literals: bool, answer_count: int) -> None:
    if not st.session_state.program.strip():
        st.error("Create or open a program first.")
        return
    if not (include_rules or include_literals):
        st.error("Select at least one debugging mode.")
        return

    try:
        reset_explanation_state()
        justifier = Justifier(
            st.session_state.program,
            include_rules,
            include_literals,
            settings=st.session_state.settings,
        )
        answer_sets = ComputeAnswerSetsService(justifier, answer_count).run()
        st.session_state.justifier = justifier

        if answer_sets is None:
            st.session_state.responses = DebugProgramService(justifier).run()
            navigate(PAGE_UNSAT)
        else:
            st.session_state.answer_sets = answer_sets
            navigate(PAGE_ANSWER_SETS)
        st.rerun()
    except Exception as exc:
        _show_error(exc)


def inspect_answer_set(index: int) -> None:
    try:
        clear_llm_explanation()
        justifier = _require_justifier()
        st.session_state.answer_atoms = RetrieveAtomsService(justifier, index).run()
        st.session_state.selected_answer_set = index
        navigate(PAGE_INSPECTION)
        st.rerun()
    except Exception as exc:
        _show_error(exc)


def explain_literal(atom: QueryAtom) -> None:
    try:
        clear_llm_explanation()
        justifier = _require_justifier()
        st.session_state.chain = [atom]
        st.session_state.responses = ExplainAtomService(justifier, [], atom, False).run()
        navigate(PAGE_EXPLANATION)
        st.rerun()
    except Exception as exc:
        _show_error(exc)


def explain_next_literal(rule: str) -> None:
    try:
        clear_llm_explanation()
        justifier = _require_justifier()
        query_atom = justifier.derive_query_atom(rule)
        if query_atom in st.session_state.chain:
            st.warning("Literal already present in the explanation chain.")
            return

        st.session_state.responses = ExplainAtomService(
            justifier,
            st.session_state.chain,
            query_atom,
            False,
        ).run()
        st.session_state.chain = [*st.session_state.chain, query_atom]
        navigate(PAGE_EXPLANATION)
        st.rerun()
    except Exception as exc:
        _show_error(exc)


def explain_optimality(level: CostLevel) -> None:
    try:
        clear_llm_explanation()
        justifier = _require_justifier()
        st.session_state.responses = ExplainCostService(justifier, level, True).run()
        navigate(PAGE_COST_EXPLANATION)
        st.rerun()
    except Exception as exc:
        _show_error(exc)


def generate_discursive_explanation(api_key: str | None, model: str, temperature: float, language: str, technical_explanation_mode: bool) -> None:
    if not st.session_state.responses and not st.session_state.answer_sets:
        st.warning("Generate answer sets or an explanation first.")
        return

    context = _llm_context()
    st.session_state.llm_prompt = build_discursive_prompt(context, language, technical_explanation_mode)

    if api_key is None or api_key == "":
        api_key = _openrouter_api_key()
    if not api_key:
        st.session_state.llm_error = (
            "Configure OPENROUTER_API_KEY or st.secrets['openrouter_api_key'] before calling the LLM."
        )
        return

    client = OpenRouterClient(
        api_key,
        app_title="E-ASP",
        site_url=None,
    )

    try:
        st.session_state.llm_error = ""
        st.session_state.llm_explanation = client.explain(
            context,
            model=model.strip(),
            temperature=temperature,
            language=language,
            technical_explanation_mode=technical_explanation_mode
        )
    except LlmExplanationError as exc:
        st.session_state.llm_error = str(exc)
    except Exception as exc:
        st.session_state.llm_error = str(exc) or exc.__class__.__name__


def prepare_discursive_prompt(language, technical_explanation_mode) -> None:
    if not st.session_state.responses and not st.session_state.answer_sets:
        st.warning("Generate answer sets or an explanation first.")
        return

    st.session_state.llm_error = ""
    st.session_state.llm_prompt = build_discursive_prompt(_llm_context(), language, technical_explanation_mode)


def _require_justifier() -> Justifier:
    justifier = st.session_state.justifier
    if justifier is None:
        raise RuntimeError("Run a program before requesting an explanation.")
    return justifier


def _show_error(exc: Exception) -> None:
    st.error(str(exc) or exc.__class__.__name__)


def _openrouter_api_key() -> str:
    """API key lookup: OPENROUTER_API_KEY env var, then Streamlit secrets."""
    env_key = os.getenv("OPENROUTER_API_KEY")
    if env_key:
        return env_key.strip()
    try:
        return str(st.secrets.get("openrouter_api_key", "")).strip()
    except Exception:  # no secrets.toml configured
        return ""


def _llm_context() -> ExplanationContext:
    return ExplanationContext(
        program=st.session_state.program,
        page=current_page(),
        answer_sets=list(st.session_state.answer_sets),
        selected_answer_set=int(st.session_state.selected_answer_set),
        chain=[str(atom) for atom in st.session_state.chain],
        responses=list(st.session_state.responses),
        aggregate_details=_aggregate_details_for_prompt(),
    )


def _aggregate_details_for_prompt() -> list[AggregateDetail]:
    justifier = st.session_state.justifier
    if justifier is None:
        return []

    details: list[AggregateDetail] = []
    for response in st.session_state.responses:
        if not _is_aggregate_response(response):
            continue

        try:
            expanded = justifier.expand_aggregate(response.rule)
            for key, groups in expanded.items():
                details.append(
                    AggregateDetail(
                        rule=response.rule,
                        key=key,
                        truth_message=justifier.truth_aggregate(response.rule, key),
                        groups=[
                            AggregateGroup(label=str(group_label), atoms=list(atoms))
                            for group_label, atoms in groups.items()
                        ],
                    )
                )
        except Exception as exc:
            details.append(
                AggregateDetail(
                    rule=response.rule,
                    key="",
                    truth_message="",
                    error=str(exc) or exc.__class__.__name__,
                )
            )
    return details


def _is_aggregate_response(response: Response) -> bool:
    return response.type == 3


def _setting_value(env_key: str, secret_key: str, default: str | None) -> str | None:
    env_value = os.getenv(env_key)
    if env_value:
        return env_value
    try:
        secret_value = st.secrets.get(secret_key, default)
    except Exception:
        secret_value = default
    if secret_value is None:
        return None
    return str(secret_value).strip()
