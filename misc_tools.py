# misc_tools_mrp_fix_REV_F.py
# Apply these exact changes to your real GitHub file: misc_tools.py
#
# Why this fix:
# - Your base app already routes Weekly Production correctly.
# - Your broken versions called render_mrp_tab() without the required client.
# - REV F mrp_module expects render_mrp_tab(client).
#
# Confirmed from your uploaded files:
# 1) misc_tools_version_A_full_fixed.py routes MRP inside Weekly Production and uses
#    st.session_state['weekly_production_view_mode'] = 'MRP'
# 2) mrp_module_REV_F.py defines render_mrp_tab(client)
#
# ------------------------------------------------------------
# PATCH 1 — IMPORT
# ------------------------------------------------------------
# Near the top of misc_tools.py, keep/import this:

from mrp_module import render_mrp_tab


# ------------------------------------------------------------
# PATCH 2 — ADD OR REPLACE THIS FUNCTION
# ------------------------------------------------------------
# Replace your current render_mrp_tab_lazy() with this exact version.

def render_mrp_tab_lazy():
    try:
        token = get_sos_access_token()
        if not token:
            st.error("MRP failed: could not get SOS access token.")
            return

        try:
            client = SOSReadonlyClient(access_token=token)
        except TypeError:
            client = SOSReadonlyClient(token)

        render_mrp_tab(client)

    except Exception as e:
        st.error(f"MRP failed: {e}")


# ------------------------------------------------------------
# PATCH 3 — WEEKLY PRODUCTION WORKSPACE
# ------------------------------------------------------------
# Inside render_weekly_production_workspace(), keep the embedded selector shape.
# The important part is that MRP view calls render_mrp_tab_lazy() only.

def render_weekly_production_workspace():
    if "weekly_is_refreshing" not in st.session_state:
        st.session_state["weekly_is_refreshing"] = False

    st.markdown(
        f"""<div style='display:flex; align-items:center; gap:0.6rem; margin-bottom:0.2rem;'>
        <img src="data:image/png;base64,{LOGO_B64}" style="width:28px; height:28px; object-fit:contain;" />
        <div style="font-size:2.05rem; font-weight:700; color:#0f172a;">Weekly Production</div>
        </div>""",
        unsafe_allow_html=True
    )
    st.caption('Live weekly production board from SOS. Priority is per sales order, item lines are grouped underneath, and shipped rows are highlighted green.')
    st.caption('Use the full SOS Inventory workspace for detailed inventory investigation and deep sales-order checks.')
    st.caption('Weekly Production now prefers manual refresh for stability while you edit priorities.')

    weekly_section = st.radio(
        'Weekly Production view',
        ['Board', 'MRP'],
        horizontal=True,
        key='weekly_production_view_mode',
        label_visibility='collapsed',
    )

    if weekly_section == 'MRP':
        st.caption('MRP is embedded inside Weekly Production in this version.')
        render_mrp_tab_lazy()
        return

    st.caption(f"Workflow state backend: {weekly_gsheet_backend_name()}")
    if st.session_state.get("weekly_is_refreshing", False):
        st.warning("Weekly board is updating from SOS...")
    else:
        st.caption(f"Weekly board status: idle | refresh limit: {weekly_get_refresh_limit()} SOs")

    # KEEP THE REST OF YOUR EXISTING WEEKLY BOARD CODE BELOW THIS POINT
    # Do not delete your manual SOS search, status block, refresh button,
    # smart priority assistant, or board rendering logic.
    #
    # Paste the rest of your existing render_weekly_production_workspace()
    # body below this comment, unchanged.


# ------------------------------------------------------------
# PATCH 4 — APP ROUTER AT BOTTOM OF FILE
# ------------------------------------------------------------
# Replace the final router block with this exact version.

inject_branding()
render_app_header()
init_workspace_state()
active_workspace = render_workspace_selector()

if active_workspace == 'Home':
    render_home_workspace()
elif active_workspace == 'Label Studio':
    render_label_tab()
elif active_workspace == 'Box Build Report':
    render_box_build_workspace()
elif active_workspace == 'SOS Inventory':
    render_sos_workspace()
elif active_workspace == 'Weekly Production':
    if 'weekly_production_view_mode' not in st.session_state:
        st.session_state['weekly_production_view_mode'] = 'Board'
    render_weekly_production_workspace()
elif active_workspace == 'MRP':
    st.session_state['weekly_production_view_mode'] = 'MRP'
    render_weekly_production_workspace()
else:
    go_to_workspace('Home')


# ------------------------------------------------------------
# PATCH 5 — HOME BUTTON NAVIGATION SAFETY
# ------------------------------------------------------------
# Anywhere inside render_home_workspace(), if you currently do this:
#
#     st.session_state['active_workspace'] = 'Weekly Production'
#
# or:
#
#     st.session_state['active_workspace'] = 'MRP'
#
# replace with:
#
#     go_to_workspace('Weekly Production')
#
# and:
#
#     go_to_workspace('MRP')
#
# Example:
#
# if st.button("Weekly Production", use_container_width=True):
#     go_to_workspace("Weekly Production")
#
# if st.button("MRP", use_container_width=True):
#     go_to_workspace("MRP")


# ------------------------------------------------------------
# PATCH 6 — IMPORTANT
# ------------------------------------------------------------
# In mrp_module.py, use the REV F version that defines:
#
#     def render_mrp_tab(client):
#         ...
#
# Do NOT use any older mrp_module.py that imports misc_tools internally
# for get_sos_access_token or SOSReadonlyClient.
