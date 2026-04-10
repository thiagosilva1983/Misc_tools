# Wibotic misc_tools.py — Version F patch
# This is a targeted patch for the MRP integration error:
# TypeError caused by calling render_mrp_tab() without the required client argument.
#
# HOW TO USE
# 1) Open your real file: misc_tools.py
# 2) Find the existing render_mrp_tab_lazy() function and replace it with the version below.
# 3) Confirm your Weekly Production workspace calls render_mrp_tab_lazy() only.
# 4) Confirm you are NOT importing misc_tools from mrp_module.py anymore in a way that creates circular imports.
#
# -------------------------------------------------------------------
# REQUIRED IMPORTS
# -------------------------------------------------------------------
# Make sure these already exist in misc_tools.py before using the function:
#
# import streamlit as st
# from mrp_module import render_mrp_tab
#
# And make sure get_sos_access_token and SOSReadonlyClient already exist
# in misc_tools.py itself.

import streamlit as st


def render_mrp_tab_lazy():
    """
    Lazy MRP renderer used inside Weekly Production workspace.

    This version fixes the error where render_mrp_tab() was called without
    the required 'client' argument.
    """
    try:
        token = get_sos_access_token()
        if not token:
            st.error("MRP failed: could not get SOS access token.")
            return

        client = SOSReadonlyClient(access_token=token)
        render_mrp_tab(client)

    except Exception as e:
        st.error(f"MRP failed: {e}")


# -------------------------------------------------------------------
# EXAMPLE: WEEKLY PRODUCTION WORKSPACE SECTION
# -------------------------------------------------------------------
# Inside your render_weekly_production_workspace() or equivalent, keep it like this:
#
# def render_weekly_production_workspace():
#     st.subheader("Weekly Production")
#
#     weekly_tab, mrp_tab = st.tabs(["Weekly Board", "MRP"])
#
#     with weekly_tab:
#         render_weekly_production_tab()
#
#     with mrp_tab:
#         render_mrp_tab_lazy()


# -------------------------------------------------------------------
# EXAMPLE: MAIN WORKSPACE ROUTER
# -------------------------------------------------------------------
# In the main selector/router, keep this shape:
#
# if active_workspace == "Weekly Production":
#     render_weekly_production_workspace()
# elif active_workspace == "SOS Inventory":
#     render_sos_inventory_workspace()
#
# IMPORTANT:
# Do not also render MRP as a top-level separate workspace if it already
# lives inside Weekly Production tabs, unless you intentionally want both.


# -------------------------------------------------------------------
# IMPORTANT CHECK FOR mrp_module.py
# -------------------------------------------------------------------
# If mrp_module.py contains something like this:
#
#     from misc_tools import get_sos_access_token, SOSReadonlyClient
#
# that can create circular import problems.
#
# BETTER OPTIONS:
#
# OPTION A — pass client from misc_tools into render_mrp_tab(client)
# and do not try to import misc_tools from mrp_module.py for SOS access.
#
# OPTION B — move SOS helper code into a shared helper file, for example:
# sos_helpers.py
#
# Then both misc_tools.py and mrp_module.py import from sos_helpers.py
# instead of importing each other.


# -------------------------------------------------------------------
# SAFER MRP MODULE INTERFACE
# -------------------------------------------------------------------
# In mrp_module.py, the function should look like this:
#
# def render_mrp_tab(client):
#     ...
#
# And anything inside mrp_module.py that needs SOS data should use that
# passed-in client, instead of trying to recreate the client by importing
# misc_tools.py again.


# -------------------------------------------------------------------
# OPTIONAL: EXAMPLE OF A CLEANER MRP MODULE PATTERN
# -------------------------------------------------------------------
# This is not your whole app — just a reference block.

def example_mrp_pattern_only(client):
    """
    Example only. Do not use this function directly unless you want a stub.
    """
    st.write("MRP placeholder loaded with existing SOS client.")
    st.caption("Replace this with your real MRP rendering logic.")


# -------------------------------------------------------------------
# FULL REPLACEMENT BLOCK YOU CAN PASTE
# -------------------------------------------------------------------
# Copy this exact block into misc_tools.py where your current lazy MRP
# function lives:

PASTE_BLOCK = r"""
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
"""
