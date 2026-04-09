import io
import math
import tarfile
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title='WiBotic Misc Tools', layout='wide')

PAIR_RE = __import__('re').compile(r'^(RX|TX|INF)_(\d{4})\.(CSV|TML)$', __import__('re').IGNORECASE)


def scan_tar_bytes(file_bytes: bytes):
    mapping = {}
    with tarfile.open(fileobj=io.BytesIO(file_bytes), mode='r') as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        for member in members:
            name = Path(member.name).name
            match = PAIR_RE.match(name)
            if not match:
                continue
            kind, suffix, _ext = match.groups()
            mapping.setdefault(suffix, {})[kind.upper()] = member.name
    return mapping


def read_csv_from_tar(tf, member_name):
    with tf.extractfile(member_name) as f:
        return pd.read_csv(f, low_memory=False)


def prefix_columns(df, prefix):
    out = df.copy()
    out.columns = [f'{prefix}{c}' for c in out.columns]
    return out


def build_unified_dataframe(rx_df, tx_df):
    max_len = max(len(rx_df), len(tx_df))
    rx_df = prefix_columns(rx_df.reindex(range(max_len)), 'Rx')
    tx_df = prefix_columns(tx_df.reindex(range(max_len)), 'Tx')
    return pd.concat([tx_df, rx_df], axis=1)


def read_source_uploaded(uploaded_file, selected_suffix=None):
    name = uploaded_file.name.lower()
    if name.endswith('.csv'):
        return pd.read_csv(uploaded_file, low_memory=False), 'csv', ''
    if name.endswith('.tar'):
        file_bytes = uploaded_file.getvalue()
        mapping = scan_tar_bytes(file_bytes)
        complete = sorted([s for s, entry in mapping.items() if 'RX' in entry and 'TX' in entry])
        if not complete:
            raise ValueError('No complete RX/TX pairs found in this TAR file.')
        suffix = selected_suffix or complete[0]
        entry = mapping[suffix]
        with tarfile.open(fileobj=io.BytesIO(file_bytes), mode='r') as tf:
            rx_df = read_csv_from_tar(tf, entry['RX'])
            tx_df = read_csv_from_tar(tf, entry['TX'])
        return build_unified_dataframe(rx_df, tx_df), 'tar', suffix
    raise ValueError('Please upload a CSV or TAR file.')


def add_calculated_columns(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    if 'RxVBatt' in data.columns and 'RxIBatt' in data.columns:
        data['RxPower'] = pd.to_numeric(data['RxVBatt'], errors='coerce') * pd.to_numeric(data['RxIBatt'], errors='coerce')
    if 'TxVPA' in data.columns and 'TxIPA' in data.columns:
        data['TxPaPower'] = pd.to_numeric(data['TxVPA'], errors='coerce') * pd.to_numeric(data['TxIPA'], errors='coerce')
    if 'RxPower' in data.columns and 'TxPaPower' in data.columns:
        tx_pa = pd.to_numeric(data['TxPaPower'], errors='coerce')
        rx_p = pd.to_numeric(data['RxPower'], errors='coerce')
        valid = tx_pa.notna() & (tx_pa != 0) & rx_p.notna()
        data['WirelessEfficiency'] = np.where(valid, (rx_p / tx_pa) * 100.0, np.nan)
    return data


def prepare_loaded_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if 'TxRTC' in df.columns:
        df['TxRTC'] = pd.to_numeric(df['TxRTC'], errors='coerce')
        df = df.dropna(subset=['TxRTC']).reset_index(drop=True)
        if df.empty:
            raise ValueError("'TxRTC' column could not be converted to numeric values.")
        df['Time_sec'] = df['TxRTC'] - df['TxRTC'].iloc[0]
    elif 'Timestamp' in df.columns:
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce')
        df = df.dropna(subset=['Timestamp']).reset_index(drop=True)
        if df.empty:
            raise ValueError("'Timestamp' column could not be converted to valid datetimes.")
        df['Time_sec'] = (df['Timestamp'] - df['Timestamp'].iloc[0]).dt.total_seconds()
    else:
        df['Time_sec'] = np.arange(len(df), dtype=float)
    return add_calculated_columns(df)


def rf_resonance(freq_mhz=None, inductance_uh=None, capacitance_pf=None):
    f = None if freq_mhz is None else float(freq_mhz) * 1e6
    l = None if inductance_uh is None else float(inductance_uh) * 1e-6
    c = None if capacitance_pf is None else float(capacitance_pf) * 1e-12
    provided = sum(v is not None and v > 0 for v in (f, l, c))
    if provided < 2:
        return None
    if f is None:
        f = 1.0 / (2.0 * math.pi * math.sqrt(l * c))
    elif l is None:
        l = 1.0 / (((2.0 * math.pi * f) ** 2) * c)
    elif c is None:
        c = 1.0 / (((2.0 * math.pi * f) ** 2) * l)
    return f / 1e6, l * 1e6, c * 1e12


st.title('WiBotic Misc Tools')
mode = st.sidebar.radio('Tool', ['Plot Explorer', 'Derate Summary', 'RF Resonance Calculator'])

if mode == 'RF Resonance Calculator':
    st.subheader('RF Resonance Calculator')
    c1, c2, c3 = st.columns(3)
    with c1:
        freq_mhz = st.number_input('Frequency (MHz)', min_value=0.0, value=6.78, step=0.01)
    with c2:
        inductance_uh = st.number_input('Inductance (uH)', min_value=0.0, value=0.0, step=0.01)
    with c3:
        capacitance_pf = st.number_input('Capacitance (pF)', min_value=0.0, value=0.0, step=1.0)
    result = rf_resonance(
        None if freq_mhz == 0 else freq_mhz,
        None if inductance_uh == 0 else inductance_uh,
        None if capacitance_pf == 0 else capacitance_pf,
    )
    if result is None:
        st.info('Fill any two values to calculate the third.')
    else:
        f_mhz, l_uh, c_pf = result
        a, b, c = st.columns(3)
        a.metric('Frequency', f'{f_mhz:.6f} MHz')
        b.metric('Inductance', f'{l_uh:.6f} uH')
        c.metric('Capacitance', f'{c_pf:.3f} pF')

else:
    uploaded_file = st.file_uploader('Upload CSV or TAR', type=['csv', 'tar'])
    if uploaded_file is not None:
        selected_suffix = None
        if uploaded_file.name.lower().endswith('.tar'):
            mapping = scan_tar_bytes(uploaded_file.getvalue())
            complete = sorted([s for s, entry in mapping.items() if 'RX' in entry and 'TX' in entry])
            if complete:
                selected_suffix = st.selectbox('Select TAR pair', complete)
        try:
            raw_df, source_type, suffix = read_source_uploaded(uploaded_file, selected_suffix)
            df = prepare_loaded_dataframe(raw_df)
            st.success(f'Loaded {len(df):,} rows from {source_type.upper()}{(" pair " + suffix) if suffix else ""}.')
        except Exception as e:
            st.error(str(e))
            st.stop()

        if mode == 'Plot Explorer':
            st.subheader('Plot Explorer')
            numeric_cols = [c for c in df.columns if c != 'Time_sec' and pd.to_numeric(df[c], errors='coerce').notna().sum() > 0]
            selected = st.multiselect('Signals', numeric_cols, default=numeric_cols[:3])
            time_mode = st.selectbox('Time axis', ['Seconds', 'Minutes', 'Hours', 'Sample index'])
            x = pd.to_numeric(df['Time_sec'], errors='coerce')
            if time_mode == 'Minutes':
                x_plot = x / 60.0
            elif time_mode == 'Hours':
                x_plot = x / 3600.0
            elif time_mode == 'Sample index':
                x_plot = np.arange(len(df))
            else:
                x_plot = x
            chart_df = pd.DataFrame({'x': x_plot})
            for col in selected:
                chart_df[col] = pd.to_numeric(df[col], errors='coerce')
            st.line_chart(chart_df.set_index('x'))
            st.dataframe(df[selected + ['Time_sec']].head(500), use_container_width=True)

        elif mode == 'Derate Summary':
            st.subheader('Derate Summary')
            power_candidates = [c for c in ['RxPower', 'TxPaPower'] if c in df.columns]
            if not power_candidates:
                st.warning('No calculated power columns were found. Need RxVBatt/RxIBatt or TxVPA/TxIPA.')
            else:
                power_col = st.selectbox('Power signal', power_candidates)
                valid = df[['Time_sec', power_col]].copy()
                valid['Time_sec'] = pd.to_numeric(valid['Time_sec'], errors='coerce')
                valid[power_col] = pd.to_numeric(valid[power_col], errors='coerce')
                valid = valid.dropna()
                if valid.empty:
                    st.warning('No valid rows for the selected signal.')
                else:
                    avg_power = float(valid[power_col].mean())
                    min_power = float(valid[power_col].min())
                    max_power = float(valid[power_col].max())
                    samples = int(len(valid))
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric('Average Power', f'{avg_power:.2f} W')
                    c2.metric('Min Power', f'{min_power:.2f} W')
                    c3.metric('Max Power', f'{max_power:.2f} W')
                    c4.metric('Samples', f'{samples}')
                    plot_df = pd.DataFrame({'x': valid['Time_sec'] / 60.0, power_col: valid[power_col]})
                    st.line_chart(plot_df.set_index('x'))
                    st.dataframe(valid.head(500), use_container_width=True)
    else:
        st.info('Upload a CSV or TAR file to use this tool.')
