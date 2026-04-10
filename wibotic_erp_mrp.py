# mrp_module_version_C.py
# VERSION C

import pandas as pd
import streamlit as st
from collections import defaultdict


def get_sos_client():
    from misc_tools import get_sos_access_token, SOSReadonlyClient
    token = get_sos_access_token()
    return SOSReadonlyClient(token)


def get_open_sales_orders():
    client = get_sos_client()
    data = client.get("salesorders")

    orders = []
    for so in data.get("data", []):
        status = str(so.get("status") or "").strip()
        if status in ["Closed", "Voided"]:
            continue

        orders.append({
            "so_number": so.get("number"),
            "items": so.get("details", []),
        })

    return orders


def get_bom(item_id):
    client = get_sos_client()
    try:
        bom = client.get(f"items/{item_id}/bom")
        return bom.get("components", []) or [{"item_id": item_id, "qty": 1}]
    except Exception:
        return [{"item_id": item_id, "qty": 1}]


def get_inventory():
    client = get_sos_client()
    data = client.get("items")

    inventory = {}
    for item in data.get("data", []):
        inventory[item.get("id")] = {
            "name": item.get("name") or item.get("sku") or item.get("id"),
            "on_hand": float(item.get("quantityOnHand") or 0),
        }
    return inventory


def calculate_demand(orders):
    demand = defaultdict(float)

    for order in orders:
        for line in order.get("items", []):
            item_id = line.get("itemId")
            qty = float(line.get("quantity") or 0)

            if not item_id or qty <= 0:
                continue

            bom = get_bom(item_id)
            for comp in bom:
                comp_id = comp.get("item_id")
                comp_qty = float(comp.get("qty") or 0)
                if comp_id and comp_qty > 0:
                    demand[comp_id] += qty * comp_qty

    return demand


def build_mrp_table():
    with st.spinner("Loading live SOS MRP data..."):
        orders = get_open_sales_orders()
        inventory = get_inventory()
        demand = calculate_demand(orders)

        rows = []
        for item_id, required in demand.items():
            inv = inventory.get(item_id, {})
            on_hand = float(inv.get("on_hand") or 0)
            name = inv.get("name") or item_id
            shortage = max(required - on_hand, 0)

            rows.append({
                "Item ID": item_id,
                "Item": name,
                "Required": round(required, 2),
                "On Hand": round(on_hand, 2),
                "Shortage": round(shortage, 2),
                "Status": "OK" if shortage <= 0 else "SHORT",
            })

        if not rows:
            return pd.DataFrame(columns=["Item ID", "Item", "Required", "On Hand", "Shortage", "Status"])

        df = pd.DataFrame(rows)
        return df.sort_values(by=["Shortage", "Required"], ascending=[False, False]).reset_index(drop=True)


def render_mrp_tab():
    st.title("MRP - Material Requirements Planning")

    a, b = st.columns([1, 1])
    with a:
        if st.button("Run Live MRP", use_container_width=True, key="mrp_refresh_live_vc"):
            st.session_state["mrp_df"] = build_mrp_table()

    with b:
        if st.button("Clear MRP", use_container_width=True, key="mrp_clear_live_vc"):
            st.session_state.pop("mrp_df", None)
            st.rerun()

    df = st.session_state.get("mrp_df")

    if df is None:
        st.info("Click Run Live MRP to load current SOS demand versus inventory.")
        return

    st.dataframe(df, use_container_width=True, hide_index=True)

    st.download_button(
        "Download MRP CSV",
        data=df.to_csv(index=False),
        file_name="mrp_report_version_C.csv",
        mime="text/csv",
        use_container_width=True,
        key="mrp_download_csv_vc",
    )
