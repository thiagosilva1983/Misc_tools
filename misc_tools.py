# misc_tools_version_C_patch.py
# VERSION C
#
# Objetivo:
# 1) Corrigir o erro de session_state do active_workspace.
# 2) Fazer o MRP abrir dentro do fluxo do app sem quebrar navigation.
# 3) Evitar setar st.session_state["active_workspace"] depois do widget com a mesma key.
#
# COMO USAR:
# - Não precisa renomear o arquivo real do GitHub.
# - O arquivo real continua sendo: misc_tools.py
# - Copie os blocos abaixo para dentro do seu misc_tools.py

import streamlit as st


# ============================================================
# BLOCO 1 — cole perto do topo do arquivo, depois dos imports
# ============================================================
def init_workspace_state():
    if "active_workspace" not in st.session_state:
        st.session_state["active_workspace"] = "Home"

    if "workspace_target" in st.session_state:
        st.session_state["active_workspace"] = st.session_state.pop("workspace_target")


def go_to_workspace(workspace_name: str):
    st.session_state["workspace_target"] = workspace_name
    st.rerun()


# =========================================================================
# BLOCO 2 — substitua sua função render_workspace_selector() por esta
# =========================================================================
def render_workspace_selector():
    options = ['Home', 'Label Studio', 'Box Build Report', 'SOS Inventory', 'Weekly Production', 'MRP']

    current_workspace = st.session_state.get('active_workspace', 'Home')
    default_index = options.index(current_workspace) if current_workspace in options else 0

    selected = st.radio(
        'Workspace',
        options,
        index=default_index,
        horizontal=True,
        key='workspace_selector_radio_vc',
        label_visibility='collapsed',
    )

    if selected != st.session_state.get('active_workspace'):
        st.session_state['active_workspace'] = selected

    return selected


# ==================================================================================
# BLOCO 3 — dentro de render_home_workspace(), troque qualquer navegação direta
# tipo:
#     st.session_state['active_workspace'] = 'Weekly Production'
# por:
#     go_to_workspace('Weekly Production')
# ==================================================================================
#
# EXEMPLO:
#
# if st.button("Weekly Production", use_container_width=True):
#     go_to_workspace("Weekly Production")
#
# if st.button("SOS Inventory", use_container_width=True):
#     go_to_workspace("SOS Inventory")
#
# if st.button("MRP", use_container_width=True):
#     go_to_workspace("MRP")


# ==================================================================================
# BLOCO 4 — no final do arquivo, antes de renderizar o app, use este fluxo
# Substitua seu bloco final de navegação por este:
# ==================================================================================
#
# init_workspace_state()
# inject_branding()
# render_app_header()
# active_workspace = render_workspace_selector()
#
# if active_workspace == 'Home':
#     render_home_workspace()
# elif active_workspace == 'Label Studio':
#     render_label_tab()
# elif active_workspace == 'Box Build Report':
#     render_box_build_workspace()
# elif active_workspace == 'SOS Inventory':
#     render_sos_workspace()
# elif active_workspace == 'Weekly Production':
#     st.session_state['weekly_production_view_mode'] = 'WEEKLY'
#     render_weekly_production_workspace()
# elif active_workspace == 'MRP':
#     st.session_state['weekly_production_view_mode'] = 'MRP'
#     render_weekly_production_workspace()
# else:
#     go_to_workspace('Home')


# ==================================================================================
# BLOCO 5 — dentro de render_weekly_production_workspace(), adicione isto no começo
# para alternar entre Weekly e MRP dentro da mesma área
# ==================================================================================
#
# view_mode = st.session_state.get('weekly_production_view_mode', 'WEEKLY')
#
# top_a, top_b = st.columns([1, 1])
# with top_a:
#     if st.button('Weekly View', use_container_width=True, key='weekly_mode_btn_vc'):
#         st.session_state['weekly_production_view_mode'] = 'WEEKLY'
#         st.rerun()
# with top_b:
#     if st.button('MRP View', use_container_width=True, key='mrp_mode_btn_vc'):
#         st.session_state['weekly_production_view_mode'] = 'MRP'
#         st.rerun()
#
# if view_mode == 'MRP':
#     render_mrp_tab_lazy()
#     return
#
# # resto da sua Weekly Production continua abaixo normalmente


# ==================================================================================
# BLOCO 6 — REMOVER do misc_tools.py
# Remova esse trecho da versão B:
#
# if active_workspace == 'MRP':
#     st.session_state['active_workspace'] = 'Weekly Production'
#     st.session_state['weekly_production_view_mode'] = 'MRP'
#     st.rerun()
#
# Esse bloco foi a origem da navegação confusa.
# ==================================================================================
