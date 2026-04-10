# rev B - MRP LIVE (SOS Integrated)

import pandas as pd
import streamlit as st
from collections import defaultdict

# -------------------------------
# 🔌 IMPORT YOUR EXISTING SOS CLIENT
# -------------------------------
# -------------------------------
# 🔐 CONNECT TO SOS
# -------------------------------
def get_sos_client():
    from misc_tools import get_sos_access_token, SOSReadonlyClient
    token = get_sos_access_token()
    return SOSReadonlyClient(token)


# -------------------------------
# 📦 FETCH OPEN SALES ORDERS (LIVE)
# -------------------------------
def get_open_sales_orders():
    client = get_sos_client()

    # Adjust endpoint if needed
    data = client.get("salesorders")

    orders = []
    for so in data.get("data", []):
        if so.get("status") not in ["Closed", "Voided"]:
            orders.append({
                "so_number": so.get("number"),
                "items": so.get("details", [])
            })

    return orders


# -------------------------------
# 🧩 GET BOM (SIMPLE VERSION)
# -------------------------------
def get_bom(item_id):
    client = get_sos_client()

    # If your SOS has BOM endpoint:
    # otherwise fallback = treat item as itself
    try:
        bom = client.get(f"items/{item_id}/bom")
        return bom.get("components", [])
    except:
        return [{
            "item_id": item_id,
            "qty": 1
        }]


# -------------------------------
# 📊 GET INVENTORY (LIVE)
# -------------------------------
def get_inventory():
    client = get_sos_client()

    data = client.get("items")

    inventory = {}

    for item in data.get("data", []):
        inventory[item.get("id")] = {
            "name": item.get("name"),
            "on_hand": item.get("quantityOnHand", 0)
        }

    return inventory


# -------------------------------
# 📈 CALCULATE DEMAND
# -------------------------------
def calculate_demand(orders):
    demand = defaultdict(float)

    for order in orders:
        for line in order["items"]:
            item_id = line.get("itemId")
            qty = line.get("quantity", 0)

            bom = get_bom(item_id)

            for comp in bom:
                demand[comp["item_id"]] += qty * comp["qty"]

    return demand


# -------------------------------
# 🧠 BUILD MRP TABLE
# -------------------------------
def build_mrp_table():
    with st.spinner("Loading SOS data..."):

        orders = get_open_sales_orders()
        inventory = get_inventory()
        demand = calculate_demand(orders)

        rows = []

        for item_id, required in demand.items():
            inv = inventory.get(item_id, {})

            on_hand = inv.get("on_hand", 0)
            name = inv.get("name", item_id)

            shortage = max(required - on_hand, 0)

            status = "OK" if shortage == 0 else "SHORT"

            rows.append({
                "Item": name,
                "Required": round(required, 2),
                "On Hand": round(on_hand, 2),
                "Shortage": round(shortage, 2),
                "Status": status
            })

        df = pd.DataFrame(rows)
        return df.sort_values(by="Shortage", ascending=False)


# -------------------------------
# 🎯 MAIN UI
# -------------------------------
def render_mrp_tab():
    st.title("📦 MRP - Material Requirements Planning (LIVE)")

    if st.button("🔄 Refresh MRP"):
        st.session_state["mrp_df"] = build_mrp_table()

    df = st.session_state.get("mrp_df")

    if df is not None:
        st.dataframe(df, use_container_width=True)

        st.download_button(
            "📥 Download CSV",
            df.to_csv(index=False),
            file_name="mrp_report.csv"
        )
    else:
        st.info("Click refresh to load MRP data.")
