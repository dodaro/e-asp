from __future__ import annotations

import streamlit as st

from easp.ui.components import render_current_page
from easp.ui.state import init_state


def main() -> None:
    st.set_page_config(page_title="E-ASP", page_icon="E", layout="wide")
    init_state()
    render_current_page()


if __name__ == "__main__":
    main()
