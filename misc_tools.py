# misc_tools_integration_patch.py
# Version E
#
# Copy these parts into your real misc_tools.py

# 1) IMPORT
# Put this near your other imports:
from mrp_module import render_mrp_tab, register_mrp_bridge

# 2) HELPER TO FEED MRP
# Add this after get_sos_access_token and SOSReadonlyClient already exist:

def get_open_sales_orders_for_mrp():
    token = get_sos_access_token()
    client = SOSReadonlyClient(token)

    if hasattr(client, "get_open_sales_orders"):
        return client.get_open_sales_orders() or []

    for method_name in [
        "list_open_sales_orders",
        "search_sales_orders",
        "get_sales_orders",
        "list_sales_orders",
    ]:
        method = getattr(client, method_name, None)
        if callable(method):
            try:
                result = method()
                if isinstance(result, list):
                    return result
                if isinstance(result, dict):
                    for key in ("data", "items", "results", "salesorders", "sales_orders"):
                        if isinstance(result.get(key), list):
                            return result.get(key) or []
            except TypeError:
                continue

    return []

# 3) REGISTER THE BRIDGE
# Add this once after the helper above:
register_mrp_bridge(
    get_sos_access_token=get_sos_access_token,
    SOSReadonlyClient=SOSReadonlyClient,
    get_open_sales_orders_for_mrp=get_open_sales_orders_for_mrp,
)

# 4) SAFE WRAPPER
def render_mrp_tab_lazy():
    import streamlit as st
    try:
        render_mrp_tab()
    except Exception as exc:
        st.error(f"MRP failed: {exc}")

# 5) CALL IT INSIDE YOUR WEEKLY PRODUCTION WORKSPACE
# Use:
#     render_mrp_tab_lazy()

# 6) IMPORTANT
# In misc_tools.py, call the lazy wrapper, not render_mrp_tab() directly.
# Good:
#     render_mrp_tab_lazy()
#
# And do NOT import misc_tools from mrp_module.py
