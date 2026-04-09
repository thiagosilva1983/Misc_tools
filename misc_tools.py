import io
import re
import tarfile
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title='WiBotic Tool O', layout='wide')

PAIR_RE = re.compile(r'^(RX|TX|INF)_(\d{4})\.(CSV|TML)$', re.IGNORECASE)


# -----------------------------
# Shared helpers
# -----------------------------
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
            entry = mapping.setdefault(suffix, {})
            entry[kind.upper()] = member.name
    return mapping


def read_csv_from_tar(tf, member_name):
    with tf.extractfile(member_name) as f:
        return pd.read_csv(f, low_memory=False)


def read_tml_lines(tf, member_name):
    with tf.extractfile(member_name) as f:
        raw = f.read()
    return raw.decode('utf-8', errors='replace').splitlines()


def prefix_columns(df, prefix):
    df = df.copy()
    df.columns = [f'{prefix}{c}' for c in df.columns]
    return df


def build_unified_dataframe(rx_df, tx_df, tml_lines=None):
    max_len = max(len(rx_df), len(tx_df), len(tml_lines or []))
    rx_df = rx_df.reindex(range(max_len))
    tx_df = tx_df.reindex(range(max_len))
    rx_df = prefix_columns(rx_df, 'Rx')
    tx_df = prefix_columns(tx_df, 'Tx')
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
        if suffix not in mapping:
            raise ValueError(f'Suffix {suffix} was not found in this TAR file.')
        entry = mapping[suffix]
        with tarfile.open(fileobj=io.BytesIO(file_bytes), mode='r') as tf:
            rx_df = read_csv_from_tar(tf, entry['RX'])
            tx_df = read_csv_from_tar(tf, entry['TX'])
            tml_lines = read_tml_lines(tf, entry['INF']) if 'INF' in entry else None
        return build_unified_dataframe(rx_df, tx_df, tml_lines), 'tar', suffix
    raise ValueError('Please upload a CSV or TAR file.')


def safe_divide(numerator, denominator):
    numerator = pd.to_numeric(numerator, errors='coerce')
    denominator = pd.to_numeric(denominator, errors='coerce')
    result = pd.Series(np.nan, index=numerator.index, dtype='float64')
    valid = denominator.notna() & (denominator != 0) & numerator.notna()
    result.loc[valid] = numerator.loc[valid] / denominator.loc[valid]
    return result


def add_calculated_columns(data):
    if 'RxVBatt' in data.columns and 'RxIBatt' in data.columns:
        data['RxPower'] = pd.to_numeric(data['RxVBatt'], errors='coerce') * pd.to_numeric(data['RxIBatt'], errors='coerce')
    if 'TxVMonSys' in data.columns and 'TxIMonSys' in data.columns:
        data['TxInPower'] = pd.to_numeric(data['TxVMonSys'], errors='coerce') * pd.to_numeric(data['TxIMonSys'], errors='coerce')
    if 'TxVPA' in data.columns and 'TxIPA' in data.columns:
        data['TxPaPower'] = pd.to_numeric(data['TxVPA'], errors='coerce') * pd.to_numeric(data['TxIPA'], errors='coerce')
    if 'RxPower' in data.columns and 'TxPaPower' in data.columns:
        tx_pa = pd.to_numeric(data['TxPaPower'], errors='coerce')
        rx_p = pd.to_numeric(data['RxPower'], errors='coerce')
        data['WirelessEfficiency'] = safe_divide(rx_p, tx_pa) * 100
        data['PowerLoss'] = tx_pa - rx_p
    if 'TxPaPower' in data.columns and 'TxInPower' in data.columns:
        data['TxDcEfficiency'] = safe_divide(pd.to_numeric(data['TxPaPower'], errors='coerce'), pd.to_numeric(data['TxInPower'], errors='coerce')) * 100
    if 'TxTemp' in data.columns and 'RxTemp' in data.columns:
        data['TempDelta'] = pd.to_numeric(data['TxTemp'], errors='coerce') - pd.to_numeric(data['RxTemp'], errors='coerce')


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


def preprocess_external_csv(uploaded_file, require_value=False):
    df = pd.read_csv(uploaded_file)
    if 'time' not in df.columns:
        raise ValueError("CSV must contain a 'time' column.")
    if require_value and 'value' not in df.columns:
        raise ValueError("CSV must contain 'time' and 'value' columns.")
    df['time_utc'] = pd.to_datetime(df['time'], utc=True, errors='coerce')
    df['time_pacific'] = df['time_utc'].dt.tz_convert('America/Los_Angeles')
    if 'value' in df.columns:
        df['value_num'] = pd.to_numeric(df['value'], errors='coerce')
    df = df.dropna(subset=['time_pacific']).sort_values('time_pacific').reset_index(drop=True)
    return df


def parse_pacific_datetime(text: str) -> Optional[pd.Timestamp]:
    text = (text or '').strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return pd.to_datetime(text, format=fmt).tz_localize('America/Los_Angeles')
        except Exception:
            pass
    ts = pd.to_datetime(text, errors='raise')
    if ts.tzinfo is None:
        return ts.tz_localize('America/Los_Angeles')
    return ts.tz_convert('America/Los_Angeles')


def align_external_to_main_data(main_df, ext_df, value_col='value_num', manual_start_text=''):
    if 'Timestamp' in main_df.columns and pd.to_datetime(main_df['Timestamp'], errors='coerce').notna().sum() > 0:
        left = main_df.copy()
        left['main_time_abs'] = pd.to_datetime(left['Timestamp'], errors='coerce')
        if getattr(left['main_time_abs'].dt, 'tz', None) is None:
            left['main_time_abs'] = left['main_time_abs'].dt.tz_localize('America/Los_Angeles', nonexistent='shift_forward', ambiguous='NaT')
        else:
            left['main_time_abs'] = left['main_time_abs'].dt.tz_convert('America/Los_Angeles')
        merged = pd.merge_asof(
            left.sort_values('main_time_abs'),
            ext_df[['time_pacific', value_col]].sort_values('time_pacific'),
            left_on='main_time_abs',
            right_on='time_pacific',
            direction='nearest',
            tolerance=pd.Timedelta(seconds=15)
        )
        out = merged.sort_index()
        return out[value_col]

    start_ts = parse_pacific_datetime(manual_start_text)
    if start_ts is None:
        raise ValueError('Enter a manual start time when main data has no Timestamp column.')
    rel = (ext_df['time_pacific'] - start_ts).dt.total_seconds()
    ext_df = ext_df.assign(rel_sec=rel)
    ext_df = ext_df[ext_df['rel_sec'].notna()]
    tmax = pd.to_numeric(main_df['Time_sec'], errors='coerce').max()
    ext_df = ext_df[(ext_df['rel_sec'] >= 0) & (ext_df['rel_sec'] <= tmax)]
    if ext_df.empty:
        return pd.Series(np.nan, index=main_df.index)
    x = ext_df['rel_sec'].to_numpy(dtype=float)
    y = pd.to_numeric(ext_df[value_col], errors='coerce').to_numpy(dtype=float)
    target_x = pd.to_numeric(main_df['Time_sec'], errors='coerce').to_numpy(dtype=float)
    interp = np.interp(target_x, x, y, left=np.nan, right=np.nan)
    return pd.Series(interp, index=main_df.index)


def make_line_chart(df, x_col, y_cols, height=360):
    chart_df = df[[x_col] + y_cols].dropna(how='all').copy()
    if chart_df.empty:
        st.info('No rows to chart.')
        return
    chart_df = chart_df.set_index(x_col)
    st.line_chart(chart_df, height=height)


# -----------------------------
# Tabs
# -----------------------------
def tab_home():
    st.subheader('Tool O Home')
    st.markdown(
        """
        This version keeps the misc engineering tools together in one app.

        Included:
        - RF Calculator
        - Capacitance Bank
        - Simple Plot Explorer
        - Derate Summary
        - Arduino Sync

        Removed:
        - Weekly Production
        - SOS Inventory
        - Label app
        - Google Sheets
        """
    )


def tab_rf_calculator():
    st.subheader('RF Resonance Calculator')
    c1, c2, c3 = st.columns(3)
    with c1:
        freq_mhz = st.number_input('Frequency (MHz)', min_value=0.0, value=6.78, step=0.01, format='%.6f')
    with c2:
        inductance_uh = st.number_input('Inductance (uH)', min_value=0.0, value=10.0, step=0.1, format='%.6f')
    with c3:
        capacitance_pf = st.number_input('Capacitance (pF)', min_value=0.0, value=55100.0, step=10.0, format='%.6f')

    c = capacitance_pf * 1e-12
    l = inductance_uh * 1e-6
    f = freq_mhz * 1e6

    calc_cols = st.columns(3)
    if l > 0 and c > 0:
        implied_f = 1.0 / (2 * np.pi * np.sqrt(l * c))
        calc_cols[0].metric('Implied Frequency', f'{implied_f/1e6:,.6f} MHz')
    if f > 0 and c > 0:
        implied_l = 1.0 / (((2 * np.pi * f) ** 2) * c)
        calc_cols[1].metric('Implied Inductance', f'{implied_l*1e6:,.6f} uH')
    if f > 0 and l > 0:
        implied_c = 1.0 / (((2 * np.pi * f) ** 2) * l)
        calc_cols[2].metric('Implied Capacitance', f'{implied_c*1e12:,.2f} pF')


def tab_cap_bank():
    st.subheader('Capacitance Bank Calculator')
    st.caption('Each bank is in parallel. Bank A and Bank B are then put in series.')
    unit = st.selectbox('Input unit', ['pF', 'uF'], index=0)
    factor = 1.0 if unit == 'pF' else 1e6

    left, right = st.columns(2)
    with left:
        st.markdown('**Bank A**')
        a_vals = [st.number_input(f'A{i+1}', min_value=0.0, value=0.0, key=f'a{i}') for i in range(5)]
    with right:
        st.markdown('**Bank B**')
        b_vals = [st.number_input(f'B{i+1}', min_value=0.0, value=0.0, key=f'b{i}') for i in range(5)]

    a_total_pf = sum(a_vals) * factor
    b_total_pf = sum(b_vals) * factor
    series_total_pf = 0.0
    if a_total_pf > 0 and b_total_pf > 0:
        series_total_pf = (a_total_pf * b_total_pf) / (a_total_pf + b_total_pf)

    m1, m2, m3 = st.columns(3)
    m1.metric('Bank A total', f'{a_total_pf:,.2f} pF')
    m2.metric('Bank B total', f'{b_total_pf:,.2f} pF')
    m3.metric('Banks in series', f'{series_total_pf:,.2f} pF')


def tab_plot_explorer():
    st.subheader('Simple Plot Explorer')
    uploaded = st.file_uploader('Upload CSV or TAR', type=['csv', 'tar'], key='plot_upload')
    if not uploaded:
        return

    selected_suffix = None
    if uploaded.name.lower().endswith('.tar'):
        mapping = scan_tar_bytes(uploaded.getvalue())
        complete = sorted([s for s, entry in mapping.items() if 'RX' in entry and 'TX' in entry])
        if not complete:
            st.error('No complete RX/TX pairs found in this TAR file.')
            return
        selected_suffix = st.selectbox('Suffix inside TAR', complete, index=0)

    try:
        df_raw, _kind, _suffix = read_source_uploaded(uploaded, selected_suffix)
        df = prepare_loaded_dataframe(df_raw)
    except Exception as e:
        st.error(str(e))
        return

    numeric_cols = [c for c in df.columns if c != 'Time_sec' and pd.to_numeric(df[c], errors='coerce').notna().sum() > 0]
    chosen = st.multiselect('Signals', numeric_cols, default=numeric_cols[:3] if numeric_cols else [])
    if chosen:
        chart_df = df[['Time_sec'] + chosen].copy()
        for col in chosen:
            chart_df[col] = pd.to_numeric(chart_df[col], errors='coerce')
        make_line_chart(chart_df, 'Time_sec', chosen)

    st.dataframe(df.head(200), use_container_width=True, height=280)


def tab_derate_summary():
    st.subheader('Derate Summary')
    main_file = st.file_uploader('Main CSV or TAR', type=['csv', 'tar'], key='derate_main')
    chamber_file = st.file_uploader('Temperature CSV (time, value)', type=['csv'], key='derate_temp')
    if not main_file or not chamber_file:
        return

    selected_suffix = None
    if main_file.name.lower().endswith('.tar'):
        mapping = scan_tar_bytes(main_file.getvalue())
        complete = sorted([s for s, entry in mapping.items() if 'RX' in entry and 'TX' in entry])
        if not complete:
            st.error('No complete RX/TX pairs found in this TAR file.')
            return
        selected_suffix = st.selectbox('Suffix inside TAR', complete, index=0, key='derate_suffix')

    try:
        df_raw, _kind, _suffix = read_source_uploaded(main_file, selected_suffix)
        main_df = prepare_loaded_dataframe(df_raw)
        temp_df = preprocess_external_csv(chamber_file, require_value=True)
    except Exception as e:
        st.error(str(e))
        return

    power_options = [c for c in ['RxPower', 'TxPaPower', 'TxInPower'] if c in main_df.columns]
    if not power_options:
        st.warning('No supported power columns found in main file.')
        return

    manual_start = st.text_input('Manual start time if main data has no Timestamp', value='')
    power_col = st.selectbox('Power column', power_options)
    try:
        main_df['ExternalTemp'] = align_external_to_main_data(main_df, temp_df, value_col='value_num', manual_start_text=manual_start)
    except Exception as e:
        st.error(str(e))
        return

    work = main_df[['Time_sec', power_col, 'ExternalTemp']].copy()
    work[power_col] = pd.to_numeric(work[power_col], errors='coerce')
    work['ExternalTemp'] = pd.to_numeric(work['ExternalTemp'], errors='coerce')
    work = work.dropna()
    if work.empty:
        st.warning('No overlapping rows found.')
        return

    work['TempBin'] = work['ExternalTemp'].round(0)
    summary = work.groupby('TempBin')[power_col].agg(['mean', 'median', 'count']).reset_index()
    st.dataframe(summary, use_container_width=True, height=280)
    make_line_chart(summary.rename(columns={'TempBin': 'Temp_C', 'mean': 'MeanPower'}), 'Temp_C', ['MeanPower'])


def tab_arduino_sync():
    st.subheader('Arduino Sync')
    st.caption('Sync an Arduino CSV to your main CSV/TAR by Timestamp when available, or by manual start time.')
    main_file = st.file_uploader('Main CSV or TAR', type=['csv', 'tar'], key='arduino_main')
    arduino_file = st.file_uploader('Arduino CSV', type=['csv'], key='arduino_csv')
    if not main_file or not arduino_file:
        return

    selected_suffix = None
    if main_file.name.lower().endswith('.tar'):
        mapping = scan_tar_bytes(main_file.getvalue())
        complete = sorted([s for s, entry in mapping.items() if 'RX' in entry and 'TX' in entry])
        if not complete:
            st.error('No complete RX/TX pairs found in this TAR file.')
            return
        selected_suffix = st.selectbox('Suffix inside TAR', complete, index=0, key='arduino_suffix')

    try:
        df_raw, _kind, _suffix = read_source_uploaded(main_file, selected_suffix)
        main_df = prepare_loaded_dataframe(df_raw)
        arduino_df = preprocess_external_csv(arduino_file, require_value=False)
    except Exception as e:
        st.error(str(e))
        return

    numeric_arduino_cols = [
        c for c in arduino_df.columns
        if c not in ['time', 'time_utc', 'time_pacific'] and pd.to_numeric(arduino_df[c], errors='coerce').notna().sum() > 0
    ]
    if not numeric_arduino_cols:
        st.warning('No numeric Arduino columns found.')
        st.dataframe(arduino_df.head(100), use_container_width=True)
        return

    value_col = st.selectbox('Arduino signal to sync', numeric_arduino_cols)
    manual_start = st.text_input('Manual start time if main data has no Timestamp', value='')

    try:
        main_df['ArduinoSynced'] = align_external_to_main_data(main_df, arduino_df, value_col=value_col, manual_start_text=manual_start)
    except Exception as e:
        st.error(str(e))
        return

    preview_cols = ['Time_sec', 'ArduinoSynced']
    for col in ['RxPower', 'TxPaPower', 'TxTemp', 'RxTemp']:
        if col in main_df.columns:
            preview_cols.append(col)

    st.dataframe(main_df[preview_cols].head(300), use_container_width=True, height=300)
    make_line_chart(main_df[preview_cols], 'Time_sec', [c for c in preview_cols if c != 'Time_sec'])

    csv_bytes = main_df.to_csv(index=False).encode('utf-8')
    st.download_button('Download synced CSV', data=csv_bytes, file_name='arduino_synced_output.csv', mime='text/csv')


# -----------------------------
# App
# -----------------------------
st.title('WiBotic Tool O')
st.caption('All-in-one misc engineering tools. Weekly Production and SOS removed.')

tabs = st.tabs([
    'Home',
    'RF Calculator',
    'Capacitance Bank',
    'Simple Plot',
    'Derate Summary',
    'Arduino Sync',
])

with tabs[0]:
    tab_home()
with tabs[1]:
    tab_rf_calculator()
with tabs[2]:
    tab_cap_bank()
with tabs[3]:
    tab_plot_explorer()
with tabs[4]:
    tab_derate_summary()
with tabs[5]:
    tab_arduino_sync()
