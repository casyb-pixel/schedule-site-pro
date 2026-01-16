import streamlit as st
import pandas as pd
import datetime
import time
import json
import matplotlib 
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
import altair as alt

# --- 1. CONFIGURATION ---
st.set_page_config(
    page_title="ScheduleSite Pro",
    page_icon="https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/Untitled-design.png",
    layout="wide",
    initial_sidebar_state="expanded"
)

try:
    import extra_streamlit_components as stx
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False

# --- 2. CSS STYLING ---
st.markdown("""
    <style>
    .stApp { background-color: #f4f6f9; color: #000000 !important; }
    [data-testid="stSidebar"] { background-color: #2B588D; }
    [data-testid="stSidebar"] * { color: white !important; }
    
    .dashboard-card {
        background-color: white; padding: 20px; border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 15px;
        border-left: 5px solid #2B588D; color: black !important;
        height: 100%;
    }
    .card-title { color: #6c757d; font-size: 14px; font-weight: 600; text-transform: uppercase; margin-bottom: 5px; }
    .card-value { color: #2B588D; font-size: 24px; font-weight: bold; margin: 0; }
    
    .completion-banner {
        background-color: #d1ecf1; border-color: #bee5eb; color: #0c5460;
        padding: 15px; border-radius: 8px; text-align: center;
        font-size: 1.2rem; font-weight: bold; margin-bottom: 20px;
        border: 1px solid #bee5eb;
    }

    .stButton button { background-color: #2B588D !important; color: white !important; border-radius: 6px; border: none; }
    .stButton button:hover { background-color: #1e3f66 !important; }
    </style>
    """, unsafe_allow_html=True)

# --- 3. DATABASE ENGINE ---
@st.cache_resource
def get_engine():
    try:
        db_url = st.secrets["database"]["url"]
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
                try: return result.lastrowid
                except: return 1 
            return None
    except Exception as e:
        st.error(f"Database Error: {e}")
        return None

# --- ROBUST DB INIT ---
def init_db():
    with engine.begin() as conn:
        conn.execute(text('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, email TEXT,
            logo_data BYTEA, company_name TEXT, company_address TEXT, company_phone TEXT, 
            contractor_types TEXT, created_at TEXT, 
            pbp_status TEXT DEFAULT 'Inactive', scheduler_status TEXT DEFAULT 'Inactive'
        )'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY, user_id INTEGER, name TEXT, client_name TEXT,
            start_date TEXT, status TEXT DEFAULT 'Planning'
        )'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS subcontractors (
            id SERIAL PRIMARY KEY, user_id INTEGER, company_name TEXT, 
            contact_name TEXT, trade TEXT, phone TEXT, email TEXT
        )'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS delay_events (
            id SERIAL PRIMARY KEY, project_id INTEGER, reason TEXT,
            days_lost INTEGER, affected_task_ids TEXT, event_date TEXT,
            description TEXT, photo_data BYTEA
        )'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS task_library (
            id SERIAL PRIMARY KEY, contractor_type TEXT, phase TEXT, task_name TEXT
        )'''))
        conn.execute(text('''CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY, project_id INTEGER, phase TEXT, name TEXT, duration INTEGER, 
            start_date_override TEXT, exposure TEXT DEFAULT 'Outdoor', 
            material_lead_time INTEGER DEFAULT 0, inspection_required INTEGER DEFAULT 0, 
            dependencies TEXT, subcontractor_id INTEGER
        )'''))

init_db()

# --- 4. LOGIC & CALCULATION ---
def calculate_schedule_dates(tasks_df, project_start_date_str):
    if tasks_df.empty: return tasks_df
    
    tasks = tasks_df.to_dict('records')
    task_map = {t['id']: t for t in tasks}
    proj_start = datetime.datetime.strptime(project_start_date_str, '%Y-%m-%d').date()
    
    for t in tasks:
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
            if t['dependencies']: t['dep_list'] = [int(x) for x in json.loads(t['dependencies'])]
            else: t['dep_list'] = []
        except: t['dep_list'] = []

    changed = True
    iterations = 0
    while changed and iterations < len(tasks) * 2:
        changed = False
        iterations += 1
        for t in tasks:
            if not t['dep_list']:
                new_start = t['early_start']
            else:
                pred_finishes = []
                for pred_id in t['dep_list']:
                    if pred_id in task_map: pred_finishes.append(task_map[pred_id]['early_finish'])
                calc_start = max(pred_finishes) if pred_finishes else proj_start
                new_start = max(calc_start, t['early_start']) if t['is_constrained'] else calc_start
            
            if new_start != t['early_start']:
                t['early_start'] = new_start
                t['early_finish'] = new_start + datetime.timedelta(days=t['duration'])
                changed = True
    
    for t in tasks:
        t['start_date'] = t['early_start'].strftime('%Y-%m-%d')
        t['end_date'] = t['early_finish'].strftime('%Y-%m-%d')
        t['is_critical'] = False 

    if tasks:
        max_finish = max(t['early_finish'] for t in tasks)
        for t in tasks:
            if t['early_finish'] == max_finish: t['is_critical'] = True
            
    return pd.DataFrame(tasks)

# --- 5. POPUP DIALOGS ---
if hasattr(st, 'dialog'): dialog_decorator = st.dialog
elif hasattr(st, 'experimental_dialog'): dialog_decorator = st.experimental_dialog
else:
    def dialog_decorator(title):
        def wrapper(func):
            def inner(*args, **kwargs):
                st.subheader(title)
                func(*args, **kwargs)
            return inner
        return wrapper

@dialog_decorator("Task Editor")
def edit_task_popup(task_id, project_id, user_id):
    is_edit_mode = (task_id is not None)
    
    # Defaults
    t_data = {
        'name': "New Task", 'phase': 'General', 'duration': 1, 'start_date_override': None, 
        'subcontractor_id': 0, 'dependencies': "[]", 'exposure': "Outdoor"
    }

    if is_edit_mode:
        t_query = run_query("SELECT * FROM tasks WHERE id=:id", {"id": task_id})
        if not t_query.empty:
            t_data = t_query.iloc[0]
    
    # --- PHASE & TASK SELECTOR (Outside Form) ---
    st.markdown("#### 🏗️ Phase & Task Selection")
    
    lib_df = run_query("SELECT * FROM task_library")
    phases = []
    if not lib_df.empty:
        phases = sorted(lib_df['phase'].unique().tolist())
    
    c_ph, c_tk = st.columns(2)
    
    # 1. Select Phase
    curr_phase_idx = 0
    if t_data.get('phase') and t_data['phase'] in phases:
        curr_phase_idx = phases.index(t_data['phase'])
        
    sel_phase = c_ph.selectbox("Project Phase", ["Custom"] + phases, index=curr_phase_idx + 1 if phases else 0)
    
    # 2. Select Task
    avail_tasks = []
    if sel_phase != "Custom" and not lib_df.empty:
        avail_tasks = sorted(lib_df[lib_df['phase'] == sel_phase]['task_name'].unique().tolist())
        
    sel_task = c_tk.selectbox("Standard Task", ["Custom"] + avail_tasks)
    
    final_name_val = t_data['name']
    if sel_task != "Custom":
        final_name_val = sel_task
    
    # --- MAIN FORM ---
    with st.form("edit_form"):
        st.markdown("---")
        st.caption("Task Details")
        new_name = st.text_input("Display Name", value=final_name_val)
        
        c1, c2 = st.columns(2)
        new_dur = c1.number_input("Duration (Days)", value=t_data['duration'], min_value=1)
        
        d_val = None
        if t_data['start_date_override']:
             d_val = datetime.datetime.strptime(t_data['start_date_override'], '%Y-%m-%d')
        start_ov = c2.date_input("Manual Start Date", value=d_val)
        
        # Subs
        subs = run_query("SELECT id, company_name FROM subcontractors WHERE user_id=:u", {"u": user_id})
        sub_opts = {0: "Unassigned"}
        for _, s in subs.iterrows(): sub_opts[s['id']] = s['company_name']
        
        curr_sub = t_data['subcontractor_id'] if pd.notna(t_data['subcontractor_id']) else 0
        
        c3, c4 = st.columns(2)
        new_sub = c3.selectbox("Subcontractor", options=list(sub_opts.keys()), 
                               format_func=lambda x: sub_opts[x], 
                               index=list(sub_opts.keys()).index(curr_sub) if curr_sub in sub_opts else 0)
        
        exposure = c4.selectbox("Exposure", ["Indoor", "Outdoor"], index=0 if t_data['exposure'] == "Indoor" else 1)

        # Deps
        all_t = run_query("SELECT id, name FROM tasks WHERE project_id=:pid", {"pid": project_id})
        t_opts = {row['id']: row['name'] for _, row in all_t.iterrows()}
        curr_deps = []
        try: curr_deps = [int(x) for x in json.loads(t_data['dependencies'])]
        except: pass
        
        new_deps = st.multiselect("Predecessors", options=t_opts.keys(), format_func=lambda x: t_opts[x], default=[d for d in curr_deps if d in t_opts])
        
        st.markdown("---")
        col_save, col_del = st.columns([1, 1])
        with col_save:
            if st.form_submit_button("💾 Save Task", type="primary"):
                ov_str = str(start_ov) if start_ov else None
                dep_json = json.dumps(new_deps)
                final_phase = sel_phase if sel_phase != "Custom" else "General"
                
                if is_edit_mode:
                    execute_statement("""UPDATE tasks SET name=:n, phase=:p, duration=:d, start_date_override=:ov, subcontractor_id=:sub, dependencies=:dep, exposure=:e WHERE id=:id""", 
                        {"n": new_name, "p": final_phase, "d": new_dur, "ov": ov_str, "sub": new_sub, "dep": dep_json, "e": exposure, "id": task_id})
                else:
                    execute_statement("""INSERT INTO tasks (project_id, name, phase, duration, start_date_override, subcontractor_id, dependencies, exposure) 
                        VALUES (:pid, :n, :p, :d, :ov, :sub, :dep, :e)""",
                        {"pid": project_id, "n": new_name, "p": final_phase, "d": new_dur, "ov": ov_str, "sub": new_sub, "dep": dep_json, "e": exposure})
                
                st.session_state.active_popup = None
                st.rerun()
        
        with col_del:
            if is_edit_mode:
                if st.form_submit_button("🗑️ Delete Task"):
                    execute_statement("DELETE FROM tasks WHERE id=:id", {"id": task_id})
                    st.session_state.active_popup = None
                    st.rerun()

    # Quick Add Sub
    with st.expander("➕ Create New Subcontractor"):
        c_sub1, c_sub2 = st.columns(2)
        new_sub_name = c_sub1.text_input("Company Name", key="ns_name")
        new_sub_contact = c_sub2.text_input("Contact Name", key="ns_cont")
        if st.button("Quick Add Sub"):
            if new_sub_name:
                execute_statement("INSERT INTO subcontractors (user_id, company_name, contact_name) VALUES (:u, :c, :ct)",
                    {"u": user_id, "c": new_sub_name, "ct": new_sub_contact})
                st.success("Added!")
                time.sleep(0.5); st.rerun()

@dialog_decorator("⚠️ Report Delay")
def delay_popup(project_id):
    p_tasks = run_query("SELECT id, name, duration FROM tasks WHERE project_id=:pid", {"pid": project_id})
    task_map = {row['id']: f"{row['name']} ({row['duration']} days)" for _, row in p_tasks.iterrows()}
    
    tab1, tab2 = st.tabs(["Log Delay", "Manage Delays"])
    
    with tab1:
        with st.form("delay_form"):
            st.warning("Delays extend task duration.")
            c1, c2 = st.columns(2)
            reason = c1.selectbox("Cause", ["Weather", "Materials", "Contractor", "Inspection", "Other"])
            days = c2.number_input("Days Lost", min_value=1, value=1)
            event_date = st.date_input("Date", value=datetime.date.today())
            
            desc = st.text_area("Explanation")
            affected = st.multiselect("Affected Tasks", options=task_map.keys(), format_func=lambda x: task_map[x])
            
            if st.form_submit_button("Submit", type="primary"):
                if affected:
                    aff_json = json.dumps(affected)
                    execute_statement("""INSERT INTO delay_events (project_id, reason, days_lost, affected_task_ids, event_date, description) 
                        VALUES (:pid, :r, :d, :a, :date, :desc)""",
                        {"pid": project_id, "r": reason, "d": days, "a": aff_json, "date": str(event_date), "desc": desc})
                    for tid in affected:
                        execute_statement("UPDATE tasks SET duration = duration + :d WHERE id=:tid", {"d": days, "tid": tid})
                    st.success("Logged!"); st.session_state.active_popup = None; st.rerun()
                else: st.error("Select at least one task.")
    
    with tab2:
        delays = run_query("SELECT * FROM delay_events WHERE project_id=:pid ORDER BY event_date DESC", {"pid": project_id})
        if delays.empty:
            st.info("No delays logged.")
        else:
            for _, d in delays.iterrows():
                with st.expander(f"{d['event_date']} - {d['reason']} ({d['days_lost']} Days)"):
                    st.write(f"**Description:** {d['description']}")
                    st.caption("Deleting this will reverse the time addition.")
                    if st.button("Delete Delay", key=f"del_{d['id']}"):
                        try:
                            for tid in json.loads(d['affected_task_ids']):
                                execute_statement("UPDATE tasks SET duration = duration - :d WHERE id=:tid", {"d": d['days_lost'], "tid": int(tid)})
                        except: pass
                        execute_statement("DELETE FROM delay_events WHERE id=:id", {"id": d['id']})
                        st.success("Reverted!")
                        st.rerun()

# --- 6. AUTH & SESSION ---
if 'user_id' not in st.session_state: st.session_state.user_id = None
if 'active_popup' not in st.session_state: st.session_state.active_popup = None 
if 'editing_id' not in st.session_state: st.session_state.editing_id = None 
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
            st.rerun()

if st.session_state.user_id is None:
    st.image("https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/ScheduleSite-Pro-Logo.png", width=300)
    st.title("Login")
    tab1, tab2 = st.tabs(["Login", "Signup"])
    with tab1:
        u = st.text_input("Username").lower().strip()
        p = st.text_input("Password", type="password")
        if st.button("Login"):
            df = run_query("SELECT id, password FROM users WHERE LOWER(username)=:u", {"u": u})
            if not df.empty and df.iloc[0]['password'] == p:
                st.session_state.user_id = int(df.iloc[0]['id'])
                if COOKIE_MANAGER_AVAILABLE: cookie_manager.set("bb_user", u)
                st.rerun()
            else: st.error("Invalid credentials")
    with tab2:
        st.subheader("Create Account")
        new_u = st.text_input("New Username").lower().strip()
        new_p = st.text_input("New Password", type="password")
        new_email = st.text_input("Email (Optional)")
        if st.button("Create Account"):
            if new_u and new_p:
                check = run_query("SELECT id FROM users WHERE username=:u", {"u": new_u})
                if not check.empty: st.error("Username taken.")
                else:
                    execute_statement("INSERT INTO users (username, password, email, created_at, scheduler_status) VALUES (:u, :p, :e, :d, 'Trial')",
                                      {"u": new_u, "p": new_p, "e": new_email, "d": str(datetime.date.today())})
                    st.success("Account Created! Please Login.")
            else: st.error("Username and Password required.")
    st.stop()

# --- 7. MAIN LAYOUT ---
user_id = st.session_state.user_id

with st.sidebar:
    st.image("https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/ScheduleSite-Pro-Logo.png", use_container_width=True)
    if st.button("🏠 Dashboard", use_container_width=True): st.session_state.page = "Dashboard"; st.session_state.active_popup=None
    if st.button("➕ New Project", use_container_width=True): st.session_state.page = "New Project"
    if st.button("🗓️ Scheduler", use_container_width=True): st.session_state.page = "Scheduler"; st.session_state.active_popup=None
    if st.button("⚙️ Settings", use_container_width=True): st.session_state.page = "Settings"
    if st.button("🚪 Logout", use_container_width=True):
        if COOKIE_MANAGER_AVAILABLE: cookie_manager.delete("bb_user")
        st.session_state.clear(); st.rerun()

page = st.session_state.page

if page == "Dashboard":
    st.title("Command Center")
    proj_count = run_query("SELECT COUNT(*) FROM projects WHERE user_id=:uid", {"uid": user_id}).iloc[0,0]
    st.metric("Total Projects", proj_count)
    st.subheader("Your Projects")
    projs = run_query("SELECT * FROM projects WHERE user_id=:u ORDER BY id DESC", {"u": user_id})
    if not projs.empty: st.dataframe(projs[['name', 'client_name', 'start_date', 'status']], use_container_width=True)

elif page == "New Project":
    st.title("New Project")
    with st.form("new_p"):
        n = st.text_input("Name")
        c = st.text_input("Client Name")
        s = st.date_input("Start Date")
        if st.form_submit_button("Create", type="primary"):
            execute_statement("INSERT INTO projects (user_id, name, client_name, start_date) VALUES (:u, :n, :c, :s)", 
                              {"u": user_id, "n": n, "c": c, "s": str(s)})
            st.success("Created!"); st.session_state.page = "Scheduler"; st.rerun()

elif page == "Scheduler":
    st.title("Interactive Scheduler")
    my_projs = run_query("SELECT id, name FROM projects WHERE user_id=:u", {"u": user_id})
    
    if my_projs.empty:
        st.warning("No projects. Go to 'New Project' to start.")
    else:
        c1, c2 = st.columns([3, 2])
        with c1:
            sel_proj_name = st.selectbox("Select Project", my_projs['name'])
            pid = int(my_projs[my_projs['name'] == sel_proj_name].iloc[0]['id'])
        with c2:
            if st.button("➕ Add Task"):
                st.session_state.active_popup = 'add_task'; st.session_state.editing_id = None; st.rerun()
            if st.button("⚠️ Log Delay"):
                st.session_state.active_popup = 'delay'; st.rerun()

        p_data = run_query("SELECT * FROM projects WHERE id=:id", {"id": pid}).iloc[0]
        t_df = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": pid})
        t_df = calculate_schedule_dates(t_df, p_data['start_date'])
        delays = run_query("SELECT * FROM delay_events WHERE project_id=:pid", {"pid": pid})

        if not t_df.empty:
            max_end = pd.to_datetime(t_df['end_date']).max().date()
            st.markdown(f"""<div class="completion-banner">🏁 Projected Completion Date: {max_end.strftime('%B %d, %Y')}</div>""", unsafe_allow_html=True)

            t_df['Color'] = t_df.apply(lambda x: '#DAA520' if x.get('is_critical') else ('#2B588D' if x['exposure']=='Indoor' else '#E65100'), axis=1)
            
            # GANTT CHART
            base = alt.Chart(t_df).mark_bar(cornerRadius=5, height=20).encode(
                x=alt.X('start_date:T', title='Date'),
                x2='end_date:T',
                y=alt.Y('name:N', sort='start_date', title='Task'),
                row=alt.Row('phase:N', title='Phase', sort=['Pre-Construction', 'Site Preparation', 'Foundation', 'Framing']), 
                color=alt.Color('Color', scale=None),
                tooltip=['phase', 'name', 'start_date', 'end_date', 'duration']
            )
            final_chart = base
            
            # DELAY OVERLAY
            if not delays.empty:
                delay_rows = []
                for _, d in delays.iterrows():
                    try:
                        for tid in json.loads(d['affected_task_ids']):
                            task_info = t_df[t_df['id'] == int(tid)]
                            if not task_info.empty:
                                d_start = pd.to_datetime(d['event_date'])
                                d_end = d_start + datetime.timedelta(days=d['days_lost'])
                                delay_rows.append({'name': task_info.iloc[0]['name'], 'phase': task_info.iloc[0]['phase'], 
                                                   'start': d_start.strftime('%Y-%m-%d'), 'end': d_end.strftime('%Y-%m-%d'), 
                                                   'reason': d['reason']})
                    except: pass
                if delay_rows:
                    d_layer = alt.Chart(pd.DataFrame(delay_rows)).mark_bar(color='#D62728', opacity=0.8).encode(
                        x='start:T', x2='end:T', y='name:N', row='phase:N', tooltip=['reason']
                    )
                    final_chart = base + d_layer

            st.altair_chart(final_chart.interactive(), use_container_width=True)
            
            # DATA GRID
            st.markdown("### Task Details")
            editor_df = t_df[['id', 'phase', 'name', 'start_date', 'end_date', 'duration']].copy()
            editor_df['Edit'] = False
            
            edited_data = st.data_editor(editor_df, hide_index=True, key="gantt_grid", 
                                         disabled=["phase", "start_date", "end_date"],
                                         column_config={"Edit": st.column_config.CheckboxColumn(required=True)})
            
            # Grid Interactions
            t_to_edit = edited_data[edited_data['Edit'] == True]
            if not t_to_edit.empty:
                st.session_state.active_popup = 'edit_task'
                st.session_state.editing_id = int(t_to_edit.iloc[0]['id'])
                st.rerun()

            for i, row in edited_data.iterrows():
                orig = t_df[t_df['id'] == row['id']].iloc[0]
                if row['duration'] != orig['duration'] or row['name'] != orig['name']:
                    execute_statement("UPDATE tasks SET duration=:d, name=:n WHERE id=:id", {"d": row['duration'], "n": row['name'], "id": row['id']})
                    st.rerun()

        # --- MOVED POPUP LOGIC OUTSIDE THE IF STATEMENT ---
        # This ensures popups open even if t_df is empty (new project)
        if st.session_state.active_popup == 'add_task': edit_task_popup(None, pid, user_id)
        elif st.session_state.active_popup == 'edit_task': edit_task_popup(st.session_state.editing_id, pid, user_id)
        elif st.session_state.active_popup == 'delay': delay_popup(pid)

elif page == "Settings":
    st.title("Settings")
    user_data = run_query("SELECT company_name, contractor_types FROM users WHERE id=:uid", {"uid": user_id}).iloc[0]
    
    with st.form("set_f"):
        st.subheader("Company Profile")
        cn = st.text_input("Company Name", value=user_data['company_name'] if user_data['company_name'] else "")
        if st.form_submit_button("Save"):
            execute_statement("UPDATE users SET company_name=:n WHERE id=:u", {"n": cn, "u": user_id})
            st.success("Saved!")