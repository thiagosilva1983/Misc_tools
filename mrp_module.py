# mrp_module.py
# REV F

import streamlit as st
import pandas as pd


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def get_open_sales_orders(client):
    candidate_methods = [
        "get_open_sales_orders",
        "fetch_open_sales_orders",
        "list_open_sales_orders",
        "get_sales_orders",
        "fetch_sales_orders",
    ]

    last_error = None

    for method_name in candidate_methods:
        method = getattr(client, method_name, None)
        if callable(method):
            try:
                result = method()
                if result is None:
                    return []
                if isinstance(result, list):
                    return result
                if isinstance(result, dict):
                    for key in ["data", "items", "salesorders", "sales_orders", "results"]:
                        if isinstance(result.get(key), list):
                            return result.get(key)
                return []
            except Exception as e:
                last_error = e

    if last_error:
        raise last_error

    raise RuntimeError(
        "Could not find a compatible method in SOSReadonlyClient to fetch sales orders."
    )


def get_bill_of_materials_for_product(client, item_id=None, sku=None, name=None):
    candidate_methods = [
        "get_product_bom",
        "fetch_product_bom",
        "get_bom",
        "fetch_bom",
        "get_bill_of_materials",
    ]

    last_error = None

    for method_name in candidate_methods:
        method = getattr(client, method_name, None)
        if callable(method):
            try:
                if item_id:
                    result = method(item_id=item_id)
                elif sku:
                    result = method(sku=sku)
                elif name:
                    result = method(name=name)
                else:
                    return []

                if result is None:
                    return []
                if isinstance(result, list):
                    return result
                if isinstance(result, dict):
                    for key in ["data", "items", "bom", "components", "results"]:
                        if isinstance(result.get(key), list):
                            return result.get(key)
                return []
            except Exception as e:
                last_error = e

    if last_error:
        return []

    return []


def get_item_stock(client, item_id=None, sku=None, name=None):
    candidate_methods = [
        "get_item_stock",
        "fetch_item_stock",
        "get_inventory_item",
        "fetch_inventory_item",
        "get_item_by_sku",
        "fetch_item_by_sku",
    ]

    last_error = None

    for method_name in candidate_methods:
        method = getattr(client, method_name, None)
        if callable(method):
            try:
                if item_id:
                    result = method(item_id=item_id)
                elif sku:
                    result = method(sku=sku)
                elif name:
                    result = method(name=name)
                else:
                    return {}

                if result is None:
                    return {}
                if isinstance(result, dict):
                    return result
                if isinstance(result, list) and result:
                    return result[0]
                return {}
            except Exception as e:
                last_error = e

    if last_error:
        return {}

    return {}


def normalize_sales_order_rows(orders):
    rows = []

    for so in orders or []:
        so_number = (
            so.get("SalesOrderNumber")
            or so.get("salesordernumber")
            or so.get("Sales Order Number")
            or so.get("DocumentNumber")
            or so.get("document_number")
            or so.get("SONumber")
            or so.get("Number")
            or ""
        )

        customer = (
            so.get("CustomerName")
            or so.get("customername")
            or so.get("Customer")
            or so.get("customer")
            or ""
        )

        line_items = (
            so.get("ItemList")
            or so.get("itemlist")
            or so.get("LineItems")
            or so.get("lineitems")
            or so.get("Items")
            or so.get("items")
            or []
        )

        for line in line_items:
            sku = (
                line.get("SKU")
                or line.get("sku")
                or line.get("ItemCode")
                or line.get("itemcode")
                or line.get("PartNumber")
                or line.get("partnumber")
                or ""
            )

            description = (
                line.get("Description")
                or line.get("description")
                or line.get("Name")
                or line.get("name")
                or ""
            )

            qty = (
                line.get("Quantity")
                or line.get("quantity")
                or line.get("Qty")
                or line.get("qty")
                or 0
            )

            item_id = (
                line.get("ItemID")
                or line.get("itemid")
                or line.get("ID")
                or line.get("id")
            )

            rows.append(
                {
                    "sales_order": so_number,
                    "customer": customer,
                    "parent_item_id": item_id,
                    "parent_sku": sku,
                    "parent_description": description,
                    "required_build_qty": _safe_float(qty, 0),
                }
            )

    return rows


def build_mrp_table(client):
    orders = get_open_sales_orders(client)
    so_rows = normalize_sales_order_rows(orders)

    mrp_rows = []

    for row in so_rows:
        parent_item_id = row["parent_item_id"]
        parent_sku = row["parent_sku"]
        parent_description = row["parent_description"]
        so_number = row["sales_order"]
        customer = row["customer"]
        build_qty = _safe_float(row["required_build_qty"], 0)

        bom_lines = get_bill_of_materials_for_product(
            client,
            item_id=parent_item_id,
            sku=parent_sku,
            name=parent_description,
        )

        if not bom_lines:
            mrp_rows.append(
                {
                    "Sales Order": so_number,
                    "Customer": customer,
                    "Parent SKU": parent_sku,
                    "Parent Description": parent_description,
                    "Component SKU": "",
                    "Component Description": "No BOM found",
                    "Qty per Parent": "",
                    "Required Qty": "",
                    "On Hand": "",
                    "Available": "",
                    "Shortage": "",
                    "Status": "NO BOM",
                }
            )
            continue

        for comp in bom_lines:
            comp_sku = (
                comp.get("SKU")
                or comp.get("sku")
                or comp.get("ItemCode")
                or comp.get("itemcode")
                or comp.get("PartNumber")
                or comp.get("partnumber")
                or ""
            )

            comp_desc = (
                comp.get("Description")
                or comp.get("description")
                or comp.get("Name")
                or comp.get("name")
                or ""
            )

            comp_item_id = (
                comp.get("ItemID")
                or comp.get("itemid")
                or comp.get("ID")
                or comp.get("id")
            )

            qty_per = (
                comp.get("Quantity")
                or comp.get("quantity")
                or comp.get("Qty")
                or comp.get("qty")
                or 0
            )
            qty_per = _safe_float(qty_per, 0)
            required_qty = build_qty * qty_per

            stock = get_item_stock(
                client,
                item_id=comp_item_id,
                sku=comp_sku,
                name=comp_desc,
            )

            on_hand = _safe_float(
                stock.get("OnHand")
                or stock.get("onhand")
                or stock.get("QuantityOnHand")
                or stock.get("quantityonhand")
                or stock.get("AvailableQuantity")
                or stock.get("availablequantity")
                or 0,
                0,
            )

            available = _safe_float(
                stock.get("Available")
                or stock.get("available")
                or stock.get("AvailableForSale")
                or stock.get("availableforsale")
                or on_hand,
                on_hand,
            )

            shortage = max(required_qty - available, 0)
            status = "OK" if shortage <= 0 else "SHORT"

            mrp_rows.append(
                {
                    "Sales Order": so_number,
                    "Customer": customer,
                    "Parent SKU": parent_sku,
                    "Parent Description": parent_description,
                    "Component SKU": comp_sku,
                    "Component Description": comp_desc,
                    "Qty per Parent": qty_per,
                    "Required Qty": required_qty,
                    "On Hand": on_hand,
                    "Available": available,
                    "Shortage": shortage,
                    "Status": status,
                }
            )

    df = pd.DataFrame(mrp_rows)

    if not df.empty:
        preferred_cols = [
            "Sales Order",
            "Customer",
            "Parent SKU",
            "Parent Description",
            "Component SKU",
            "Component Description",
            "Qty per Parent",
            "Required Qty",
            "On Hand",
            "Available",
            "Shortage",
            "Status",
        ]
        df = df[[c for c in preferred_cols if c in df.columns]]

    return df


def render_mrp_tab(client):
    st.subheader("MRP - Materials Planning")
    st.caption("Runs against live SOS data using the same authenticated SOS client as Weekly Production.")

    c1, c2 = st.columns(2)

    run_clicked = c1.button("Run MRP", use_container_width=True, key="mrp_run_btn")
    refresh_clicked = c2.button("Refresh MRP Data", use_container_width=True, key="mrp_refresh_btn")

    if run_clicked or refresh_clicked:
        with st.spinner("Running MRP from SOS..."):
            try:
                st.session_state["mrp_df"] = build_mrp_table(client)
                st.success("MRP loaded successfully.")
            except Exception as e:
                st.error(f"MRP failed: {e}")

    df = st.session_state.get("mrp_df", pd.DataFrame())

    if df is None or df.empty:
        st.info("No MRP data loaded yet. Click Run MRP.")
        return

    if "Status" in df.columns:
        x1, x2, x3 = st.columns(3)
        x1.metric("Rows", len(df))
        x2.metric("Shortages", int((df["Status"] == "SHORT").sum()))
        x3.metric("No BOM", int((df["Status"] == "NO BOM").sum()))

    st.dataframe(df, use_container_width=True, height=520)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download MRP CSV",
        data=csv_data,
        file_name="mrp_output.csv",
        mime="text/csv",
        key="mrp_download_csv",
    )
