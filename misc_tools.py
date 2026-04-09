import io
import tarfile
from pathlib import Path
import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title='WiBotic Misc Tools', layout='wide')

PAIR_RE = re.compile(r'^(RX|TX|INF)_(\d{4})\.(CSV|TML)$', re.IGNORECASE)


def scan_tar_bytes(file_bytes: bytes):
    mapping = {}
    with tarfile.open(fileobj=io.BytesIO(file_bytes), mode='r') as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name
            m = PAIR_RE.match(name)
            if not m:
                continue
            kind, suffix, _ = m.groups()
            mapping.setdefault(suffix, {})[kind.upper()] = member.name
    return mapping


def read_csv_from_tar(tf, member_name):
    with tf.extractfile(member_name) as f:
        return pd.read_csv(f, low_memory=False)


def read_tml_lines(tf, member_name):
    with tf.extractfile(member_name) as f:
        raw = f.read()
    return raw.decode('utf-8', errors='replace').splitlines()


def prefix_columns(df, prefix):
    out = df.copy()
    out.columns = [f'{prefix}{c}' for c in out.columns]
    return out


def build_unified_dataframe(rx_df, tx_df, tml_lines=None):
    max_len = max(len(rx_df), len(tx_df), len(tml_lines or []))
    rx_df = prefix_columns(rx_df.reindex(range(max_len)), 'Rx')
    tx_df = prefix_columns(tx_df.reindex(range(max_len)), 'Tx')
    tml_series = pd.Series(tml_lines or [], name='tml_info').reindex(range(max_len))
    return pd.concat([tml_series, tx_df, rx_df], axis=1)


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
            tml_lines = read_tml_lines(tf, entry['INF']) if 'INF' in entry else None
        return build_unified_dataframe(rx_df, tx_df, tml_lines), 'tar', suffix
    raise ValueError('Please upload a CSV or TAR file.')


def safe_divide(a, b):
    a = pd.to_numeric(a, errors='coerce')
    b = pd.to_numeric(b, errors='coerce')
    out = pd.Series(np.nan, index=a.index, dtype='float64')
    valid = a.notna() & b.notna() & (b != 0)
    out.loc[valid] = a.loc[valid] / b.loc[valid]
    return out


def add_calculated_columns(df):
    if 'RxVBatt' in df.columns and 'RxIBatt' in df.columns:
        df['RxPower'] = pd.to_numeric(df['RxVBatt'], errors='coerce') * pd.to_numeric(df['RxIBatt'], errors='coerce')
    if 'TxVMonSys' in df.columns and 'TxIMonSys' in df.columns:
        df['TxInPower'] = pd.to_numeric(df['TxVMonSys'], errors='coerce') * pd.to_numeric(df['TxIMonSys'], errors='coerce')
    if 'TxVPA' in df.columns and 'TxIPA' in df.columns:
        df['TxPaPower'] = pd.to_numeric(df['TxVPA'], errors='coerce') * pd.to_numeric(df['TxIPA'], errors='coerce')
    if 'RxPower' in df.columns and 'TxPaPower' in df.columns:
        df['WirelessEfficiency'] = safe_divide(df['RxPower'], df['TxPaPower']) * 100
        df['PowerLoss'] = pd.to_numeric(df['TxPaPower'], errors='coerce') - pd.to_numeric(df['RxPower'], errors='coerce')
    if 'TxPaPower' in df.columns and 'TxInPower' in df.columns:
        df['TxDcEfficiency'] = safe_divide(df['TxPaPower'], df['TxInPower']) * 100
    if 'TxTemp' in df.columns and 'RxTemp' in df.columns:
        df['TempDelta'] = pd.to_numeric(df['TxTemp'], errors='coerce') - pd.to_numeric(df['RxTemp'], errors='coerce')


def prepare_loaded_dataframe(df):
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
    add_calculated_columns(df)
    return df


def get_plot_columns(df):
    cols = []
    for c in df.columns:
        if c == 'Time_sec':
            continue
        if pd.to_numeric(df[c], errors='coerce').notna().sum() > 0:
            cols.append(c)
    return cols


def smooth_series(series, mode, window):
    window = max(1, int(window))
    mode = mode.lower()
    if mode == 'none':
        return series
    if mode == 'moving average':
        return series.rolling(window=window, min_periods=1).mean()
    if mode == 'median':
        return series.rolling(window=window, min_periods=1).median()
    if mode == 'ema':
        return series.ewm(span=window, adjust=False).mean()
    return series


def render_plot_explorer():
    st.header('Plot Explorer')
    uploaded = st.file_uploader('Upload TAR or CSV', type=['tar', 'csv'], key='plot_upload')
    if not uploaded:
        st.info('Upload a TAR or CSV file to start.')
        return

    suffix = None
    if uploaded.name.lower().endswith('.tar'):
        mapping = scan_tar_bytes(uploaded.getvalue())
        complete = sorted([s for s, entry in mapping.items() if 'RX' in entry and 'TX' in entry])
        if len(complete) > 1:
            suffix = st.selectbox('Select TAR pair', complete)
    try:
        raw_df, _, picked = read_source_uploaded(uploaded, suffix)
        df = prepare_loaded_dataframe(raw_df)
    except Exception as e:
        st.error(str(e))
        return

    st.caption(f'Loaded rows: {len(df):,}' + (f' | TAR pair: {picked}' if picked else ''))
    columns = get_plot_columns(df)
    if not columns:
        st.warning('No numeric columns available to plot.')
        return

    left, right = st.columns([1, 3])
    with left:
        selected = st.multiselect('Signals', columns, default=columns[: min(4, len(columns))])
        smoothing = st.selectbox('Smoothing', ['None', 'Moving Average', 'Median', 'EMA'])
        window = st.number_input('Window', min_value=1, value=10, step=1)
    if not selected:
        st.warning('Select at least one signal.')
        return

    plot_df = pd.DataFrame({'Time_sec': pd.to_numeric(df['Time_sec'], errors='coerce')})
    for col in selected:
        plot_df[col] = smooth_series(pd.to_numeric(df[col], errors='coerce'), smoothing, window)

    long_df = plot_df.melt(id_vars=['Time_sec'], var_name='Signal', value_name='Value').dropna()
    fig = px.line(long_df, x='Time_sec', y='Value', color='Signal')
    fig.update_layout(height=550, xaxis_title='Time (seconds)', yaxis_title='Value')
    with right:
        st.plotly_chart(fig, use_container_width=True)

    with st.expander('Preview data'):
        st.dataframe(df.head(200), use_container_width=True)


def render_derate_summary():
    st.header('Derate Summary')
    uploaded = st.file_uploader('Upload TAR or CSV', type=['tar', 'csv'], key='derate_upload')
    if not uploaded:
        st.info('Upload a TAR or CSV file to build a simple derate view.')
        return
    try:
        raw_df, _, _ = read_source_uploaded(uploaded)
        df = prepare_loaded_dataframe(raw_df)
    except Exception as e:
        st.error(str(e))
        return

    power_candidates = [c for c in ['RxPower', 'TxPaPower', 'TxInPower'] if c in df.columns]
    temp_candidates = [c for c in ['TxTemp', 'RxTemp', 'TempDelta'] if c in df.columns]
    if not power_candidates or not temp_candidates:
        st.warning('Need at least one power column and one temperature column.')
        st.dataframe(df.head(50), use_container_width=True)
        return

    c1, c2 = st.columns(2)
    power_col = c1.selectbox('Power column', power_candidates)
    temp_col = c2.selectbox('Temperature column', temp_candidates)
    work = df[[power_col, temp_col]].copy()
    work[power_col] = pd.to_numeric(work[power_col], errors='coerce')
    work[temp_col] = pd.to_numeric(work[temp_col], errors='coerce')
    work = work.dropna()
    if work.empty:
        st.warning('No valid rows after cleaning.')
        return
    work['TempBin'] = (np.round(work[temp_col] / 2) * 2).round(1)
    curve = work.groupby('TempBin')[power_col].mean().reset_index()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=curve['TempBin'], y=curve[power_col], mode='lines+markers', name=power_col))
    fig.update_layout(height=500, xaxis_title='Temperature bin', yaxis_title=f'{power_col} mean')
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(curve, use_container_width=True)


def render_rf_calc():
    st.header('RF Resonance Calculator')
    c1, c2, c3 = st.columns(3)
    freq_mhz = c1.number_input('Frequency (MHz)', min_value=0.0, value=6.78, step=0.01)
    inductance_uh = c2.number_input('Inductance (uH)', min_value=0.0, value=10.0, step=0.1)
    capacitance_pf = c3.number_input('Capacitance (pF)', min_value=0.0, value=55144.0, step=10.0)

    freq_hz = freq_mhz * 1e6
    L = inductance_uh * 1e-6
    C = capacitance_pf * 1e-12

    c4, c5 = st.columns(2)
    if L > 0 and C > 0:
        calc_freq = 1 / (2 * np.pi * np.sqrt(L * C))
        c4.metric('Calculated Frequency', f'{calc_freq / 1e6:.6f} MHz')
    if freq_hz > 0 and C > 0:
        calc_L = 1 / (((2 * np.pi * freq_hz) ** 2) * C)
        c5.metric('Required Inductance', f'{calc_L * 1e6:.6f} uH')
    if freq_hz > 0 and L > 0:
        calc_C = 1 / (((2 * np.pi * freq_hz) ** 2) * L)
        st.metric('Required Capacitance', f'{calc_C * 1e12:.2f} pF')


st.title('WiBotic Misc Tools')
st.caption('Lightweight tools only. No SOS, no Google Sheets, no production board.')

page = st.radio('Tool', ['Plot Explorer', 'Derate Summary', 'RF Resonance Calculator'], horizontal=True)
if page == 'Plot Explorer':
    render_plot_explorer()
elif page == 'Derate Summary':
    render_derate_summary()
else:
    render_rf_calc()
