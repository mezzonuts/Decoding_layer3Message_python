import pandas as pd
import re
from datetime import datetime
import os

# --- Constants ---
RSRP_THRESHOLD_WEAK = -110
RSRQ_THRESHOLD_POOR = -15
OUTPUT_FILENAME = 'telecom_log_analysis_report.xlsx'

def clean_string(text):
    """Removes illegal control characters from a string."""
    if not isinstance(text, str):
        return text
    # This regex removes characters in the C0 control character block (\x00-\x1F) and DEL (\x7F)
    return re.sub(r'[\x00-\x1F\x7F]', '', text)

def parse_rf_metrics(message_data):
    """Parses RSRP and RSRQ from MessageData string using regex."""
    rsrp, rsrq = None, None
    rsrp_match = re.search(r'RSRP:.*?(-?\d+)', message_data, re.IGNORECASE)
    rsrq_match = re.search(r'RSRQ:.*?(-?\d+)', message_data, re.IGNORECASE)
    if rsrp_match:
        rsrp = int(rsrp_match.group(1))
    if rsrq_match:
        rsrq = int(rsrq_match.group(1))
    return rsrp, rsrq

def categorize_row(row):
    """Categorizes a log row based on its MessageType and MessageData."""
    msg_type = row['MessageType']
    msg_data = row['MessageData']

    if msg_type == 'JSON_LOG':
        return 'Test Configuration', 'Konfigurasi tes dimuat.', ''
    if msg_type == 'DEVICE_INFO':
        return 'Device/System Info', 'Informasi perangkat dan operator.', msg_data
    if msg_type == 'TEST_FLOW' or (isinstance(msg_data, str) and msg_data.endswith(',A')):
        return 'Test Flow', 'Penanda alur tes.', msg_data.replace(',A', '')
    if msg_type == 'RF_MEAS':
        rsrp, rsrq = parse_rf_metrics(msg_data)
        desc = []
        if rsrp is not None:
            desc.append(f"RSRP: {rsrp} dBm")
        if rsrq is not None:
            desc.append(f"RSRQ: {rsrq} dB")
        return 'Control Plane (RF)', 'Pengukuran sinyal radio.', ', '.join(desc)
    if msg_type == 'L3_MSG':
        return 'Control Plane (Layer 3)', 'Pesan RRC (Layer 3).', msg_data
    if msg_type == 'QOE_METRIC':
        return 'User Plane (QoE)', 'Metrik kualitas pengalaman (QoE).', msg_data

    return 'Unknown', 'Kategori tidak diketahui.', msg_data

def analyze_logs(df):
    """
    Analyzes the log DataFrame to build a chronological storyline of events.
    This function implements the core state machine logic.
    """
    # --- Initialize state variables ---
    network_state = 'BAIK'  # Possible states: BAIK, SINYAL_LEMAH, KUALITAS_BURUK
    handover_start_info = None
    summary_events = []

    # --- Initialize new analysis columns ---
    df['Analisa_Kategori'] = ''
    df['Analisa_Event'] = ''
    df['Analisa_Deskripsi'] = ''

    # --- Iterate through each log entry chronologically ---
    for index, row in df.iterrows():
        timestamp = row['TimeStampCreated']
        category, event, description = categorize_row(row)

        # --- Update the detailed log columns ---
        df.at[index, 'Analisa_Kategori'] = category
        df.at[index, 'Analisa_Event'] = event
        df.at[index, 'Analisa_Deskripsi'] = description

        # --- Storyline State Machine Logic ---

        # 1. RF Condition Analysis
        if category == 'Control Plane (RF)':
            rsrp, rsrq = parse_rf_metrics(row['MessageData'])

            is_weak = rsrp is not None and rsrp < RSRP_THRESHOLD_WEAK
            is_poor = rsrq is not None and rsrq < RSRQ_THRESHOLD_POOR

            new_state = network_state
            if is_weak or is_poor:
                # Determine the primary cause for the summary
                cause = []
                if is_weak:
                    new_state = 'SINYAL_LEMAH'
                    cause.append(f"RSRP di {rsrp} dBm (Threshold: {RSRP_THRESHOLD_WEAK})")
                if is_poor:
                    new_state = 'KUALITAS_BURUK'
                    cause.append(f"RSRQ di {rsrq} dB (Threshold: {RSRQ_THRESHOLD_POOR})")

                if network_state == 'BAIK':
                    summary_events.append({
                        'Timestamp': timestamp,
                        'Kategori': 'Peringatan Jaringan',
                        'Narasi Peristiwa': f"Kondisi jaringan menurun. Penyebab: {', '.join(cause)}."
                    })
            else: # Signal is good
                new_state = 'BAIK'
                if network_state != 'BAIK':
                    summary_events.append({
                        'Timestamp': timestamp,
                        'Kategori': 'Pemulihan Jaringan',
                        'Narasi Peristiwa': f"Sinyal kembali normal. RSRP: {rsrp} dBm, RSRQ: {rsrq} dB."
                    })
            network_state = new_state

        # 2. Handover (RRC Reconfiguration) Analysis
        elif category == 'Control Plane (Layer 3)':
            msg_data = row['MessageData']
            if 'RRCConnectionReconfiguration' in msg_data and 'Complete' not in msg_data:
                handover_start_info = {'time': pd.to_datetime(timestamp)}
            elif 'RRCConnectionReconfigurationComplete' in msg_data and handover_start_info:
                end_time = pd.to_datetime(timestamp)
                duration = (end_time - handover_start_info['time']).total_seconds() * 1000
                summary_events.append({
                    'Timestamp': timestamp,
                    'Kategori': 'Aksi Jaringan',
                    'Narasi Peristiwa': f"Handover Berhasil terdeteksi. Durasi: {duration:.0f} ms."
                })
                handover_start_info = None # Reset after completion

        # 3. User Impact (QoE) Analysis
        elif category == 'User Plane (QoE)':
            if network_state != 'BAIK':
                # Simple check for negative QoE indicators
                if 'stall' in row['MessageData'].lower() or ('mos' in row['MessageData'].lower() and float(re.search(r'MOS:.*?(\d\.\d)', row['MessageData'], re.IGNORECASE).group(1)) < 3.0):
                    summary_events.append({
                        'Timestamp': timestamp,
                        'Kategori': 'Dampak Pengguna',
                        'Narasi Peristiwa': f"Kualitas layanan (QoE) menurun saat {network_state}. Detail: {row['MessageData']}"
                    })

    # Create DataFrames from the results
    summary_df = pd.DataFrame(summary_events)
    return df, summary_df

def main(input_filepath):
    """Main function to run the analysis."""
    print(f"Membaca file log dari: {input_filepath}")
    if not os.path.exists(input_filepath):
        print(f"Error: File tidak ditemukan di '{input_filepath}'")
        return

    # Load data with error handling for potentially bad lines
    try:
        df = pd.read_csv(input_filepath, sep=';', on_bad_lines='skip', engine='python')
    except Exception as e:
        print(f"Gagal membaca CSV: {e}")
        return

    print("Membersihkan data dari karakter ilegal...")
    # Apply cleaning to all object (string) columns
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].apply(clean_string)

    # Convert timestamp and sort the logs chronologically
    df['TimeStampCreated'] = pd.to_datetime(df['TimeStampCreated'], errors='coerce')
    df.dropna(subset=['TimeStampCreated'], inplace=True)
    df.sort_values(by='TimeStampCreated', inplace=True)
    df.reset_index(drop=True, inplace=True)

    print("Menganalisis log dan membangun storyline...")
    detailed_df, summary_df = analyze_logs(df)

    print(f"Menyimpan laporan ke file Excel: {OUTPUT_FILENAME}")
    try:
        # --- Prepare for Excel Export ---
        # Excel does not support timezone-aware datetimes. We must remove the timezone info.
        detailed_df_export = detailed_df.copy()
        summary_df_export = summary_df.copy()

        detailed_df_export['TimeStampCreated'] = pd.to_datetime(detailed_df_export['TimeStampCreated']).dt.tz_localize(None)
        if not summary_df_export.empty:
            summary_df_export['Timestamp'] = pd.to_datetime(summary_df_export['Timestamp']).dt.tz_localize(None)


        with pd.ExcelWriter(OUTPUT_FILENAME, engine='openpyxl') as writer:
            detailed_df_export.to_excel(writer, sheet_name='Detailed Log Analysis', index=False)
            summary_df_export.to_excel(writer, sheet_name='Summary of Events', index=False)
        print("\nAnalisis selesai! Laporan berhasil dibuat.")
    except Exception as e:
        print(f"Gagal menyimpan file Excel: {e}")
        print("Pastikan Anda telah menginstal library 'openpyxl': pip install openpyxl")

if __name__ == '__main__':
    # The script will look for 'Message.csv' in the same directory.
    # You can change this to your actual log file name.
    input_log_file = 'Message.csv'
    main(input_log_file)
