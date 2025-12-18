#mohamed Waleed & saif & youssef (Dead Line : wednesday )
import os
import time
import random
import threading
import queue

import pandas as pd
import gradio as gr
import matplotlib.pyplot as plt

# IMPORTANT: tailored to YOUR repo:
# INFORMATION-STORAGE-PROJ/
#   Modules/
#     Raid_Calculation.py
#     ...
from Modules.Raid_Calculation import (
    calculate_capacity_breakdown_dict,
    usable_capacity_percent,
    redundancy_percent,
    space_efficiency,
)

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".mp4", ".mov", ".mkv", ".avi", ".webm"}


# -----------------------------
# Helpers (no imports that cause side effects)
# -----------------------------
def get_media_files(folder: str):
    paths = []
    for root, _, files in os.walk(folder):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXT:
                paths.append(os.path.join(root, f))
    return paths


def total_size_bytes(paths):
    return sum(os.path.getsize(p) for p in paths)


def summary_statistics(df: pd.DataFrame):
    fields = ["read_time_ms", "write_time_ms", "read_iops", "write_iops", "total_time_ms"]
    rows = []
    for field in fields:
        if field in df.columns:
            rows.append({
                "metric": field,
                "mean": df[field].mean(),
                "std": df[field].std(),
                "median": df[field].median(),
                "min": df[field].min(),
                "max": df[field].max(),
                "variance": df[field].var(),
            })
        else:
            rows.append({"metric": field, "mean": None, "std": None, "median": None, "min": None, "max": None, "variance": None})
    return pd.DataFrame(rows)


def save_report_csv(df: pd.DataFrame, filename: str):
    reports_folder = "reports"
    os.makedirs(reports_folder, exist_ok=True)
    full_path = os.path.join(reports_folder, filename)
    df.to_csv(full_path, index=False)
    return full_path


# -----------------------------
# RAID timing model using REAL folder size
# -----------------------------
def simulate_read_time_ms(total_bytes: int, raid_level: str, num_disks: int, base_read_MBps: float):
    size_MB = total_bytes / (1024 * 1024)

    if raid_level == "RAID 0":
        effective = base_read_MBps * num_disks
    elif raid_level == "RAID 1":
        # reads can be served from multiple disks; cap boost a bit
        effective = base_read_MBps * min(num_disks, 2) * 1.1
    elif raid_level == "RAID 5":
        effective = base_read_MBps * max(num_disks - 1, 1) * 1.05
    else:
        effective = base_read_MBps

    seconds = size_MB / max(effective, 1e-6)
    # small sleep to mimic I/O without freezing too long
    time.sleep(min(seconds / 50.0, 0.25))
    return seconds * 1000.0


def simulate_write_time_ms(total_bytes: int, raid_level: str, num_disks: int, base_write_MBps: float):
    size_MB = total_bytes / (1024 * 1024)

    if raid_level == "RAID 0":
        effective = base_write_MBps * num_disks
        overhead = 1.0
    elif raid_level == "RAID 1":
        effective = base_write_MBps
        overhead = 1.5
    elif raid_level == "RAID 5":
        effective = base_write_MBps * max(num_disks - 1, 1)
        overhead = 1.3
    else:
        effective = base_write_MBps
        overhead = 1.0

    seconds = (size_MB / max(effective, 1e-6)) * overhead
    time.sleep(min(seconds / 50.0, 0.25))
    return seconds * 1000.0


def run_sim_all(folder: str, num_disks: int, base_read: float, base_write: float):
    if not os.path.isdir(folder):
        raise ValueError("Folder path does not exist or is not a directory.")

    files = get_media_files(folder)
    if not files:
        raise ValueError("No supported media files found in that folder.")

    total_bytes = total_size_bytes(files)

    rows = []
    for raid in ["RAID 0", "RAID 1", "RAID 5"]:
        read_ms = simulate_read_time_ms(total_bytes, raid, num_disks, base_read)
        write_ms = simulate_write_time_ms(total_bytes, raid, num_disks, base_write)
        total_ms = read_ms + write_ms

        rows.append({
            "raid_level": raid,
            "disk_count": num_disks,
            "total_files": len(files),
            "total_size_bytes": int(total_bytes),
            "read_time_ms": float(read_ms),
            "write_time_ms": float(write_ms),
            "total_time_ms": float(total_ms),
            "read_iops": random.randint(800, 1500),
            "write_iops": random.randint(500, 1200),
            "usable_%": usable_capacity_percent(num_disks, raid),
            "redundancy_%": redundancy_percent(num_disks, raid),
            "efficiency_%": space_efficiency(num_disks, raid) * 100.0,
        })

    df = pd.DataFrame(rows)
    stats_df = summary_statistics(df)
    return df, stats_df


# -----------------------------
# Plotting
# -----------------------------
def make_pie(raid_level: str, num_disks: int):
    breakdown = calculate_capacity_breakdown_dict(num_disks, raid_level)
    usable = breakdown["usable"]
    parity = breakdown["parity"]
    mirror = breakdown["mirror"]
    total = usable + parity + mirror

    labels = ["Usable", "Parity", "Mirrored"]
    values = [
        (usable / total) * 100 if total else 0,
        (parity / total) * 100 if total else 0,
        (mirror / total) * 100 if total else 0,
    ]

    fig = plt.figure(figsize=(5, 5))
    plt.pie(values, labels=labels, autopct="%1.1f%%")
    plt.title(f"{raid_level} Storage Distribution ({num_disks} disks)")
    return fig


def make_bar(df: pd.DataFrame):
    raids = df["raid_level"].tolist()
    total_ms = df["total_time_ms"].tolist()

    fig = plt.figure(figsize=(7, 5))
    plt.bar(raids, total_ms)
    plt.ylabel("Total Time (ms) [read + write]")
    plt.title("Time Taken by Each RAID Level")
    return fig


# -----------------------------
# Background thread wrapper
# -----------------------------
def worker(folder, num_disks, base_read, base_write, out_q):
    try:
        df, stats = run_sim_all(folder, num_disks, base_read, base_write)
        out_q.put(("ok", df, stats))
    except Exception as e:
        out_q.put(("err", str(e), None))


def on_run(folder, num_disks, base_read, base_write, pie_raid_level):
    log = ["Starting simulation in background thread..."]
    q = queue.Queue()

    t = threading.Thread(
        target=worker,
        args=(folder, int(num_disks), float(base_read), float(base_write), q),
        daemon=True
    )
    t.start()

    # Poll until done (Gradio stays responsive)
    while True:
        try:
            msg = q.get_nowait()
            break
        except queue.Empty:
            time.sleep(0.1)

    if msg[0] == "err":
        log.append(f"ERROR: {msg[1]}")
        return None, None, None, "\n".join(log), None, None

    df, stats_df = msg[1], msg[2]

    pie_fig = make_pie(pie_raid_level, int(num_disks))
    bar_fig = make_bar(df)

    report_path = save_report_csv(df, "raid_report.csv")
    summary_path = save_report_csv(stats_df, "raid_summary.csv")

    log.append("Simulation completed.")
    log.append(f"Saved: {report_path}")
    log.append(f"Saved: {summary_path}")

    # Return files as downloadable
    return df, stats_df, pie_fig, "\n".join(log), bar_fig, [report_path, summary_path]


# -----------------------------
# Gradio UI
# -----------------------------
with gr.Blocks(title="Multimedia RAID Performance Simulator") as demo:
    gr.Markdown("# Multimedia Storage Performance Simulator (RAID 0 / RAID 1 / RAID 5)")

    with gr.Row():
        folder = gr.Textbox(label="Folder Path", placeholder="e.g. C:\\Users\\You\\Videos  OR  /home/you/media")
        num_disks = gr.Slider(2, 12, value=4, step=1, label="Number of Disks")

    with gr.Row():
        base_read = gr.Number(value=150, label="Base Read Speed per disk (MB/s)")
        base_write = gr.Number(value=120, label="Base Write Speed per disk (MB/s)")
        pie_raid_level = gr.Dropdown(["RAID 0", "RAID 1", "RAID 5"], value="RAID 5", label="Pie Chart RAID Level")

    run_btn = gr.Button("Run Simulation (BG Thread)")

    log = gr.Textbox(label="Log", lines=8)

    with gr.Tab("Tables"):
        df_out = gr.Dataframe(label="Results")
        stats_out = gr.Dataframe(label="Statistics")

    with gr.Tab("Charts"):
        pie_plot = gr.Plot(label="Usable vs Parity vs Mirrored")
        bar_plot = gr.Plot(label="Time by RAID Level")

    downloads = gr.File(label="Download CSV Reports", file_count="multiple")

    run_btn.click(
        fn=on_run,
        inputs=[folder, num_disks, base_read, base_write, pie_raid_level],
        outputs=[df_out, stats_out, pie_plot, log, bar_plot, downloads],
    )

if __name__ == "__main__":
    demo.launch()
