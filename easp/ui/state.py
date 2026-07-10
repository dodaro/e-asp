from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from easp.config import Settings


PAGE_EDITOR = "editor"
PAGE_ANSWER_SETS = "answer_sets"
PAGE_INSPECTION = "inspection"
PAGE_EXPLANATION = "explanation"
PAGE_COST_EXPLANATION = "cost_explanation"
PAGE_UNSAT = "unsat"

DEFAULT_FILE_NAME = "program.lp"


def init_state() -> None:
    """Create Streamlit state keys once per browser session."""
    defaults: dict[str, Callable[[], Any]] = {
        "page": lambda: PAGE_EDITOR,
        "history": list,
        "program": str,
        "program_editor": lambda: str(st.session_state.get("program", "")),
        "file_name": lambda: DEFAULT_FILE_NAME,
        "justifier": lambda: None,
        "answer_sets": list,
        "answer_atoms": list,
        "chain": list,
        "responses": list,
        "selected_answer_set": int,
        "settings": Settings.load,
        "llm_explanation": str,
        "llm_error": str,
        "llm_prompt": str,
        "llm_models": lambda : ["nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3-super-120b-a12b:free", "openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free", "google/gemma-4-31b-it:free"],
        "llm_temperature": lambda: 0.7,
        "languages": lambda : ["Arabic", "Bengali", "Bulgarian", "Catalan", "Chinese", "Croatian", "Czech", "Danish", "Dutch", "English", "Estonian", "Finnish", "French", "German", "Greek", "Hebrew", "Hindi", "Hungarian", "Indonesian", "Italian", "Japanese", "Korean", "Latvian", "Lithuanian", "Malay", "Norwegian", "Persian", "Polish", "Portuguese", "Romanian", "Russian", "Serbian", "Slovak", "Slovenian", "Spanish", "Swedish", "Thai", "Turkish", "Ukrainian", "Urdu", "Vietnamese"],
        "api_key": str
    }
    for key, factory in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = factory()


def current_page() -> str:
    return str(st.session_state.get("page", PAGE_EDITOR))


def navigate(page: str) -> None:
    current = current_page()
    if current != page:
        st.session_state.history.append(current)
    st.session_state.page = page


def navigate_back() -> None:
    if st.session_state.history:
        st.session_state.page = st.session_state.history.pop()
    else:
        st.session_state.page = PAGE_EDITOR


def go_home() -> None:
    st.session_state.page = PAGE_EDITOR
    st.session_state.history = []


def reset_explanation_state() -> None:
    """Drop derived UI data when a new program run starts."""
    st.session_state.answer_sets = []
    st.session_state.answer_atoms = []
    st.session_state.chain = []
    st.session_state.responses = []
    st.session_state.selected_answer_set = 0
    clear_llm_explanation()


def clear_llm_explanation() -> None:
    st.session_state.llm_explanation = ""
    st.session_state.llm_error = ""
    st.session_state.llm_prompt = ""
