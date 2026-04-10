# mrp_module.py
# Version E
#
# Purpose:
# - Render the MRP tab inside the Weekly Production workspace
# - Avoid circular imports with misc_tools.py
# - Use bridge injection from misc_tools.py for SOS access
#
# How it works:
# 1) misc_tools.py imports render_mrp_tab and register_mrp_bridge from this file
# 2) misc_tools.py calls register_mrp_bridge(get_sos_access_token, SOSReadonlyClient, get_open_sales_orders_for_mrp)
# 3) render_mrp_tab() can then use those functions/classes without importing misc_tools.py

from __future__ import annotations

from typing import Callable, Any, Dict, List, Optional
import traceback

import pandas as pd
import streamlit as st

_MRP_BRIDGE: Dict[str, Any] = {
    "get_sos_access_token": None,
    "SOSReadonlyClient": None,
    "get_open_sales_orders_for_mrp": None,
}

def register_mrp_bridge(
    get_sos_access_token: Optional[Callable] = None,
    SOSReadonlyClient: Optional[type] = None,
    get_open_sales_orders_for_mrp: Optional[Callable] = None,
) -> None:
    if get_sos_access_token is not None:
        _MRP_BRIDGE["get_sos_access_token"] = get_sos_access_token
    if SOSReadonlyClient is not None:
        _MRP_BRIDGE["SOSReadonlyClient"] = SOSReadonlyClient
    if get_open_sales_orders_for_mrp is not None:
        _MRP_BRIDGE["get_open_sales_orders_for_mrp"] = get_open_sales_orders_for_mrp

def _bridge_ready() -> bool:
    return (
        _MRP_BRIDGE["get_sos_access_token"] is not None
        and _MRP_BRIDGE["SOSReadonlyClient"] is not None
    )

def get_sos_client():
    if not _bridge_ready():
        raise RuntimeError(
            "MRP bridge not registered. In misc_tools.py, call register_mrp_bridge(...) "
            "after get_sos_access_token and SOSReadonlyClient are defined."
        )

    token_fn = _MRP_BRIDGE["get_sos_access_token"]
    client_cls = _MRP_BRIDGE["SOSReadonlyClient"]

    token = token_fn()
    if not token:
        raise RuntimeError("SOS access token is empty or unavailable.")

    return client_cls(token)

def get_open_sales_orders() -> List[dict]:
    helper = _MRP_BRIDGE.get("get_open_sales_orders_for_mrp")
    if helper is not None:
        orders = helper()
        return orders or []

    client = get_sos_client()

    candidate_methods = [
        "get_open_sales_orders",
        "list_open_sales_orders",
        "search_sales_orders",
        "get_sales_orders",
        "list_sales_orders",
    ]

    for method_name in candidate_methods:
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
            except Exception:
                continue

    raise RuntimeError(
        "Could not fetch open sales orders. Provide get_open_sales_orders_for_mrp "
        "from misc_tools.py via register_mrp_bridge(...)."
    )

def _safe_get(d: dict, *keys, default=None):
    cur = d
    for key in keys:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur

def _extract_order_header(order: dict) -> dict:
    so_number = (
        _safe_get(order, "salesorder_number")
        or _safe_get(order, "sales_order_number")
        or _safe_get(order, "number")
        or _safe_get(order, "doc_number")
        or _safe_get(order, "reference_number")
        or "UNKNOWN-SO"
    )

    customer = (
        _safe_get(order, "customer_name")
        or _safe_get(order, "customer", "name")
        or _safe_get(order, "customer")
        or ""
    )

    status = (
        _safe_get(order, "status")
        or _safe_get(order, "salesorder_status")
        or ""
    )

    return {
        "so_number": str(so_number),
        "customer": str(customer) if customer is not None else "",
        "status": str(status) if status is not None else "",
    }

def _extract_lines(order: dict) -> list[dict]:
    lines = (
        _safe_get(order, "line_items")
        or _safe_get(order, "items")
        or _safe_get(order, "lines")
        or []
    )

    normalized = []
    for idx, line in enumerate(lines, start=1):
        sku = (
            _safe_get(line, "sku")
            or _safe_get(line, "item_code")
            or _safe_get(line, "part_number")
            or _safe_get(line, "name")
            or _safe_get(line, "item_name")
            or f"LINE-{idx}"
        )

        description = (
            _safe_get(line, "description")
            or _safe_get(line, "item_name")
            or _safe_get(line, "name")
            or ""
        )

        qty = (
            _safe_get(line, "quantity")
            or _safe_get(line, "qty")
            or _safe_get(line, "ordered_quantity")
            or 0
        )

        try:
            qty = float(qty)
        except Exception:
            qty = 0

        normalized.append({
            "sku": str(sku),
            "description": str(description) if description is not None else "",
            "qty": qty,
        })

    return normalized

def build_mrp_table() -> pd.DataFrame:
    orders = get_open_sales_orders()

    rows = []
    for order in orders:
        header = _extract_order_header(order)
        for line in _extract_lines(order):
            rows.append({
                "Sales Order": header["so_number"],
                "Customer": header["customer"],
                "SO Status": header["status"],
                "Item": line["sku"],
                "Description": line["description"],
                "Required Qty": line["qty"],
            })

    if not rows:
        return pd.DataFrame(columns=[
            "Sales Order", "Customer", "SO Status", "Item", "Description", "Required Qty"
        ])

    df = pd.DataFrame(rows)
    df["Required Qty"] = pd.to_numeric(df["Required Qty"], errors="coerce").fillna(0)
    return df.sort_values(["Sales Order", "Item"]).reset_index(drop=True)

def render_mrp_tab() -> None:
    st.subheader("MRP")
    st.caption("Material requirements view based on open Sales Orders from SOS.")

    if "mrp_df" not in st.session_state:
        st.session_state["mrp_df"] = pd.DataFrame()

    c1, c2 = st.columns([1, 3])

    with c1:
        refresh = st.button("Refresh MRP", key="mrp_refresh_btn", use_container_width=True)

    if refresh:
        try:
            st.session_state["mrp_df"] = build_mrp_table()
            st.success("MRP refreshed.")
        except Exception as exc:
            st.error(f"MRP failed: {exc}")
            with st.expander("Technical details"):
                st.code(traceback.format_exc())

    df = st.session_state.get("mrp_df", pd.DataFrame())

    if df is None or df.empty:
        st.info("No MRP data loaded yet. Click Refresh MRP.")
        return

    col1, col2, col3 = st.columns(3)

    so_options = ["All"] + sorted(df["Sales Order"].dropna().astype(str).unique().tolist())
    item_options = ["All"] + sorted(df["Item"].dropna().astype(str).unique().tolist())
    cust_options = ["All"] + sorted(df["Customer"].dropna().astype(str).unique().tolist())

    with col1:
        selected_so = st.selectbox("Sales Order", so_options, key="mrp_filter_so")
    with col2:
        selected_item = st.selectbox("Item", item_options, key="mrp_filter_item")
    with col3:
        selected_customer = st.selectbox("Customer", cust_options, key="mrp_filter_customer")

    filtered = df.copy()
    if selected_so != "All":
        filtered = filtered[filtered["Sales Order"].astype(str) == selected_so]
    if selected_item != "All":
        filtered = filtered[filtered["Item"].astype(str) == selected_item]
    if selected_customer != "All":
        filtered = filtered[filtered["Customer"].astype(str) == selected_customer]

    st.dataframe(filtered, use_container_width=True, hide_index=True)

    csv_data = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download MRP CSV",
        data=csv_data,
        file_name="mrp_export.csv",
        mime="text/csv",
        key="mrp_download_csv_btn",
        use_container_width=True,
    )
