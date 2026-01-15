import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta, time
from io import BytesIO

# =========================================================
# Flowboard — MVP v0.1 (Bible-locked skeleton + v0 planner)
# =========================================================

st.set_page_config(page_title="Flowboard", page_icon="🧠", layout="wide")

# -----------------------------
# Helpers
# -----------------------------
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WD_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

LOAD_MODES = ["Light", "Normal", "Heavy"]  # Heavy = +20%
LOAD_MULTIPLIER = {"Light": 0.85, "Normal": 1.00, "Heavy": 1.20}  # conservative default

def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())

def as_date(x):
    """Best-effort to convert to date."""
    if pd.isna(x):
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    try:
        return pd.to_datetime(x).date()
    except Exception:
        return None

def pick_col(cols, candidates):
    """Return first matching column (case-insensitive contains)."""
    cols_lower = {c.lower(): c for c in cols}
    for cand in candidates:
        for c in cols:
            if cand in c.lower():
                return c
    # also exact-ish
    for cand in candidates:
        if cand in cols_lower:
            return cols_lower[cand]
    return None

def normalize_address(row, number_col, street_col, suburb_col, city_col):
    parts = []
    if number_col and pd.notna(row.get(number_col, None)):
        parts.append(str(row[number_col]).strip())
    if street_col and pd.notna(row.get(street_col, None)):
        parts.append(str(row[street_col]).strip())
    addr = " ".join(parts).strip()

    loc_parts = []
    if suburb_col and pd.notna(row.get(suburb_col, None)):
        loc_parts.append(str(row[suburb_col]).strip())
    if city_col and pd.notna(row.get(city_col, None)):
        loc_parts.append(str(row[city_col]).strip())
    loc = ", ".join([p for p in loc_parts if p])

    return addr if not loc else f"{addr} — {loc}"

def urgency_band(target_date: date, week_start: date):
    """
    Bible-locked:
    - Target Date fixed.
    - Completion window: target-1 month to target+1 month
    - Dark Blue: week currently being scheduled is the FINAL schedulable week before window closes.
    - Light Blue: final 2-week warning band prior to Dark Blue
      described by user as: from ~3 weeks till cutoff to ~1 week + 1 day till cutoff.
    """
    if not target_date:
        return "Flexible"

    window_close = target_date + timedelta(days=30)  # approx 1 month after
    # Define "week currently being scheduled" as its week window [week_start, week_start+6]
    week_end = week_start + timedelta(days=6)

    # Find the final schedulable week start: the Monday of the week that contains window_close
    last_week_start = monday_of_week(window_close)

    # Dark Blue if this scheduled week is the last week start
    if week_start == last_week_start:
        return "Dark Blue"

    # Light Blue band: the 2 weeks leading up to last_week_start
    # i.e., week_start is 1 or 2 weeks before last_week_start
    if week_start in (last_week_start - timedelta(days=7), last_week_start - timedelta(days=14)):
        return "Light Blue"

    return "Flexible"

def futile_rank(status_val: str):
    """
    Within Dark Blues:
    prioritize those without futile attempts:
    (not Futile 1 or Futile 2) first, then Futile 1, then Futile 2.
    """
    if not status_val:
        return 0
    s = str(status_val).strip().lower()
    if "futile 2" in s or "futile2" in s:
        return 2
    if "futile 1" in s or "futile1" in s:
        return 1
    return 0

def estimate_minutes(bedrooms, inspection_type=None):
    """
    Conservative baseline from your rules of thumb (not precise):
    - 1 / bedsit ~7 min
    - 2–3 ~15 min
    - 4–6 ~30–50 min (use 40 conservative mid)
    Adds simple modifier for inspection type if present (placeholder).
    """
    try:
        b = int(float(bedrooms))
    except Exception:
        b = None

    if b is None:
        base = 15
    elif b <= 1:
        base = 7
    elif b <= 3:
        base = 15
    else:
        base = 40

    # very light placeholder modifier (kept conservative)
    if inspection_type:
        t = str(inspection_type).lower()
        if "plus" in t or "full" in t or "condition" in t:
            base = int(base * 1.35)
        elif "h&s" in t or "h&s" in t or "health" in t:
            base = int(base * 1.00)

    return base

def session_capacity_minutes(time_mode, global_times, day_override_times, day_name, session_name, load_mode):
    """
    Convert time bounds into a conservative session budget.
    We don't do minute-perfect scheduling; we use a budget to stop filling.
    """
    # pick times
    times = day_override_times.get(day_name) or global_times
    if time_mode == "Inspection window":
        start_t = times["start_first"]
        end_t = times["latest_arrival_last"]
    else:
        start_t = times["depart_depot"]
        end_t = times["return_depot"]

    if not (start_t and end_t):
        base_minutes = 240  # fallback conservative
    else:
        dt0 = datetime.combine(date.today(), start_t)
        dt1 = datetime.combine(date.today(), end_t)
        base_minutes = max(0, int((dt1 - dt0).total_seconds() // 60))

    # Split day into AM/PM conservatively.
    # Default: AM ~55%, PM ~45% of day window (field reality).
    if session_name == "AM":
        sess = int(base_minutes * 0.55)
    else:
        sess = int(base_minutes * 0.45)

    # Apply load mode multiplier (Light reduces, Heavy increases)
    sess = int(sess * LOAD_MULTIPLIER[load_mode])

    # Safety buffer (conservative): keep 10% unallocated
    sess = int(sess * 0.90)

    return max(60, sess)  # never less than 1 hour

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Completed Schedule")
    return out.getvalue()

# -----------------------------
# State init
# -----------------------------
if "view" not in st.session_state:
    st.session_state.view = "setup"  # setup | review
if "df" not in st.session_state:
    st.session_state.df = None
if "colmap" not in st.session_state:
    st.session_state.colmap = {}
if "plan" not in st.session_state:
    st.session_state.plan = None
if "plan_df" not in st.session_state:
    st.session_state.plan_df = None

# -----------------------------
# Header
# -----------------------------
st.markdown(
    """
    <div style="display:flex;align-items:baseline;gap:14px;">
      <div style="font-size:40px;font-weight:800;">Flowboard</div>
      <div style="font-size:16px;opacity:0.75;">Weekly Planning + Memory Layer</div>
    </div>
    """,
    unsafe_allow_html=True
)

# -----------------------------
# Sidebar: Week Setup
# -----------------------------
with st.sidebar:
    st.subheader("Week Setup")

    uploaded = st.file_uploader("Import Backlog (Excel)", type=["xlsx", "xls"])
    if uploaded is not None:
        try:
            st.session_state.df = pd.read_excel(uploaded)
        except Exception as e:
            st.error(f"Could not read file: {e}")
            st.stop()

    df = st.session_state.df

    # Week selection
    today = date.today()
    default_week = monday_of_week(today)
    week_start = st.date_input("Week starting", value=default_week)
    week_start = monday_of_week(week_start)

    st.caption("Working days (toggle off for holidays/leave):")
    active_days = {}
    cols_days = st.columns(5)
    for i, d in enumerate(WEEKDAYS[:5]):  # Mon-Fri for MVP
        with cols_days[i]:
            active_days[d] = st.checkbox(d[:3], value=True, key=f"day_{d}")

    # Time bounds mode
    st.divider()
    st.caption("Time bounds (global, with per-day overrides):")
    time_mode = st.radio(
        "Mode",
        ["Inspection window", "Depot window"],
        horizontal=True,
        label_visibility="collapsed"
    )

    # Global time bounds
    if time_mode == "Inspection window":
        start_first = st.time_input("Start 1st inspection", value=time(8, 30))
        latest_arrival_last = st.time_input("Latest arrival @ last inspection", value=time(15, 30))
        global_times = {
            "start_first": start_first,
            "latest_arrival_last": latest_arrival_last,
            "depart_depot": None,
            "return_depot": None,
        }
    else:
        depart_depot = st.time_input("Depart depot", value=time(8, 0))
        return_depot = st.time_input("Return to depot", value=time(16, 30))
        global_times = {
            "start_first": None,
            "latest_arrival_last": None,
            "depart_depot": depart_depot,
            "return_depot": return_depot,
        }

    # Sessions + Load modes + Overrides
    st.divider()
    st.caption("Sessions & load per day (Light / Normal / Heavy):")

    day_override_times = {}
    day_sessions = {}

    for d in WEEKDAYS[:5]:
        if not active_days.get(d, False):
            continue

        with st.expander(d, expanded=False):
            am_enabled = st.checkbox("AM enabled", value=True, key=f"{d}_am_on")
            pm_enabled = st.checkbox("PM enabled", value=True, key=f"{d}_pm_on")

            am_load = st.selectbox("AM load", LOAD_MODES, index=1, key=f"{d}_am_load")
            pm_load = st.selectbox("PM load", LOAD_MODES, index=1, key=f"{d}_pm_load")

            override = st.checkbox("Override times for this day", value=False, key=f"{d}_override")
            if override:
                if time_mode == "Inspection window":
                    o_start = st.time_input("Start 1st inspection (override)", value=global_times["start_first"], key=f"{d}_o_start")
                    o_last = st.time_input("Latest arrival last (override)", value=global_times["latest_arrival_last"], key=f"{d}_o_last")
                    day_override_times[d] = {
                        "start_first": o_start,
                        "latest_arrival_last": o_last,
                        "depart_depot": None,
                        "return_depot": None,
                    }
                else:
                    o_dep = st.time_input("Depart depot (override)", value=global_times["depart_depot"], key=f"{d}_o_dep")
                    o_ret = st.time_input("Return depot (override)", value=global_times["return_depot"], key=f"{d}_o_ret")
                    day_override_times[d] = {
                        "start_first": None,
                        "latest_arrival_last": None,
                        "depart_depot": o_dep,
                        "return_depot": o_ret,
                    }

            day_sessions[d] = {
                "AM": {"enabled": am_enabled, "load": am_load},
                "PM": {"enabled": pm_enabled, "load": pm_load},
            }

    st.divider()

    go = st.button("Go ahead, PLAN my day", type="primary", use_container_width=True, disabled=(df is None))

# -----------------------------
# Main: Setup view (Overview)
# -----------------------------
df = st.session_state.df

if df is None:
    st.info("Upload an Excel backlog to begin.")
    st.stop()

# Column mapping (generic; can be adjusted without asking you questions)
cols = list(df.columns)

# Best-effort auto-detection (generic)
auto_target = pick_col(cols, ["target_date", "target date", "due", "target"])
auto_status = pick_col(cols, ["status", "survey_status", "survey status", "state"])
auto_bed = pick_col(cols, ["bdrm", "bed", "bedroom", "bdrm_no", "bdrm no"])
auto_type = pick_col(cols, ["inspection type", "type", "visit type"])
auto_ref = pick_col(cols, ["reference", "property_reference", "property reference", "id"])
auto_street = pick_col(cols, ["street"])
auto_number = pick_col(cols, ["number", "street number", "no."])
auto_suburb = pick_col(cols, ["suburb"])
auto_city = pick_col(cols, ["city", "town"])

# Offer a small "Data mapping" expander in main panel (optional, not annoying)
with st.expander("Data mapping (optional)", expanded=False):
    st.caption("Flowboard is input-format agnostic. These defaults are detected; change if needed.")
    col_target = st.selectbox("Target date column", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_target) if auto_target in cols else 0)
    col_status = st.selectbox("Status column (e.g. Futile 1/2)", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_status) if auto_status in cols else 0)
    col_bed = st.selectbox("Bedrooms column", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_bed) if auto_bed in cols else 0)
    col_type = st.selectbox("Inspection type column", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_type) if auto_type in cols else 0)
    col_ref = st.selectbox("Reference/ID column", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_ref) if auto_ref in cols else 0)
    col_number = st.selectbox("Street number column", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_number) if auto_number in cols else 0)
    col_street = st.selectbox("Street name column", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_street) if auto_street in cols else 0)
    col_suburb = st.selectbox("Suburb column", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_suburb) if auto_suburb in cols else 0)
    col_city = st.selectbox("City column", ["(none)"] + cols, index=(["(none)"] + cols).index(auto_city) if auto_city in cols else 0)

# Persist mapping
st.session_state.colmap = {
    "target": None if col_target == "(none)" else col_target,
    "status": None if col_status == "(none)" else col_status,
    "bed": None if col_bed == "(none)" else col_bed,
    "type": None if col_type == "(none)" else col_type,
    "ref": None if col_ref == "(none)" else col_ref,
    "number": None if col_number == "(none)" else col_number,
    "street": None if col_street == "(none)" else col_street,
    "suburb": None if col_suburb == "(none)" else col_suburb,
    "city": None if col_city == "(none)" else col_city,
}

# Build derived fields for overview
cm = st.session_state.colmap

df_work = df.copy()

# target_date parsed
if cm["target"]:
    df_work["_target_date"] = df_work[cm["target"]].apply(as_date)
else:
    df_work["_target_date"] = None

df_work["_urgency"] = df_work["_target_date"].apply(lambda td: urgency_band(td, week_start))

# address label
df_work["_label"] = df_work.apply(
    lambda r: normalize_address(r, cm["number"], cm["street"], cm["suburb"], cm["city"]),
    axis=1
)

# estimates
df_work["_mins"] = df_work.apply(
    lambda r: estimate_minutes(r.get(cm["bed"]) if cm["bed"] else None,
                               r.get(cm["type"]) if cm["type"] else None),
    axis=1
)

# status rank
if cm["status"]:
    df_work["_futile_rank"] = df_work[cm["status"]].apply(futile_rank)
else:
    df_work["_futile_rank"] = 0

# Overview UI (Main Panel)
st.subheader("Planning Overview")

c1, c2, c3, c4 = st.columns(4)
dark_ct = int((df_work["_urgency"] == "Dark Blue").sum())
light_ct = int((df_work["_urgency"] == "Light Blue").sum())
flex_ct = int((df_work["_urgency"] == "Flexible").sum())
# Requested/Restricted is input-specific; we’ll add when we have a constraint column.
req_ct = 0

c1.metric("Dark Blue (Must this week)", dark_ct)
c2.metric("Light Blue (Warning band)", light_ct)
c3.metric("Requested days", req_ct)
c4.metric("Flexible backlog", flex_ct)

# Week capacity summary
active_day_list = [d for d in WEEKDAYS[:5] if st.session_state.get(f"day_{d}", False)]
sessions_enabled = 0
light_sess = 0
heavy_sess = 0
for d in active_day_list:
    for sess in ["AM", "PM"]:
        enabled = st.session_state.get(f"{d}_{sess.lower()}_on", True)
        if enabled:
            sessions_enabled += 1
            lm = st.session_state.get(f"{d}_{sess.lower()}_load", "Normal")
            if lm == "Light":
                light_sess += 1
            if lm == "Heavy":
                heavy_sess += 1

st.divider()
cc1, cc2, cc3, cc4 = st.columns(4)
cc1.metric("Active days", len(active_day_list))
cc2.metric("Sessions enabled", sessions_enabled)
cc3.metric("Light sessions", light_sess)
cc4.metric("Heavy sessions", heavy_sess)

# Optional cluster hint (cheap, generic): top suburbs
if cm["suburb"] and cm["suburb"] in df_work.columns:
    top = (
        df_work[cm["suburb"]]
        .fillna("Unknown")
        .astype(str)
        .value_counts()
        .head(6)
        .reset_index()
    )
    top.columns = ["Area", "Jobs"]
    st.write("**Where the work is (top areas)**")
    st.dataframe(top, use_container_width=True, hide_index=True)

# Warnings/flags (conservative)
flags = []
if cm["status"]:
    futile2 = df_work[cm["status"]].astype(str).str.lower().str.contains("futile 2", na=False).sum()
    if futile2:
        flags.append(f"⚠️ {int(futile2)} jobs show 'Futile 2' history (expect higher uncertainty).")

if flags:
    st.write("**Flags**")
    for f in flags:
        st.write(f)

# -----------------------------
# Planning Engine (v0)
# -----------------------------
def build_week_plan(df_in: pd.DataFrame, week_start: date, active_days, day_sessions, time_mode, global_times, day_override_times):
    """
    v0 conservative planner:
    - Build buckets for enabled days/sessions
    - Sort jobs by urgency + internal futile rank (only inside Dark Blue)
    - Fill sessions by a time budget (derived, conservative)
    - Simple geographic grouping by suburb+street (not routing)
    """
    # Prepare jobs list
    jobs = df_in.copy()

    # Sort key
    urgency_order = {"Dark Blue": 0, "Light Blue": 2, "Flexible": 3}  # Requests handled later when available
    jobs["_urg_order"] = jobs["_urgency"].map(urgency_order).fillna(3)

    # Dark Blue tie-breaker: prioritize non-futile (rank 0) before futile1 (1) before futile2 (2)
    # We want rank ascending (0 best).
    jobs["_dark_tie"] = jobs.apply(lambda r: r["_futile_rank"] if r["_urgency"] == "Dark Blue" else 0, axis=1)

    # Geography key for light clustering
    geo_key_cols = []
    if cm["suburb"]:
        geo_key_cols.append(cm["suburb"])
    if cm["street"]:
        geo_key_cols.append(cm["street"])
    if geo_key_cols:
        jobs["_geo_key"] = jobs[geo_key_cols].astype(str).agg(" | ".join, axis=1)
    else:
        jobs["_geo_key"] = "Unknown"

    jobs = jobs.sort_values(
        by=["_urg_order", "_dark_tie", "_geo_key", "_mins"],
        ascending=[True, True, True, True]
    ).reset_index(drop=True)

    # Build session buckets
    buckets = {}  # {day: {AM: [], PM: []}}
    for d in WEEKDAYS[:5]:
        if not active_days.get(d, False):
            continue
        buckets[d] = {"AM": [], "PM": []}

    # Fill each session in day order
    remaining = jobs.to_dict(orient="records")

    def pop_next():
        return remaining.pop(0) if remaining else None

    for d in [wd for wd in WEEKDAYS[:5] if active_days.get(wd, False)]:
        for sess in ["AM", "PM"]:
            if not day_sessions[d][sess]["enabled"]:
                continue
            load = day_sessions[d][sess]["load"]
            budget = session_capacity_minutes(time_mode, global_times, day_override_times, d, sess, load)

            used = 0
            picked = []
            # conservative fill: stop when next item would exceed 110% of budget (tiny flexibility)
            while remaining:
                nxt = remaining[0]
                m = int(nxt.get("_mins", 15))
                if used + m <= int(budget * 1.10):
                    picked.append(pop_next())
                    used += m
                else:
                    break

            # Assign sequence numbers within session
            for i, job in enumerate(picked, start=1):
                job["_planned_day"] = d
                job["_planned_date"] = week_start + timedelta(days=WEEKDAYS.index(d))
                job["_planned_session"] = sess
                job["_planned_seq"] = i
            buckets[d][sess] = picked

    # Build output DF (completed schedule)
    planned_rows = []
    for d, sessions in buckets.items():
        for sess, items in sessions.items():
            for job in items:
                planned_rows.append(job)

    plan_df = pd.DataFrame(planned_rows) if planned_rows else pd.DataFrame(columns=list(jobs.columns) + ["_planned_date","_planned_session","_planned_seq"])

    return buckets, plan_df, remaining

# -----------------------------
# GO button: build plan + switch view
# -----------------------------
if go:
    # Gather active days + sessions data from sidebar state (already built)
    act = {d: st.session_state.get(f"day_{d}", False) for d in WEEKDAYS[:5]}
    sessions = {}
    for d in WEEKDAYS[:5]:
        if not act.get(d, False):
            continue
        sessions[d] = {
            "AM": {
                "enabled": st.session_state.get(f"{d}_am_on", True),
                "load": st.session_state.get(f"{d}_am_load", "Normal"),
            },
            "PM": {
                "enabled": st.session_state.get(f"{d}_pm_on", True),
                "load": st.session_state.get(f"{d}_pm_load", "Normal"),
            },
        }

    buckets, plan_df, remaining = build_week_plan(df_work, week_start, act, sessions, time_mode, global_times, day_override_times)

    st.session_state.plan = {
        "week_start": week_start,
        "active_days": act,
        "day_sessions": sessions,
        "time_mode": time_mode,
        "global_times": global_times,
        "day_override_times": day_override_times,
        "buckets": buckets,
        "remaining": remaining,
    }
    st.session_state.plan_df = plan_df
    st.session_state.view = "review"

# -----------------------------
# Review Screen
# -----------------------------
def badge(color, text):
    return f"""
    <span style="
        display:inline-block;
        padding:2px 8px;
        border-radius:999px;
        background:{color};
        color:white;
        font-size:12px;
        font-weight:700;
        margin-left:6px;
        ">
        {text}
    </span>
    """

URGENCY_COLORS = {
    "Dark Blue": "#1f4cff",
    "Light Blue": "#5aa9ff",
    "Flexible": "#9aa3af",
}

def render_job(job):
    urg = job.get("_urgency", "Flexible")
    col = URGENCY_COLORS.get(urg, "#9aa3af")
    seq = job.get("_planned_seq", "")
    label = job.get("_label", "Unknown address")
    mins = job.get("_mins", 0)
    return f"""
    <div style="display:flex;align-items:center;gap:10px;padding:6px 8px;border-bottom:1px dashed #e5e7eb;">
      <div style="
        width:26px;height:26px;border-radius:6px;
        background:{col};color:white;
        display:flex;align-items:center;justify-content:center;
        font-weight:800;
      ">{seq}</div>
      <div style="flex:1;">
        <div style="font-weight:700;">{label}</div>
        <div style="font-size:12px;opacity:0.7;">{urg} • est {mins} mins</div>
      </div>
    </div>
    """

if st.session_state.view == "review" and st.session_state.plan is not None:
    st.divider()
    plan = st.session_state.plan
    week_start = plan["week_start"]

    # Header row
    h1, h2, h3 = st.columns([2, 1, 1])
    with h1:
        st.subheader("Weekly Plan Review")
        st.caption(f"Week starting: {week_start.strftime('%a %d %b %Y')} • Plans are conservative. Overflow is expected.")
    with h2:
        if st.button("Back to Week Setup", use_container_width=True):
            st.session_state.view = "setup"
    with h3:
        plan_df = st.session_state.plan_df.copy()

        # Build export frame: merge planned fields back onto original columns
        export = plan_df.copy()

        export["planned_date"] = export.get("_planned_date")
        export["planned_session"] = export.get("_planned_session")
        export["planned_sequence"] = export.get("_planned_seq")

        # Keep original columns first
        original_cols = [c for c in df.columns if c in export.columns]
        export_cols = original_cols + ["planned_date", "planned_session", "planned_sequence"]
        export_final = export[export_cols].copy()

        xbytes = to_excel_bytes(export_final)
        st.download_button(
            "Export Completed Schedule",
            data=xbytes,
            file_name=f"flowboard_completed_{week_start.isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

    # Columns for active days
    active_day_list = [d for d in WEEKDAYS[:5] if plan["active_days"].get(d, False)]
    day_cols = st.columns(len(active_day_list)) if active_day_list else []

    for idx, d in enumerate(active_day_list):
        with day_cols[idx]:
            sessions = plan["day_sessions"][d]
            am_load = sessions["AM"]["load"] if sessions["AM"]["enabled"] else None
            pm_load = sessions["PM"]["load"] if sessions["PM"]["enabled"] else None

            st.markdown(f"### {d}")
            sub = []
            if sessions["AM"]["enabled"] and am_load:
                sub.append(f"AM {am_load}")
            if sessions["PM"]["enabled"] and pm_load:
                sub.append(f"PM {pm_load}")
            st.caption(" | ".join(sub) if sub else "No sessions enabled")

            # Reset button
            if st.button("Reset", key=f"reset_{d}", use_container_width=True):
                # return all jobs to remaining; clear buckets for this day
                # (conservative: don’t attempt re-fill automatically)
                for sess in ["AM", "PM"]:
                    plan["remaining"] = plan["buckets"][d][sess] + plan["remaining"]
                    plan["buckets"][d][sess] = []
                st.session_state.plan = plan
                st.experimental_rerun()

            # Render AM/PM boxes
            for sess in ["AM", "PM"]:
                if not sessions[sess]["enabled"]:
                    continue

                st.markdown(f"**{sess}**")
                items = plan["buckets"][d][sess]

                box = st.container(border=True)
                with box:
                    if not items:
                        st.caption("— empty —")
                    else:
                        # Ensure sequences are consistent
                        for i, job in enumerate(items, start=1):
                            job["_planned_seq"] = i

                        for i, job in enumerate(items):
                            st.markdown(render_job(job), unsafe_allow_html=True)

                        # Simple reorder controls (MVP-safe substitute for drag/drop)
                        st.caption("Reorder:")
                        for i in range(len(items)):
                            c_up, c_dn, c_sp = st.columns([1, 1, 6])
                            with c_up:
                                if st.button("↑", key=f"{d}_{sess}_up_{i}"):
                                    if i > 0:
                                        items[i-1], items[i] = items[i], items[i-1]
                                        st.experimental_rerun()
                            with c_dn:
                                if st.button("↓", key=f"{d}_{sess}_dn_{i}"):
                                    if i < len(items) - 1:
                                        items[i+1], items[i] = items[i], items[i+1]
                                        st.experimental_rerun()
                            with c_sp:
                                st.caption(f"Item {i+1}")

                st.write("")

    # Bottom summary bar
    st.divider()
    planned = st.session_state.plan_df
    dark_scheduled = int((planned["_urgency"] == "Dark Blue").sum()) if not planned.empty else 0
    dark_total = int((df_work["_urgency"] == "Dark Blue").sum())
    light_scheduled = int((planned["_urgency"] == "Light Blue").sum()) if not planned.empty else 0
    light_total = int((df_work["_urgency"] == "Light Blue").sum())
    st.markdown(
        f"""
        <div style="padding:10px 12px;border:1px solid #e5e7eb;border-radius:12px;">
          <b>Dark Blue scheduled:</b> {dark_scheduled} / {dark_total}
          &nbsp;&nbsp;|&nbsp;&nbsp;
          <b>Light Blue scheduled:</b> {light_scheduled} / {light_total}
          &nbsp;&nbsp;|&nbsp;&nbsp;
          <b>Overflow remaining:</b> Normal
        </div>
        """,
        unsafe_allow_html=True
    )
