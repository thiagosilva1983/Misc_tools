# misc_tools_patch_REV_F.py
# Replace only the MRP integration parts in your real misc_tools.py

# 1) import near your other imports
from mrp_module import render_mrp_tab


# 2) replace your current render_mrp_tab_lazy() with this exact block
def render_mrp_tab_lazy():
    try:
        token = get_sos_access_token()
        if not token:
            st.error("MRP failed: could not get SOS access token.")
            return

        client = SOSReadonlyClient(access_token=token)
        render_mrp_tab(client)

    except Exception as e:
        st.error(f"MRP failed: {e}")


# 3) inside render_weekly_production_workspace(), use tabs like this
def render_weekly_production_workspace():
    st.subheader("Weekly Production")

    weekly_tab, mrp_tab = st.tabs(["Weekly Board", "MRP"])

    with weekly_tab:
        render_weekly_production_tab()

    with mrp_tab:
        render_mrp_tab_lazy()


# 4) main router shape
inject_branding()
render_app_header()
active_workspace = render_workspace_selector()

if active_workspace == 'Home':
    render_home_workspace()
elif active_workspace == 'Label Studio':
    render_label_tab()
elif active_workspace == 'Box Build Report':
    render_box_build_workspace()
elif active_workspace == 'SOS Inventory':
    render_sos_workspace()
else:
    render_weekly_production_workspace()
