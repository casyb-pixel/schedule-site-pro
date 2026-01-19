import streamlit as st
import pandas as pd
import datetime
import time
import json
import altair as alt
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

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
    .completion-banner {
        background-color: #d1ecf1; border-color: #bee5eb; color: #0c5460;
        padding: 15px; border-radius: 8px; text-align: center;
        font-size: 1.2rem; font-weight: bold; margin-bottom: 20px;
        border: 1px solid #bee5eb;
    }
    .stButton button { background-color: #2B588D !important; color: white !important; }
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

# --- 4. DB INIT (Safe) ---
def init_db():
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, email TEXT, created_at TEXT, company_name TEXT, scheduler_status TEXT DEFAULT 'Trial')"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS projects (id SERIAL PRIMARY KEY, user_id INTEGER, name TEXT, client_name TEXT, start_date TEXT, status TEXT DEFAULT 'Planning', project_type TEXT DEFAULT 'Residential')"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS subcontractors (id SERIAL PRIMARY KEY, user_id INTEGER, company_name TEXT, contact_name TEXT, trade TEXT)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS delay_events (id SERIAL PRIMARY KEY, project_id INTEGER, reason TEXT, days_lost INTEGER, affected_task_ids TEXT, event_date TEXT, description TEXT)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS task_library (id SERIAL PRIMARY KEY, contractor_type TEXT, phase TEXT, task_name TEXT)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS tasks (id SERIAL PRIMARY KEY, project_id INTEGER, phase TEXT, name TEXT, duration INTEGER, start_date_override TEXT, exposure TEXT DEFAULT 'Outdoor', material_lead_time INTEGER DEFAULT 0, inspection_required INTEGER DEFAULT 0, dependencies TEXT, subcontractor_id INTEGER)"))
        
        # Migrations
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN phase TEXT"))
        except: pass
        try: conn.execute(text("ALTER TABLE projects ADD COLUMN project_type TEXT DEFAULT 'Residential'"))
        except: pass

init_db()

# --- 5. LOGIC ---
def calculate_schedule_dates(tasks_df, project_start_date_str):
    if tasks_df.empty: return tasks_df
    tasks = tasks_df.to_dict('records')
    task_map = {t['id']: t for t in tasks}
    proj_start = datetime.datetime.strptime(project_start_date_str, '%Y-%m-%d').date()
    
    for t in tasks:
        t['early_start'] = datetime.datetime.strptime(t['start_date_override'], '%Y-%m-%d').date() if t.get('start_date_override') else proj_start
        t['early_finish'] = t['early_start'] + datetime.timedelta(days=t['duration'])
        try: t['dep_list'] = [int(x) for x in json.loads(t['dependencies'])]
        except: t['dep_list'] = []

    changed = True
    iterations = 0
    while changed and iterations < 100:
        changed = False
        iterations += 1
        for t in tasks:
            if not t['dep_list']: new_start = t['early_start']
            else:
                pred_finishes = []
                for pred_id in t['dep_list']:
                    if pred_id in task_map: pred_finishes.append(task_map[pred_id]['early_finish'])
                new_start = max(pred_finishes) if pred_finishes else proj_start
            
            if new_start > t['early_start']:
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

# --- 6. POPUPS ---
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
    is_edit = (task_id is not None)
    t_data = {'name': "New Task", 'phase': 'Pre-Construction', 'duration': 1, 'start_date_override': None, 'subcontractor_id': 0, 'dependencies': "[]", 'exposure': "Outdoor"}
    
    if is_edit:
        q = run_query("SELECT * FROM tasks WHERE id=:id", {"id": task_id})
        if not q.empty: t_data = q.iloc[0]

    # --- FETCH PROJECT TYPE ---
    try:
        p_q = run_query("SELECT project_type FROM projects WHERE id=:pid", {"pid": project_id})
        p_type = p_q.iloc[0]['project_type'] if not p_q.empty else 'Residential'
    except: p_type = 'Residential'

    # --- LIBRARY SELECTORS ---
    lib_df = run_query("SELECT * FROM task_library WHERE contractor_type IN ('All', :pt)", {"pt": p_type})
    phases = sorted(lib_df['phase'].unique().tolist()) if not lib_df.empty else []
    
    if not phases:
        st.error("⚠️ 'task_library' in Supabase is empty! Add tasks there first.")

    c_ph, c_tk = st.columns(2)
    curr_ph_idx = 0
    if t_data.get('phase') in phases: curr_ph_idx = phases.index(t_data['phase'])
    sel_phase = c_ph.selectbox("Phase", ["Custom"] + phases, index=curr_ph_idx + 1)
    
    avail_tasks = []
    if sel_phase != "Custom":
        avail_tasks = sorted(lib_df[lib_df['phase'] == sel_phase]['task_name'].unique().tolist())
    sel_task = c_tk.selectbox("Task", ["Custom"] + avail_tasks)
    
    final_name_val = t_data['name']
    if sel_task != "Custom": final_name_val = sel_task

    # --- FORM ---
    with st.form("task_f"):
        new_name = st.text_input("Task Name", value=final_name_val)
        c1, c2 = st.columns(2)
        dur = c1.number_input("Duration", value=t_data['duration'], min_value=1)
        d_val = datetime.datetime.strptime(t_data['start_date_override'], '%Y-%m-%d') if t_data['start_date_override'] else None
        start_ov = c2.date_input("Manual Start", value=d_val)
        
        # Subcontractor Selection
        subs = run_query("SELECT id, company_name FROM subcontractors WHERE user_id=:u", {"u": user_id})
        sub_opts = {0: "Unassigned"}
        for _, s in subs.iterrows(): sub_opts[s['id']] = s['company_name']
        
        curr_sub_idx = 0
        if t_data['subcontractor_id'] in sub_opts:
            curr_sub_idx = list(sub_opts.keys()).index(t_data['subcontractor_id'])
            
        st.write("**Subcontractor**")
        col_sub, col_add = st.columns([3, 1])
        with col_sub:
            sub_id = st.selectbox("Select Sub", options=list(sub_opts.keys()), format_func=lambda x: sub_opts[x], index=curr_sub_idx, label_visibility="collapsed")
        
        # Dependencies
        all_t = run_query("SELECT id, name FROM tasks WHERE project_id=:pid", {"pid": project_id})
        t_opts = {row['id']: row['name'] for _, row in all_t.iterrows()}
        curr_deps = []
        try: curr_deps = [int(x) for x in json.loads(t_data['dependencies'])]
        except: pass
        deps = st.multiselect("Predecessors", options=t_opts.keys(), format_func=lambda x: t_opts[x], default=[d for d in curr_deps if d in t_opts])
        
        c_save, c_del = st.columns([1,1])
        save_btn = c_save.form_submit_button("💾 Save", type="primary")
        del_btn = c_del.form_submit_button("🗑️ Delete")
        
        if save_btn:
            ov = str(start_ov) if start_ov else None
            dep_j = json.dumps(deps)
            ph = sel_phase if sel_phase != "Custom" else "General"
            
            if is_edit:
                execute_statement("UPDATE tasks SET name=:n, phase=:p, duration=:d, start_date_override=:ov, subcontractor_id=:s, dependencies=:dep WHERE id=:id",
                    {"n": new_name, "p": ph, "d": dur, "ov": ov, "s": sub_id, "dep": dep_j, "id": task_id})
            else:
                execute_statement("INSERT INTO tasks (project_id, name, phase, duration, start_date_override, subcontractor_id, dependencies) VALUES (:pid, :n, :p, :d, :ov, :s, :dep)",
                    {"pid": project_id, "n": new_name, "p": ph, "d": dur, "ov": ov, "s": sub_id, "dep": dep_j})
            st.session_state.active_popup = None; st.rerun()
            
        if del_btn:
            if is_edit: execute_statement("DELETE FROM tasks WHERE id=:id", {"id": task_id})
            st.session_state.active_popup = None; st.rerun()

    # --- QUICK ADD SUB (Outside Form) ---
    with st.expander("➕ Add New Subcontractor"):
        with st.form("new_sub_f"):
            ns_name = st.text_input("Company Name")
            ns_trade = st.text_input("Trade (e.g. Plumbing)")
            if st.form_submit_button("Add Sub"):
                if ns_name:
                    execute_statement("INSERT INTO subcontractors (user_id, company_name, trade) VALUES (:u, :n, :t)", 
                        {"u": user_id, "n": ns_name, "t": ns_trade})
                    st.success("Added! Re-open popup to select."); time.sleep(1); st.rerun()

@dialog_decorator("Log Delay")
def delay_popup(project_id, project_start_date):
    # 1. Recalculate Schedule Live to get accurate dates
    tasks_raw = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": project_id})
    tasks = calculate_schedule_dates(tasks_raw, project_start_date)
    
    with st.form("d_form"):
        st.info("Select the date first to filter active tasks.")
        date_input = st.date_input("Date of Delay", value=datetime.date.today())
        
        # 2. Filter tasks: Start <= Date <= End
        if not tasks.empty:
            # Convert to same type for comparison
            tasks['start_dt'] = pd.to_datetime(tasks['start_date']).dt.date
            tasks['end_dt'] = pd.to_datetime(tasks['end_date']).dt.date
            
            active_mask = (tasks['start_dt'] <= date_input) & (tasks['end_dt'] >= date_input)
            active_tasks = tasks[active_mask]
            
            # Fallback if no tasks active
            if active_tasks.empty:
                t_opts = {}
                st.warning(f"No tasks active on {date_input}. showing all.")
                t_opts = {row['id']: row['name'] for _, row in tasks.iterrows()}
            else:
                t_opts = {row['id']: row['name'] for _, row in active_tasks.iterrows()}
        else:
            t_opts = {}

        reason = st.selectbox("Reason", ["Weather", "Material", "Inspection", "Other"])
        days = st.number_input("Days Lost", min_value=1)
        
        aff = st.multiselect("Affected Tasks (Active on Date)", options=t_opts.keys(), format_func=lambda x: t_opts[x])
        
        if st.form_submit_button("Save Delay", type="primary"):
            if aff:
                execute_statement("INSERT INTO delay_events (project_id, reason, days_lost, affected_task_ids, event_date) VALUES (:pid, :r, :d, :a, :date)",
                    {"pid": project_id, "r": reason, "d": days, "a": json.dumps(aff), "date": str(date_input)})
                for tid in aff:
                    execute_statement("UPDATE tasks SET duration = duration + :d WHERE id=:tid", {"d": days, "tid": tid})
                st.session_state.active_popup = None; st.rerun()
            else: st.error("Select tasks")

    st.caption("Recent Delays")
    delays = run_query("SELECT * FROM delay_events WHERE project_id=:pid ORDER BY event_date DESC", {"pid": project_id})
    for _, d in delays.iterrows():
        if st.button(f"🗑️ Delete: {d['reason']} ({d['days_lost']}d)", key=f"dd_{d['id']}"):
            try:
                for tid in json.loads(d['affected_task_ids']):
                    execute_statement("UPDATE tasks SET duration = duration - :d WHERE id=:tid", {"d": d['days_lost'], "tid": tid})
            except: pass
            execute_statement("DELETE FROM delay_events WHERE id=:id", {"id": d['id']})
            st.rerun()

# --- 7. AUTH & MAIN ---
if 'user_id' not in st.session_state: st.session_state.user_id = None
if 'active_popup' not in st.session_state: st.session_state.active_popup = None
if 'editing_id' not in st.session_state: st.session_state.editing_id = None
if 'page' not in st.session_state: st.session_state.page = "Dashboard"

if COOKIE_MANAGER_AVAILABLE:
    cm = stx.CookieManager()
    time.sleep(0.1)
    if st.session_state.user_id is None and "bb_user" in cm.get_all():
        u = cm.get("bb_user")
        q = run_query("SELECT id FROM users WHERE username=:u", {"u": u})
        if not q.empty: st.session_state.user_id = int(q.iloc[0]['id']); st.rerun()

if not st.session_state.user_id:
    st.image("https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/ScheduleSite-Pro-Logo.png", width=300)
    tab1, tab2 = st.tabs(["Login", "Signup"])
    with tab1:
        u = st.text_input("User").strip()
        p = st.text_input("Pass", type="password")
        if st.button("Login"): 
            q = run_query("SELECT id, password FROM users WHERE LOWER(username)=:u", {"u": u.lower()})
            if not q.empty and q.iloc[0]['password'] == p:
                st.session_state.user_id = int(q.iloc[0]['id'])
                if COOKIE_MANAGER_AVAILABLE: cm.set("bb_user", u)
                st.rerun()
            else: st.error("Invalid")
    with tab2:
        new_u = st.text_input("New User").strip()
        new_p = st.text_input("New Pass", type="password")
        if st.button("Sign Up"):
            execute_statement("INSERT INTO users (username, password, created_at) VALUES (:u, :p, :d)", {"u": new_u, "p": new_p, "d": str(datetime.date.today())})
            st.success("Created! Login.")
    st.stop()

# --- APP CONTENT ---
with st.sidebar:
    st.image("https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/ScheduleSite-Pro-Logo.png", use_container_width=True)
    if st.button("🏠 Dashboard"): st.session_state.page = "Dashboard"; st.session_state.active_popup=None
    if st.button("➕ New Project"): st.session_state.page = "New Project"
    if st.button("🗓️ Scheduler"): st.session_state.page = "Scheduler"; st.session_state.active_popup=None
    if st.button("⚙️ Settings"): st.session_state.page = "Settings"
    if st.button("🚪 Logout"): 
        if COOKIE_MANAGER_AVAILABLE: cm.delete("bb_user")
        st.session_state.clear(); st.rerun()

if st.session_state.page == "Dashboard":
    st.title("Command Center")
    proj_count = run_query("SELECT COUNT(*) FROM projects WHERE user_id=:uid", {"uid": st.session_state.user_id}).iloc[0,0]
    st.metric("Total Projects", proj_count)
    st.subheader("Your Projects")
    projs = run_query("SELECT * FROM projects WHERE user_id=:u ORDER BY id DESC", {"u": st.session_state.user_id})
    if not projs.empty:
        cols_to_show = ['name', 'client_name', 'start_date']
        if 'project_type' in projs.columns: cols_to_show.insert(2, 'project_type')
        st.dataframe(projs[cols_to_show], use_container_width=True)

elif st.session_state.page == "New Project":
    st.title("Create Project")
    with st.form("np"):
        n = st.text_input("Name"); c = st.text_input("Client"); s = st.date_input("Start")
        pt = st.selectbox("Project Type", ["Residential", "Commercial"])
        if st.form_submit_button("Create", type="primary"):
            try:
                execute_statement("INSERT INTO projects (user_id, name, client_name, start_date, project_type) VALUES (:u, :n, :c, :s, :pt)", 
                    {"u": st.session_state.user_id, "n": n, "c": c, "s": str(s), "pt": pt})
                st.session_state.page = "Scheduler"; st.rerun()
            except Exception as e:
                st.error(f"Error creating project: {e}. Please go to Settings -> Fix Database Schema.")

elif st.session_state.page == "Scheduler":
    st.title("Scheduler")
    projs = run_query("SELECT id, name, client_name, start_date, project_type FROM projects WHERE user_id=:u", {"u": st.session_state.user_id})
    if projs.empty: st.warning("Create a project first."); st.stop()
    
    c1, c2 = st.columns([3, 1])
    sel_p_name = c1.selectbox("Project", projs['name'])
    
    # Get details for selected project
    curr_proj = projs[projs['name'] == sel_p_name].iloc[0]
    pid = int(curr_proj['id'])
    
    # --- EDIT PROJECT EXPANDER ---
    with st.expander(f"⚙️ Edit Project: {sel_p_name}"):
        with st.form("edit_p"):
            en = st.text_input("Project Name", value=curr_proj['name'])
            ec = st.text_input("Client", value=curr_proj['client_name'])
            es = st.date_input("Start Date", value=datetime.datetime.strptime(curr_proj['start_date'], '%Y-%m-%d'))
            # Safe status check (since column might not exist in old DBs)
            est = st.selectbox("Status", ["Planning", "In Progress", "Completed", "On Hold"])
            if st.form_submit_button("Update Project"):
                execute_statement("UPDATE projects SET name=:n, client_name=:c, start_date=:s, status=:st WHERE id=:id",
                    {"n": en, "c": ec, "s": str(es), "st": est, "id": pid})
                st.success("Project Updated!"); time.sleep(0.5); st.rerun()

    with c2:
        if st.button("➕ Add Task"): st.session_state.active_popup = 'add'; st.session_state.editing_id = None; st.rerun()
        if st.button("⚠️ Delay"): st.session_state.active_popup = 'delay'; st.rerun()

    # Data
    tasks = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": pid})
    tasks = calculate_schedule_dates(tasks, curr_proj['start_date'])
    
    if not tasks.empty:
        # COMPLETION BANNER
        final_date = pd.to_datetime(tasks['end_date']).max().strftime('%B %d, %Y')
        st.markdown(f'<div class="completion-banner">Estimated Completion: {final_date}</div>', unsafe_allow_html=True)
        
        # Chart
        tasks['Color'] = tasks.apply(lambda x: '#DAA520' if x.get('is_critical') else '#2B588D', axis=1)
        base = alt.Chart(tasks).mark_bar(cornerRadius=5).encode(
            x='start_date:T', x2='end_date:T', y='name:N', row='phase:N', color=alt.Color('Color', scale=None),
            tooltip=['phase', 'name', 'duration', 'start_date', 'end_date']
        ).interactive()
        
        # Delays
        delays = run_query("SELECT * FROM delay_events WHERE project_id=:pid", {"pid": pid})
        if not delays.empty:
            d_rows = []
            for _, d in delays.iterrows():
                try:
                    for tid in json.loads(d['affected_task_ids']):
                        t = tasks[tasks['id'] == int(tid)]
                        if not t.empty:
                            ds = pd.to_datetime(d['event_date'])
                            de = ds + datetime.timedelta(days=d['days_lost'])
                            # ALTAIR FIX: Ensure column names match 'base' chart for overlay to work
                            d_rows.append({
                                'name': t.iloc[0]['name'], 
                                'phase': t.iloc[0]['phase'], 
                                'start_date': ds.strftime('%Y-%m-%d'), 
                                'end_date': de.strftime('%Y-%m-%d')
                            })
                except: pass
            
            if d_rows:
                # Same X/X2/Y/Row encodings as Base
                over = alt.Chart(pd.DataFrame(d_rows)).mark_bar(color='#D62728', opacity=0.8).encode(
                    x='start_date:T', x2='end_date:T', y='name:N', row='phase:N'
                )
                base = base + over
        
        st.altair_chart(base, use_container_width=True)
        
        # Grid
        gd = st.data_editor(tasks[['id', 'phase', 'name', 'start_date', 'end_date', 'duration']], hide_index=True, key="g", disabled=["phase", "start_date", "end_date"])
        for i, row in gd.iterrows():
            orig = tasks[tasks['id'] == row['id']].iloc[0]
            if row['duration'] != orig['duration']:
                execute_statement("UPDATE tasks SET duration=:d WHERE id=:id", {"d": row['duration'], "id": row['id']}); st.rerun()

    if st.session_state.active_popup == 'add': edit_task_popup(None, pid, st.session_state.user_id)
    if st.session_state.active_popup == 'delay': delay_popup(pid, curr_proj['start_date'])

elif st.session_state.page == "Settings":
    st.title("Settings")
    user_data = run_query("SELECT company_name FROM users WHERE id=:uid", {"uid": st.session_state.user_id}).iloc[0]
    
    with st.form("set_f"):
        st.subheader("Profile")
        cn = st.text_input("Company", value=user_data['company_name'] if user_data['company_name'] else "")
        if st.form_submit_button("Save"):
            execute_statement("UPDATE users SET company_name=:n WHERE id=:u", {"n": cn, "u": st.session_state.user_id})
            st.success("Saved!")

    st.markdown("---")
    st.subheader("Database Maintenance")
    st.info("Run this if your Dashboard crashes or 'Project Type' is missing.")
    
    if st.button("🛠️ Fix Database Schema"):
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS project_type TEXT DEFAULT 'Residential'"))
                conn.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS phase TEXT"))
            st.success("Schema Repaired! You can now use the Dashboard.")
        except Exception as e:
            st.error(f"Migration Failed: {e}. Try running the SQL manually in Supabase.")