import io
import tempfile
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
# Box Build Report helpers
# -----------------------------
@st.cache_resource(show_spinner=False)
def load_box_build_module():
    import importlib.util
    module_path = Path(__file__).with_name('bb_report.py')
    spec = importlib.util.spec_from_file_location('bb_report_module', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@st.cache_resource(show_spinner=False)
def get_box_build_table(db_name: str):
    bb = load_box_build_module()
    db_enum = bb.DatabaseName.PRODUCTION if db_name == 'Production' else bb.DatabaseName.DEVELOPMENT
    return bb.get_db_table(db_enum)


def _safe_get(record, *path, default=None):
    cur = record
    for key in path:
        try:
            cur = cur[key]
        except Exception:
            return default
    return cur


def _record_summary_row(record):
    create_time = record.get('create_time')
    local_text = ''
    if create_time:
        try:
            dt_utc = pd.to_datetime(create_time, utc=True, errors='coerce')
            if pd.notna(dt_utc):
                local_text = dt_utc.tz_convert('America/Los_Angeles').strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            local_text = str(create_time)
    return {
        'Local Time': local_text,
        'Procedure': _safe_get(record, 'config', 'procedure_name', default=''),
        'Result': 'Passed' if record.get('passed') else 'Failed',
        'Serial': record.get('serial', ''),
        'Model': _safe_get(record, 'config', 'ids', 'mn', default=''),
        'Mac': record.get('mac', record.get('oc_mac', '')),
    }


def _extract_chart_frames(record):
    frames = []
    for key in sorted(k for k in record.keys() if str(k).startswith('datalog_')):
        payload = record.get(key, {})
        if not isinstance(payload, dict):
            continue
        for device_name, csv_text in payload.items():
            try:
                df = pd.read_csv(io.StringIO(csv_text))
            except Exception:
                continue
            if 'Timestamp' in df.columns:
                df['Timestamp'] = pd.to_numeric(df['Timestamp'], errors='coerce')
                df = df.dropna(subset=['Timestamp']).reset_index(drop=True)
                if not df.empty:
                    df['Timestamp'] = df['Timestamp'] - df['Timestamp'].iloc[0]
            for col in df.columns:
                if col != 'Timestamp':
                    df[col] = pd.to_numeric(df[col], errors='ignore')
            frames.append((key, str(device_name).upper(), df))
    return frames


def _render_box_build_record_view(record):
    summary = _record_summary_row(record)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Result', summary['Result'])
    c2.metric('Model', summary['Model'] or '—')
    c3.metric('Serial', summary['Serial'] or '—')
    c4.metric('Mac', summary['Mac'] or '—')

    st.caption(
        f"Procedure: {summary['Procedure'] or '—'} | "
        f"Local time: {summary['Local Time'] or '—'}"
    )

    with st.expander('Record details', expanded=False):
        details = {
            'create_time': record.get('create_time'),
            'time': record.get('time'),
            'serial': record.get('serial'),
            'mac': record.get('mac'),
            'oc_mac': record.get('oc_mac'),
            'model': _safe_get(record, 'config', 'ids', 'mn', default=''),
            'procedure_name': _safe_get(record, 'config', 'procedure_name', default=''),
            'passed': record.get('passed'),
            'report_type': str(record.get('type')),
        }
        st.json(details)

    tol = record.get('tolerance_checks')
    if isinstance(tol, dict) and tol:
        rows = []
        for test_name, values in tol.items():
            if isinstance(values, dict):
                rows.append({
                    'Test': test_name,
                    'Low': values.get('lower_limit'),
                    'High': values.get('upper_limit'),
                    'Actual': values.get('actual'),
                    'Pass': values.get('pass'),
                })
        if rows:
            st.markdown('**Tolerance checks**')
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=260)

    prompts = record.get('pass_fail_prompts')
    if isinstance(prompts, dict) and prompts:
        rows = [{'Prompt': k, 'Pass': v} for k, v in prompts.items()]
        st.markdown('**Manual checks**')
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=180)

    frames = _extract_chart_frames(record)
    if not frames:
        st.info('No datalog charts found in this record.')
        return

    st.markdown('**Test data viewer**')
    for datalog_name, device_name, df in frames:
        with st.expander(f'{datalog_name} · {device_name}', expanded=('wireless' in datalog_name.lower())):
            if 'Timestamp' not in df.columns:
                st.dataframe(df.head(200), use_container_width=True, height=260)
                continue
            numeric_cols = [
                c for c in df.columns
                if c != 'Timestamp' and pd.to_numeric(df[c], errors='coerce').notna().sum() > 0
            ]
            default_cols = numeric_cols[:4]
            selected = st.multiselect(
                f'Signals for {datalog_name} / {device_name}',
                numeric_cols,
                default=default_cols,
                key=f"bbsig_{datalog_name}_{device_name}",
            )
            if selected:
                chart_df = df[['Timestamp'] + selected].copy()
                for col in selected:
                    chart_df[col] = pd.to_numeric(chart_df[col], errors='coerce')
                make_line_chart(chart_df, 'Timestamp', selected, height=320)
            st.dataframe(df.head(200), use_container_width=True, height=220)


def tab_box_build_report():
    st.subheader('Box Build Report')
    st.caption('Search Box Build by serial number or MAC, preview a record, and generate the original PDF report.')

    bb = load_box_build_module()

    left, right = st.columns([1.2, 1])
    with left:
        db_name = st.selectbox('Database', ['Production', 'Development'], index=0)
        query = st.text_input('Serial number or MAC address', value='').strip().upper()
    with right:
        st.markdown('')
        st.markdown('')
        search_clicked = st.button('Search Box Build', type='primary', use_container_width=True)

    if search_clicked:
        normalized, input_type = bb.detect_serial_or_mac(query)
        if input_type == bb.InputType.UNKNOWN:
            st.error('That value does not look like a valid serial number or MAC address.')
        else:
            try:
                table = get_box_build_table(db_name)
                db_enum = bb.DatabaseName.PRODUCTION if db_name == 'Production' else bb.DatabaseName.DEVELOPMENT
                items = bb.get_item_list_from_serial_or_mac(db_enum, table, normalized, input_type) or []
                st.session_state['bb_items'] = items
                st.session_state['bb_query'] = normalized
                st.session_state['bb_db_name'] = db_name
            except Exception as e:
                st.exception(e)

    items = st.session_state.get('bb_items', [])
    query_used = st.session_state.get('bb_query', query)

    if not items:
        st.info('Enter a serial number or MAC address and click Search Box Build.')
        return

    st.success(f'Found {len(items)} matching record(s) for {query_used}.')

    summary_df = pd.DataFrame([_record_summary_row(r) for r in items])
    st.dataframe(summary_df, use_container_width=True, height=min(420, 80 + 35 * len(summary_df)))

    labels = [
        f"{i+1}. {row['Local Time']} | {row['Procedure']} | {row['Result']} | {row['Serial']}"
        for i, row in summary_df.iterrows()
    ]
    selected_label = st.selectbox('Select record to view', labels, index=max(len(labels) - 1, 0))
    selected_index = labels.index(selected_label)
    record = items[selected_index]

    if not record.get('passed'):
        st.warning('This is a failed record. The original CLI script normally asks for confirmation before generating the PDF.')

    _render_box_build_record_view(record)

    export_col1, export_col2 = st.columns([1, 2])
    with export_col1:
        generate_pdf = st.button('Generate PDF report', use_container_width=True)
    with export_col2:
        st.caption('Uses the original bb_report.py PDF generator and saves the file to a temporary folder for download.')

    if generate_pdf:
        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix='boxbuild_report_'))
            original_input = __builtins__.input
            def _fake_input(prompt=''):
                return 'Y'
            __builtins__.input = _fake_input
            try:
                bb.create_report(items, query_used, selected_index, tmp_dir)
            finally:
                __builtins__.input = original_input

            pdf_files = sorted(tmp_dir.glob('*.pdf'))
            if not pdf_files:
                st.error('No PDF was generated.')
            else:
                pdf_path = pdf_files[0]
                st.download_button(
                    'Download generated PDF',
                    data=pdf_path.read_bytes(),
                    file_name=pdf_path.name,
                    mime='application/pdf',
                    use_container_width=True,
                )
        except Exception as e:
            st.exception(e)

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
    'Box Build Report',
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
with tabs[6]:
    tab_box_build_report()
