import streamlit as st
import pandas as pd
import datetime
import random
import string
import os
import time
import matplotlib
import io
import json
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
import altair as alt

# --- 1. CONFIGURATION & IMPORTS ---
matplotlib.use('Agg')

# Check for Optional Components
try:
    import extra_streamlit_components as stx
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False

# PAGE CONFIGURATION (Favicon & Title)
st.set_page_config(
    page_title="ScheduleSite Pro",
    page_icon="https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/Untitled-design.png",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 2. CSS STYLING (BRANDING) ---
st.markdown("""
    <style>
    .stApp { background-color: #f4f6f9; color: #000000 !important; }
    
    /* Sidebar Branding */
    [data-testid="stSidebar"] { background-color: #2B588D; }
    [data-testid="stSidebar"] * { color: white !important; }
    
    /* Dashboard Cards */
    .dashboard-card {
        background-color: white; padding: 20px; border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 15px;
        border-left: 5px solid #2B588D; color: black !important;
        height: 100%;
    }
    .card-title { color: #6c757d; font-size: 14px; font-weight: 600; text-transform: uppercase; margin-bottom: 5px; }
    .card-value { color: #2B588D; font-size: 24px; font-weight: bold; margin: 0; }
    
    /* Alerts */
    .alert-box {
        padding: 15px; border-radius: 8px; margin-bottom: 10px;
        border: 1px solid #eee; display: flex; align-items: center; gap: 10px;
    }
    .alert-warning { background-color: #fff3cd; border-left: 5px solid #ffc107; color: #856404; }
    .alert-danger { background-color: #f8d7da; border-left: 5px solid #dc3545; color: #721c24; }
    
    /* Buttons */
    .stButton button { background-color: #2B588D !important; color: white !important; border-radius: 6px; border: none; }
    .stButton button:hover { background-color: #1e3f66 !important; }
    </style>
    """, unsafe_allow_html=True)

# --- 3. DATABASE ENGINE & HELPERS ---
@st.cache_resource
def get_engine():
    # Attempt to get the URL from Streamlit secrets (Cloud)
    # If not found, fall back to local SQLite (for testing on your machine)
    try:
        db_url = st.secrets["database"]["url"]
        # Essential for Postgres to handle the connection pool correctly
        return create_engine(db_url, pool_pre_ping=True)
    except:
        return create_engine("sqlite:///local_scheduler.db", poolclass=NullPool)

engine = get_engine()

def run_query(query, params=None):
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(query), conn, params=params)
    except Exception as e:
        return pd.DataFrame()

def execute_statement(query, params=None):
    try:
        with engine.begin() as conn:
            result = conn.execute(text(query), params)
            if result.rowcount > 0 and 'INSERT' in query.upper():
                return result.lastrowid
            return None
    except Exception as e:
        st.error(f"Database Error: {e}")
        return None

def init_db():
    with engine.begin() as conn:
        # USERS TABLE
        conn.execute(text('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, 
            username TEXT UNIQUE, 
            password TEXT, 
            email TEXT,
            logo_data BYTEA, 
            company_name TEXT, 
            company_address TEXT, 
            company_phone TEXT, 
            created_at TEXT, 
            pbp_status TEXT DEFAULT 'Inactive', 
            scheduler_status TEXT DEFAULT 'Inactive'
        )'''))
        
        # PROJECTS TABLE
        conn.execute(text('''CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY, 
            user_id INTEGER, 
            name TEXT, 
            client_name TEXT,
            start_date TEXT, 
            status TEXT DEFAULT 'Planning'
        )'''))
        
        # TASKS TABLE
        conn.execute(text('''CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY, 
            project_id INTEGER, 
            name TEXT, 
            duration INTEGER, 
            start_date_override TEXT, 
            exposure TEXT DEFAULT 'Outdoor', 
            material_lead_time INTEGER DEFAULT 0, 
            material_status TEXT DEFAULT 'Not Ordered', 
            inspection_required INTEGER DEFAULT 0, 
            dependencies TEXT, 
            subcontractor_id INTEGER
        )'''))
        
        # SUBCONTRACTORS TABLE
        conn.execute(text('''CREATE TABLE IF NOT EXISTS subcontractors (
            id SERIAL PRIMARY KEY, 
            user_id INTEGER, 
            company_name TEXT, 
            contact_name TEXT, 
            trade TEXT, 
            phone TEXT, 
            email TEXT
        )'''))
        
        # WBS LIBRARY
        conn.execute(text('''CREATE TABLE IF NOT EXISTS wbs_library (
            id SERIAL PRIMARY KEY, 
            category TEXT, 
            json_structure TEXT
        )'''))
        
        # DELAY EVENTS
        conn.execute(text('''CREATE TABLE IF NOT EXISTS delay_events (
            id SERIAL PRIMARY KEY, 
            project_id INTEGER, 
            reason TEXT,
            days_lost INTEGER, 
            affected_task_ids TEXT, 
            event_date TEXT
        )'''))

        # MIGRATION STEPS (Safe to keep these)
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN dependencies TEXT"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN subcontractor_id INTEGER"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN start_date_override TEXT"))
        except: pass

init_db()

# --- 4. LOGIC FUNCTIONS ---
def seed_wbs_library():
    check = run_query("SELECT COUNT(*) FROM wbs_library")
    if not check.empty and check.iloc[0,0] == 0:
        residential_template = {
            "phases": [
                {"name": "Foundation", "exposure": "Outdoor", "tasks": [
                    {"name": "Excavation", "duration": 3},
                    {"name": "Pour Footings", "duration": 1, "inspection_required": 1},
                    {"name": "Cure Time", "duration": 7}
                ]},
                {"name": "Framing", "exposure": "Outdoor", "tasks": [
                    {"name": "First Floor Frame", "duration": 5},
                    {"name": "Sheathing", "duration": 3},
                    {"name": "Window Install", "duration": 3, "material_lead_time": 21},
                    {"name": "Framing Inspection", "duration": 1, "inspection_required": 1}
                ]}
            ]
        }
        execute_statement("INSERT INTO wbs_library (category, json_structure) VALUES (:cat, :json)", 
                          {"cat": "Residential", "json": json.dumps(residential_template)})
seed_wbs_library()

# --- CRITICAL PATH METHOD (CPM) ENGINE ---
def calculate_schedule_dates(tasks_df, project_start_date_str):
    if tasks_df.empty: return tasks_df
    
    tasks = tasks_df.to_dict('records')
    task_map = {t['id']: t for t in tasks}
    
    proj_start = datetime.datetime.strptime(project_start_date_str, '%Y-%m-%d').date()
    
    # 1. Initialize
    for t in tasks:
        # Check for Manual Override
        if t.get('start_date_override'):
             try:
                 t['early_start'] = datetime.datetime.strptime(t['start_date_override'], '%Y-%m-%d').date()
                 t['is_constrained'] = True
             except:
                 t['early_start'] = proj_start
                 t['is_constrained'] = False
        else:
            t['early_start'] = proj_start
            t['is_constrained'] = False
            
        t['early_finish'] = t['early_start'] + datetime.timedelta(days=t['duration'])
        
        try:
            if t['dependencies']:
                t['dep_list'] = json.loads(t['dependencies'])
                t['dep_list'] = [int(x) for x in t['dep_list']] 
            else:
                t['dep_list'] = []
        except:
            t['dep_list'] = []

    # 2. Forward Pass
    changed = True
    iterations = 0
    while changed and iterations < len(tasks) * 2:
        changed = False
        iterations += 1
        for t in tasks:
            # If constrained, do not push start date earlier, but allow push later if deps require
            if not t['dep_list']:
                new_start = t['early_start'] # Keep existing if constrained or proj_start
            else:
                pred_finishes = []
                for pred_id in t['dep_list']:
                    if pred_id in task_map:
                        pred_finishes.append(task_map[pred_id]['early_finish'])
                
                calc_start = max(pred_finishes) if pred_finishes else proj_start
                # Respect constraint if it exists (Start No Earlier Than)
                new_start = max(calc_start, t['early_start']) if t['is_constrained'] else calc_start
            
            if new_start != t['early_start']:
                t['early_start'] = new_start
                t['early_finish'] = new_start + datetime.timedelta(days=t['duration'])
                changed = True
    
    # 3. Finalize Data for Display
    for t in tasks:
        t['slack'] = 0 # Simplified CPM for display
        t['is_critical'] = False # Re-calc slack properly if needed
        t['start_date'] = t['early_start'].strftime('%Y-%m-%d')
        t['end_date'] = t['early_finish'].strftime('%Y-%m-%d')

    # Basic Critical Path Check (Longest Path)
    if tasks:
        max_finish = max(t['early_finish'] for t in tasks)
        for t in tasks:
            if t['early_finish'] == max_finish:
                t['is_critical'] = True
                
    return pd.DataFrame(tasks)

# --- 5. POPUP DIALOG (Task Editor) ---
# Using @st.experimental_dialog (or st.dialog in 1.34+)
if hasattr(st, 'dialog'):
    dialog_decorator = st.dialog
elif hasattr(st, 'experimental_dialog'):
    dialog_decorator = st.experimental_dialog
else:
    # Fallback for older streamlit
    def dialog_decorator(title):
        def wrapper(func):
            def inner(*args, **kwargs):
                st.subheader(title)
                func(*args, **kwargs)
            return inner
        return wrapper

@dialog_decorator("Task Details & Logic")
def edit_task_popup(task_id, project_id, user_id):
    # Fetch latest data
    t_data = run_query("SELECT * FROM tasks WHERE id=:id", {"id": task_id}).iloc[0]
    
    # Fetch Dependencies Options
    all_tasks = run_query("SELECT id, name FROM tasks WHERE project_id=:pid AND id!=:tid", 
                          {"pid": project_id, "tid": task_id})
    task_opts = {row['id']: row['name'] for _, row in all_tasks.iterrows()}
    
    # Fetch Subs
    subs = run_query("SELECT id, company_name FROM subcontractors WHERE user_id=:u", {"u": user_id})
    sub_opts = {0: "Unassigned"}
    for _, s in subs.iterrows():
        sub_opts[s['id']] = s['company_name']

    with st.form("popup_form"):
        c1, c2 = st.columns(2)
        new_name = c1.text_input("Task Name", value=t_data['name'])
        new_dur = c2.number_input("Duration (Days)", value=t_data['duration'], min_value=1)
        
        c3, c4 = st.columns(2)
        start_ov = c3.date_input("Manual Start Date (Optional)", 
                               value=datetime.datetime.strptime(t_data['start_date_override'], '%Y-%m-%d') if t_data['start_date_override'] else None)
        
        # Subcontractor
        curr_sub = t_data['subcontractor_id'] if pd.notna(t_data['subcontractor_id']) else 0
        new_sub = c4.selectbox("Subcontractor", options=list(sub_opts.keys()), 
                               format_func=lambda x: sub_opts[x], 
                               index=list(sub_opts.keys()).index(curr_sub) if curr_sub in sub_opts else 0)

        # Dependencies
        curr_deps = []
        if t_data['dependencies']:
            try: curr_deps = [int(x) for x in json.loads(t_data['dependencies'])]
            except: pass
        
        new_deps = st.multiselect("Predecessors (Must finish before this starts)", 
                                  options=task_opts.keys(), 
                                  format_func=lambda x: task_opts[x],
                                  default=[d for d in curr_deps if d in task_opts])
        
        st.markdown("---")
        st.caption("Materials & Logistics")
        m1, m2 = st.columns(2)
        lead_time = m1.number_input("Material Lead Time (Days)", value=t_data['material_lead_time'])
        exposure = m2.selectbox("Exposure", ["Indoor", "Outdoor"], index=0 if t_data['exposure'] == "Indoor" else 1)
        
        if st.form_submit_button("💾 Save Changes"):
            ov_str = str(start_ov) if start_ov else None
            dep_json = json.dumps(new_deps)
            execute_statement("""
                UPDATE tasks 
                SET name=:n, duration=:d, start_date_override=:ov, subcontractor_id=:sub, 
                    dependencies=:dep, material_lead_time=:m, exposure=:e
                WHERE id=:id
            """, {
                "n": new_name, "d": new_dur, "ov": ov_str, "sub": new_sub,
                "dep": dep_json, "m": lead_time, "e": exposure, "id": task_id
            })
            st.rerun()

# --- 6. SESSION & AUTH ---
if 'user_id' not in st.session_state: st.session_state.user_id = None
if 'username' not in st.session_state: st.session_state.username = ""
if 'page' not in st.session_state: st.session_state.page = "Dashboard"

if COOKIE_MANAGER_AVAILABLE:
    cookie_manager = stx.CookieManager()
    time.sleep(0.1)
    cookies = cookie_manager.get_all()
    if st.session_state.user_id is None and "bb_user" in cookies:
        u = cookies.get("bb_user")
        df = run_query("SELECT id, username FROM users WHERE username=:u", {"u": u})
        if not df.empty:
            st.session_state.user_id = int(df.iloc[0]['id'])
            st.session_state.username = df.iloc[0]['username']
            st.rerun()

# AUTH SCREEN
if st.session_state.user_id is None:
    st.image("https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/ScheduleSite-Pro-Logo.png", width=300)
    st.title("Login")
    tab1, tab2 = st.tabs(["Login", "Signup"])
    with tab1:
        u = st.text_input("Username").lower().strip()
        p = st.text_input("Password", type="password")
        if st.button("Login"):
            df = run_query("SELECT id, password FROM users WHERE username=:u", {"u": u})
            if not df.empty:
                st.session_state.user_id = int(df.iloc[0]['id'])
                st.session_state.username = u
                if COOKIE_MANAGER_AVAILABLE: cookie_manager.set("bb_user", u)
                st.rerun()
            else:
                st.error("Invalid user")
    with tab2:
        u_s = st.text_input("New Username").lower()
        p_s = st.text_input("New Password", type="password")
        if st.button("Create Account"):
            if u_s and p_s:
                execute_statement("INSERT INTO users (username, password, created_at, scheduler_status) VALUES (:u, :p, :d, 'Trial')",
                                  {"u": u_s, "p": p_s, "d": str(datetime.date.today())})
                st.success("Created! Please Login.")
    st.stop()

# --- 7. MAIN APP LAYOUT ---
user_id = st.session_state.user_id
username = st.session_state.username

with st.sidebar:
    st.image("https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/ScheduleSite-Pro-Logo.png", use_container_width=True)
    st.markdown("---")
    
    # Navigation Buttons
    if st.button("🏠 Dashboard", use_container_width=True): st.session_state.page = "Dashboard"
    if st.button("➕ New Project", use_container_width=True): st.session_state.page = "New Project"
    if st.button("🗓️ Scheduler", use_container_width=True): st.session_state.page = "Scheduler"
    if st.button("⚙️ Settings", use_container_width=True): st.session_state.page = "Settings"
    
    st.markdown("---")
    if st.button("🚪 Logout", use_container_width=True):
        if COOKIE_MANAGER_AVAILABLE: cookie_manager.delete("bb_user")
        st.session_state.clear()
        st.rerun()

page = st.session_state.page

# --- DASHBOARD ---
if page == "Dashboard":
    st.title("Command Center")
    
    proj_count = run_query("SELECT COUNT(*) FROM projects WHERE user_id=:uid", {"uid": user_id}).iloc[0,0]
    all_tasks = run_query("""
        SELECT t.*, p.start_date as proj_start, p.name as proj_name 
        FROM tasks t 
        JOIN projects p ON t.project_id = p.id 
        WHERE p.user_id=:uid
    """, {"uid": user_id})
    
    active_tasks_count = 0
    today = datetime.date.today()
    
    # Simple calculation for dashboard stats
    if not all_tasks.empty:
        for pid in all_tasks['project_id'].unique():
            p_tasks = all_tasks[all_tasks['project_id'] == pid].copy()
            p_start = p_tasks.iloc[0]['proj_start']
            p_tasks = calculate_schedule_dates(p_tasks, p_start)
            
            for _, t in p_tasks.iterrows():
                start = datetime.datetime.strptime(t['start_date'], '%Y-%m-%d').date()
                end = datetime.datetime.strptime(t['end_date'], '%Y-%m-%d').date()
                if start <= today <= end:
                    active_tasks_count += 1

    c1, c2, c3 = st.columns(3)
    with c1: st.markdown(f'<div class="dashboard-card"><div class="card-title">Projects</div><div class="card-value">{proj_count}</div></div>', unsafe_allow_html=True)
    with c2: st.markdown(f'<div class="dashboard-card"><div class="card-title">Active Tasks</div><div class="card-value">{active_tasks_count}</div></div>', unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="dashboard-card"><div class="card-title">Date</div><div class="card-value">{today.strftime("%b %d")}</div></div>', unsafe_allow_html=True)

    st.subheader("Recent Projects")
    projs = run_query("SELECT * FROM projects WHERE user_id=:u ORDER BY id DESC LIMIT 5", {"u": user_id})
    if not projs.empty:
        st.dataframe(projs[['name', 'client_name', 'start_date', 'status']], use_container_width=True)

# --- NEW PROJECT ---
elif page == "New Project":
    st.title("Start New Project")
    st.markdown("Configure your project parameters and select a template.")
    
    with st.container(border=True):
        with st.form("new_p"):
            c1, c2 = st.columns(2)
            n = c1.text_input("Project Name")
            c = c2.text_input("Client Name")
            s = st.date_input("Start Date")
            t_opts = run_query("SELECT category, json_structure FROM wbs_library")
            t_sel = st.selectbox("Template", ["Blank"] + t_opts['category'].tolist())
            
            if st.form_submit_button("Create Project", type="primary"):
                pid = execute_statement("INSERT INTO projects (user_id, name, client_name, start_date) VALUES (:u, :n, :c, :s)", 
                                        {"u": user_id, "n": n, "c": c, "s": str(s)})
                
                # Apply Template
                if pid and t_sel != "Blank":
                    tj = t_opts[t_opts['category'] == t_sel]['json_structure'].iloc[0]
                    td = json.loads(tj)
                    for phase in td['phases']:
                        for task in phase['tasks']:
                            execute_statement("""INSERT INTO tasks 
                            (project_id, name, duration, exposure, material_lead_time, inspection_required) 
                            VALUES (:pid, :n, :d, :e, :m, :i)""", 
                            {"pid": pid, "n": task['name'], "d": task['duration'], "e": phase['exposure'], 
                             "m": task.get('material_lead_time', 0), "i": task.get('inspection_required', 0)})
                
                st.success(f"Project '{n}' Created Successfully!")
                time.sleep(1)
                st.session_state.page = "Scheduler" # Redirect to scheduler
                st.rerun()

# --- SCHEDULER ---
elif page == "Scheduler":
    st.title("Interactive Scheduler")
    
    my_projs = run_query("SELECT id, name FROM projects WHERE user_id=:u", {"u": user_id})
    
    if my_projs.empty:
        st.warning("No projects found. Create one first!")
        if st.button("Go to New Project"):
            st.session_state.page = "New Project"
            st.rerun()
    else:
        # PROJECT SELECTOR
        c_sel, c_act = st.columns([3, 1])
        with c_sel:
            sel_proj_name = st.selectbox("Select Project", my_projs['name'], label_visibility="collapsed")
            pid = int(my_projs[my_projs['name'] == sel_proj_name].iloc[0]['id'])
        with c_act:
            if st.button("➕ Add Task"):
                execute_statement("INSERT INTO tasks (project_id, name, duration) VALUES (:pid, 'New Task', 1)", {"pid": pid})
                st.rerun()

        # FETCH & CALCULATE
        p_data = run_query("SELECT * FROM projects WHERE id=:id", {"id": pid}).iloc[0]
        t_df = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": pid})
        t_df = calculate_schedule_dates(t_df, p_data['start_date'])

        if t_df.empty:
            st.info("No tasks in this project yet.")
        else:
            # --- INTERACTIVE GANTT SECTION ---
            
            # 1. Prepare Data for Editor
            # We want users to be able to edit 'Duration' and 'Manual Start Override' directly
            # To make it user friendly, we present a subset df
            
            editor_df = t_df[['id', 'name', 'start_date', 'end_date', 'duration', 'exposure']].copy()
            editor_df['Edit'] = False # Checkbox for popups
            
            # 2. Split Layout: Chart on Top, Data Grid Below (or vice versa)
            
            # --- GANTT CHART (ALTAIR) ---
            # Dynamic colors based on exposure or critical path
            t_df['Color'] = t_df.apply(lambda x: '#DAA520' if x.get('is_critical', False) else ('#2B588D' if x['exposure']=='Indoor' else '#E65100'), axis=1)
            
            chart_data = t_df[['name', 'start_date', 'end_date', 'Color', 'duration']].copy()
            # Convert to datetime for Altair
            chart_data['Start'] = pd.to_datetime(chart_data['start_date'])
            chart_data['End'] = pd.to_datetime(chart_data['end_date'])
            
            chart = alt.Chart(chart_data).mark_bar(cornerRadius=5, height=25).encode(
                x=alt.X('Start', axis=alt.Axis(format='%b %d')),
                x2='End',
                y=alt.Y('name', sort='x', title=None),
                color=alt.Color('Color', scale=None),
                tooltip=['name', 'start_date', 'end_date', 'duration']
            ).properties(
                height=max(400, len(t_df)*35),
                title=f"Gantt Chart: {sel_proj_name}"
            ).interactive()
            
            st.altair_chart(chart, use_container_width=True)
            
            st.markdown("### ⚡ Live Task Editor")
            st.caption("Change **Duration** to resize bars. Double-click **Edit Details** to manage dependencies/materials.")
            
            # --- DATA EDITOR (The "Dynamic" part) ---
            # We allow editing Duration and Name directly. 
            # Note: Editing Start/End directly in grid is complex with CPM, so we allow Duration here
            # and full Date override in the popup.
            
            edited_data = st.data_editor(
                editor_df,
                column_config={
                    "Edit": st.column_config.CheckboxColumn("Edit Details", help="Check to open popup"),
                    "id": None, # Hide ID
                    "start_date": st.column_config.DateColumn("Start", disabled=True),
                    "end_date": st.column_config.DateColumn("End", disabled=True),
                    "duration": st.column_config.NumberColumn("Duration (Days)", min_value=1, required=True),
                    "name": "Task Name",
                    "exposure": st.column_config.SelectboxColumn("Exposure", options=["Indoor", "Outdoor"])
                },
                disabled=["start_date", "end_date"],
                hide_index=True,
                use_container_width=True,
                key="gantt_editor"
            )
            
            # CHECK FOR EDITS IN GRID
            # If duration/name/exposure changed in the grid, update DB immediately
            for index, row in edited_data.iterrows():
                # Compare with original t_df (inefficient but safe for small projects)
                orig = t_df[t_df['id'] == row['id']].iloc[0]
                if (row['duration'] != orig['duration'] or 
                    row['name'] != orig['name'] or 
                    row['exposure'] != orig['exposure']):
                    
                    execute_statement(
                        "UPDATE tasks SET duration=:d, name=:n, exposure=:e WHERE id=:id",
                        {"d": row['duration'], "n": row['name'], "e": row['exposure'], "id": row['id']}
                    )
                    st.rerun()

            # CHECK FOR POPUP REQUEST
            # If user checked "Edit", open the dialog
            t_to_edit = edited_data[edited_data['Edit'] == True]
            if not t_to_edit.empty:
                # Get the first one checked
                tid = int(t_to_edit.iloc[0]['id'])
                edit_task_popup(tid, pid, user_id)

elif page == "Settings":
    st.title("Settings")
    user_data = run_query("SELECT company_name FROM users WHERE id=:uid", {"uid": user_id}).iloc[0]
    
    with st.form("set_f"):
        st.subheader("Company Profile")
        cn = st.text_input("Company Name", value=user_data['company_name'] if user_data['company_name'] else "")
        if st.form_submit_button("Save Settings"):
            execute_statement("UPDATE users SET company_name=:n WHERE id=:u", {"n": cn, "u": user_id})
            st.success("Settings Saved!")
            time.sleep(1)
            st.rerun()

    st.markdown("---")
    st.subheader("Subcontractor Directory")
    
    # Simple Subcontractor Adder for convenience
    with st.expander("Add New Subcontractor"):
        with st.form("add_sub_set"):
            c1, c2 = st.columns(2)
            sn = c1.text_input("Company Name")
            sc = c2.text_input("Contact Name")
            st_rade = st.text_input("Trade")
            if st.form_submit_button("Add Subcontractor"):
                execute_statement("INSERT INTO subcontractors (user_id, company_name, contact_name, trade) VALUES (:u, :c, :ct, :t)",
                                  {"u": user_id, "c": sn, "ct": sc, "t": st_rade})
                st.success("Added")
                st.rerun()
                
    subs = run_query("SELECT company_name, contact_name, trade FROM subcontractors WHERE user_id=:u", {"u": user_id})
    if not subs.empty:
        st.dataframe(subs, use_container_width=True)