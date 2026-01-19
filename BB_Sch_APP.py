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
    
    /* DASHBOARD CARDS */
    .metric-card {
        background-color: white; padding: 20px; border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center;
        border-top: 4px solid #2B588D; margin-bottom: 10px;
    }
    .metric-value { font-size: 2rem; font-weight: bold; color: #2B588D; }
    .metric-label { font-size: 0.9rem; color: #666; text-transform: uppercase; }
    
    /* ALERTS */
    .alert-red {
        background-color: #f8d7da; color: #721c24; padding: 10px; border-radius: 5px; 
        border: 1px solid #f5c6cb; margin-bottom: 10px; font-weight: bold;
    }
    .alert-yellow {
        background-color: #fff3cd; color: #856404; padding: 10px; border-radius: 5px; 
        border: 1px solid #ffeeba; margin-bottom: 10px; font-weight: bold;
    }
    
    .suggestion-box {
        background-color: #d4edda; color: #155724; padding: 15px;
        border-radius: 8px; border: 1px solid #c3e6cb; margin-bottom: 10px;
    }
    
    .stButton button { background-color: #2B588D !important; color: white !important; }
    div[data-testid="stExpander"] details summary p { font-weight: bold; font-size: 1.1em; }
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
        conn.execute(text("CREATE TABLE IF NOT EXISTS projects (id SERIAL PRIMARY KEY, user_id INTEGER, name TEXT, client_name TEXT, start_date TEXT, status TEXT DEFAULT 'Planning', project_type TEXT DEFAULT 'Residential', non_working_days TEXT DEFAULT '[]')"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS subcontractors (id SERIAL PRIMARY KEY, user_id INTEGER, company_name TEXT, contact_name TEXT, trade TEXT, phone TEXT, email TEXT)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS delay_events (id SERIAL PRIMARY KEY, project_id INTEGER, reason TEXT, days_lost INTEGER, affected_task_ids TEXT, event_date TEXT, description TEXT)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS task_library (id SERIAL PRIMARY KEY, contractor_type TEXT, phase TEXT, task_name TEXT)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS tasks (id SERIAL PRIMARY KEY, project_id INTEGER, phase TEXT, name TEXT, duration INTEGER, start_date_override TEXT, exposure TEXT DEFAULT 'Outdoor', material_lead_time INTEGER DEFAULT 0, material_status TEXT DEFAULT 'Not Ordered', inspection_required INTEGER DEFAULT 0, percent_complete INTEGER DEFAULT 0, dependencies TEXT, subcontractor_id INTEGER, baseline_start_date TEXT, baseline_end_date TEXT)"))
        
        # Migrations (Auto-Healing)
        try: conn.execute(text("ALTER TABLE projects ADD COLUMN non_working_days TEXT DEFAULT '[]'"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN baseline_start_date TEXT"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN baseline_end_date TEXT"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN exposure TEXT DEFAULT 'Outdoor'"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN material_lead_time INTEGER DEFAULT 0"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN material_status TEXT DEFAULT 'Not Ordered'"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN inspection_required INTEGER DEFAULT 0"))
        except: pass
        try: conn.execute(text("ALTER TABLE tasks ADD COLUMN percent_complete INTEGER DEFAULT 0"))
        except: pass
        try: conn.execute(text("ALTER TABLE subcontractors ADD COLUMN phone TEXT"))
        except: pass
        try: conn.execute(text("ALTER TABLE subcontractors ADD COLUMN email TEXT"))
        except: pass

init_db()

# --- 5. SCHEDULE LOGIC (BUSINESS DAYS) ---
def add_business_days(start_date, days_to_add, blocked_dates_set):
    current_date = start_date
    while current_date.weekday() >= 5 or str(current_date) in blocked_dates_set:
        current_date += datetime.timedelta(days=1)
        
    if days_to_add == 0: return current_date

    days_added = 0
    while days_added < days_to_add:
        current_date += datetime.timedelta(days=1)
        if current_date.weekday() < 5 and str(current_date) not in blocked_dates_set:
            days_added += 1
            
    return current_date

def calculate_schedule_dates(tasks_df, project_start_date_str, blocked_dates_json="[]"):
    if tasks_df.empty: return tasks_df
    tasks = tasks_df.to_dict('records')
    task_map = {t['id']: t for t in tasks}
    
    try: blocked_dates = set(json.loads(blocked_dates_json or "[]"))
    except: blocked_dates = set()

    proj_start = datetime.datetime.strptime(project_start_date_str, '%Y-%m-%d').date()
    
    # Forward Pass
    for t in tasks:
        if t.get('start_date_override'):
            base_start = datetime.datetime.strptime(t['start_date_override'], '%Y-%m-%d').date()
        else:
            base_start = proj_start
            
        t['early_start'] = add_business_days(base_start, 0, blocked_dates)
        t['early_finish'] = add_business_days(t['early_start'], t['duration'], blocked_dates)
        
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
                
                if pred_finishes:
                    max_pred = max(pred_finishes)
                    new_start = max_pred
                else:
                    new_start = proj_start
            
            new_start = add_business_days(new_start, 0, blocked_dates)
            
            if new_start > t['early_start']:
                t['early_start'] = new_start
                t['early_finish'] = add_business_days(new_start, t['duration'], blocked_dates)
                changed = True
    
    max_finish = max(t['early_finish'] for t in tasks) if tasks else proj_start
    
    for t in tasks:
        t['start_date'] = t['early_start'].strftime('%Y-%m-%d')
        t['end_date'] = t['early_finish'].strftime('%Y-%m-%d')
        t['is_critical'] = (t['early_finish'] == max_finish)
        
        if t.get('baseline_end_date'):
            try:
                base_end = datetime.datetime.strptime(t['baseline_end_date'], '%Y-%m-%d').date()
                t['variance'] = (t['early_finish'] - base_end).days
            except: t['variance'] = 0
        else: t['variance'] = 0

    return pd.DataFrame(tasks)

def capture_baseline(project_id, tasks_df):
    if tasks_df.empty: return
    for _, row in tasks_df.iterrows():
        execute_statement(
            "UPDATE tasks SET baseline_start_date=:s, baseline_end_date=:e WHERE id=:id",
            {"s": row['start_date'], "e": row['end_date'], "id": row['id']}
        )

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

@dialog_decorator("Manage Task")
def edit_task_popup(mode, task_id_to_edit, project_id, user_id, project_start_date):
    selected_task_id = task_id_to_edit
    t_data = {
        'name': "New Task", 'phase': 'Pre-Construction', 'duration': 1, 
        'start_date_override': project_start_date, 'subcontractor_id': 0, 'dependencies': "[]", 
        'exposure': 'Outdoor', 'material_lead_time': 0, 'material_status': 'Not Ordered', 'inspection_required': 0, 'percent_complete': 0
    }

    if mode == 'edit' and selected_task_id is None:
        all_tasks = run_query("SELECT id, name, phase FROM tasks WHERE project_id=:pid ORDER BY phase, id", {"pid": project_id})
        if all_tasks.empty:
            st.warning("No tasks to edit."); return
        t_options = {row['id']: f"{row['phase']} - {row['name']}" for _, row in all_tasks.iterrows()}
        selected_task_id = st.selectbox("Select Task to Edit", options=t_options.keys(), format_func=lambda x: t_options[x])
        st.divider()

    if selected_task_id:
        q = run_query("SELECT * FROM tasks WHERE id=:id", {"id": selected_task_id})
        if not q.empty: t_data = q.iloc[0]

    try:
        p_q = run_query("SELECT project_type FROM projects WHERE id=:pid", {"pid": project_id})
        p_type = p_q.iloc[0]['project_type'] if not p_q.empty else 'Residential'
    except: p_type = 'Residential'

    lib_df = run_query("SELECT * FROM task_library WHERE contractor_type IN ('All', :pt)", {"pt": p_type})
    phases = sorted(lib_df['phase'].unique().tolist()) if not lib_df.empty else []
    
    if not phases: st.error("⚠️ Library empty!")

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

    with st.form("task_f"):
        new_name = st.text_input("Task Name", value=final_name_val)
        
        c1, c2 = st.columns(2)
        dur = c1.number_input("Duration (Days)", value=t_data['duration'], min_value=1)
        
        d_val = None
        if t_data['start_date_override']:
            try: d_val = datetime.datetime.strptime(str(t_data['start_date_override']), '%Y-%m-%d').date()
            except: d_val = None
        start_ov = c2.date_input("Manual Start (Optional)", value=d_val)

        # --- EXPOSURE & LEAD TIME ---
        c3, c4 = st.columns(2)
        exp_idx = 0 if t_data.get('exposure') == 'Outdoor' else 1
        exposure = c3.selectbox("Exposure", ["Outdoor", "Indoor"], index=exp_idx)
        lead_time = c4.number_input("Material Lead Time (Days)", value=t_data.get('material_lead_time', 0), min_value=0)
        
        # New: Material Status
        mat_opts = ["Not Ordered", "Ordered", "Delivered", "Installed"]
        curr_mat = t_data.get('material_status', 'Not Ordered')
        if curr_mat is None: curr_mat = 'Not Ordered'
        mat_idx = mat_opts.index(curr_mat) if curr_mat in mat_opts else 0
        mat_status = st.selectbox("Material Status", mat_opts, index=mat_idx)

        # --- INSPECTION & PERCENT COMPLETE ---
        st.write("---")
        c5, c6 = st.columns(2)
        insp_val = (t_data.get('inspection_required', 0) == 1)
        insp = c5.checkbox("Inspection Required?", value=insp_val)
        pct_val = t_data.get('percent_complete', 0)
        pct = c6.slider("Percent Complete (%)", 0, 100, value=pct_val)

        # -----------------------------
        
        subs = run_query("SELECT id, company_name FROM subcontractors WHERE user_id=:u", {"u": user_id})
        sub_opts = {0: "Unassigned"}
        for _, s in subs.iterrows(): sub_opts[s['id']] = s['company_name']
        
        curr_sub_idx = list(sub_opts.keys()).index(t_data['subcontractor_id']) if t_data['subcontractor_id'] in sub_opts else 0
        
        st.write("**Subcontractor**")
        sub_id = st.selectbox("Select Sub", options=list(sub_opts.keys()), format_func=lambda x: sub_opts[x], index=curr_sub_idx)
        
        all_t = run_query("SELECT id, name FROM tasks WHERE project_id=:pid", {"pid": project_id})
        t_opts = {row['id']: row['name'] for _, row in all_t.iterrows()}
        if selected_task_id and selected_task_id in t_opts: del t_opts[selected_task_id]

        curr_deps = []
        try: curr_deps = [int(x) for x in json.loads(t_data['dependencies'])]
        except: pass
        deps = st.multiselect("Predecessors", options=t_opts.keys(), format_func=lambda x: t_opts[x], default=[d for d in curr_deps if d in t_opts])
        
        c_save, c_del = st.columns([1,1])
        if c_save.form_submit_button("💾 Save Task", type="primary"):
            ov = str(start_ov) if start_ov else None
            dep_j = json.dumps(deps)
            ph = sel_phase if sel_phase != "Custom" else "General"
            insp_int = 1 if insp else 0
            
            if selected_task_id:
                execute_statement(
                    "UPDATE tasks SET name=:n, phase=:p, duration=:d, start_date_override=:ov, subcontractor_id=:s, dependencies=:dep, exposure=:ex, material_lead_time=:mlt, material_status=:ms, inspection_required=:ir, percent_complete=:pc WHERE id=:id",
                    {"n": new_name, "p": ph, "d": dur, "ov": ov, "s": sub_id, "dep": dep_j, "ex": exposure, "mlt": lead_time, "ms": mat_status, "ir": insp_int, "pc": pct, "id": selected_task_id}
                )
            else:
                execute_statement(
                    "INSERT INTO tasks (project_id, name, phase, duration, start_date_override, subcontractor_id, dependencies, exposure, material_lead_time, material_status, inspection_required, percent_complete) VALUES (:pid, :n, :p, :d, :ov, :s, :dep, :ex, :mlt, :ms, :ir, :pc)",
                    {"pid": project_id, "n": new_name, "p": ph, "d": dur, "ov": ov, "s": sub_id, "dep": dep_j, "ex": exposure, "mlt": lead_time, "ms": mat_status, "ir": insp_int, "pc": pct}
                )
            st.session_state.active_popup = None; st.rerun()
            
        if c_del.form_submit_button("🗑️ Delete Task"):
            execute_statement("DELETE FROM tasks WHERE id=:id", {"id": selected_task_id})
            st.session_state.active_popup = None; st.rerun()
            
    if selected_task_id:
        if st.button("✅ Mark as 100% Complete"):
             execute_statement("UPDATE tasks SET percent_complete=100 WHERE id=:id", {"id": selected_task_id})
             st.success("Task Complete!"); time.sleep(0.5); st.rerun()

    with st.expander("➕ Add New Subcontractor"):
        with st.form("new_sub_f"):
            ns_name = st.text_input("Company Name")
            ns_trade = st.text_input("Trade")
            ns_phone = st.text_input("Phone")
            ns_email = st.text_input("Email")
            if st.form_submit_button("Add Sub"):
                if ns_name:
                    execute_statement("INSERT INTO subcontractors (user_id, company_name, trade, phone, email) VALUES (:u, :n, :t, :p, :e)", 
                        {"u": user_id, "n": ns_name, "t": ns_trade, "p": ns_phone, "e": ns_email})
                    st.success("Added!"); time.sleep(1); st.rerun()

@dialog_decorator("Log Delay")
def delay_popup(project_id, project_start_date, blocked_dates_json):
    tasks_raw = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": project_id})
    tasks = calculate_schedule_dates(tasks_raw, project_start_date, blocked_dates_json)
    
    with st.form("d_form"):
        st.info("Select the date first to filter active tasks.")
        date_input = st.date_input("Date of Delay", value=datetime.date.today())
        
        # Filter Tasks Active on Date
        active_tasks = pd.DataFrame()
        if not tasks.empty:
            tasks['start_dt'] = pd.to_datetime(tasks['start_date']).dt.date
            tasks['end_dt'] = pd.to_datetime(tasks['end_date']).dt.date
            active_mask = (tasks['start_dt'] <= date_input) & (tasks['end_dt'] >= date_input)
            active_tasks = tasks[active_mask]
            
            t_opts = {row['id']: row['name'] for _, row in (active_tasks if not active_tasks.empty else tasks).iterrows()}
        else:
            t_opts = {}

        reason = st.selectbox("Reason", ["Weather", "Material", "Inspection", "Other"])
        days = st.number_input("Days Lost", min_value=1)
        aff = st.multiselect("Affected Tasks", options=t_opts.keys(), format_func=lambda x: t_opts[x])
        
        mitigation_warning = False
        override_checkbox = False
        
        if reason == "Weather" and aff:
            affected_subs = tasks[tasks['id'].isin(aff)]['subcontractor_id'].unique()
            affected_subs = [s for s in affected_subs if s != 0]
            
            if affected_subs:
                indoor_candidates = tasks[
                    (tasks['subcontractor_id'].isin(affected_subs)) & 
                    (tasks['exposure'] == 'Indoor') & 
                    (~tasks['id'].isin(aff))
                ]
                
                if not indoor_candidates.empty:
                    mitigation_warning = True
                    st.markdown("""
                        <div class='alert-red'>
                        🛑 MITIGATION OPPORTUNITY FOUND<br>
                        We noticed you are logging a WEATHER delay. The following subcontractors have INDOOR tasks available.
                        Instead of delaying the schedule, consider reassigning them:
                        </div>
                    """, unsafe_allow_html=True)
                    for _, row in indoor_candidates.iterrows():
                        st.write(f"- **{row['name']}** (Indoor) - Sub ID: {row['subcontractor_id']}")
                    
                    st.write("---")
                    override_checkbox = st.checkbox("I cannot reassign. Proceed with Delay.")

        submitted = st.form_submit_button("Save Delay", type="primary")
        
        if submitted:
            if not aff:
                st.error("Select tasks first.")
            elif mitigation_warning and not override_checkbox:
                st.error("Please review the mitigation suggestion above. Check the box to override if you must delay.")
            else:
                execute_statement("INSERT INTO delay_events (project_id, reason, days_lost, affected_task_ids, event_date) VALUES (:pid, :r, :d, :a, :date)",
                    {"pid": project_id, "r": reason, "d": days, "a": json.dumps(aff), "date": str(date_input)})
                for tid in aff:
                    execute_statement("UPDATE tasks SET duration = duration + :d WHERE id=:tid", {"d": days, "tid": tid})
                st.session_state.active_popup = None; st.rerun()

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
    if st.button("🏠 Command Center"): st.session_state.page = "Dashboard"; st.session_state.active_popup=None
    if st.button("➕ New Project"): st.session_state.page = "New Project"
    if st.button("🗓️ Scheduler"): st.session_state.page = "Scheduler"; st.session_state.active_popup=None
    if st.button("⚙️ Settings"): st.session_state.page = "Settings"
    if st.button("🚪 Logout"): 
        if COOKIE_MANAGER_AVAILABLE: cm.delete("bb_user")
        st.session_state.clear(); st.rerun()

# --- DASHBOARD: COMMAND CENTER ---
if st.session_state.page == "Dashboard":
    st.title("Command Center")
    projs = run_query("SELECT * FROM projects WHERE user_id=:u ORDER BY id DESC", {"u": st.session_state.user_id})
    
    if projs.empty:
        st.info("No projects yet. Click 'New Project' to start.")
        st.stop()
        
    sel_p_name = st.selectbox("Select Project to View", projs['name'])
    curr_proj = projs[projs['name'] == sel_p_name].iloc[0]
    pid = int(curr_proj['id'])
    p_blocked = curr_proj.get('non_working_days', '[]')
    
    c_launch, c_dummy = st.columns([1, 4])
    if c_launch.button("🚀 Launch Project (Reset Start)"):
        st.session_state.active_popup = 'launch_project'
        st.rerun()

    tasks_raw = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": pid})
    tasks = calculate_schedule_dates(tasks_raw, curr_proj['start_date'], p_blocked)
    delays = run_query("SELECT * FROM delay_events WHERE project_id=:pid", {"pid": pid})
    
    if tasks.empty:
        st.warning("No tasks in this project yet.")
    else:
        # --- TABS FOR VIEW ---
        tab_dash, tab_wbs = st.tabs(["📊 Dashboard Overview", "📋 WBS View"])
        
        with tab_dash:
            final_date = pd.to_datetime(tasks['end_date']).max().strftime('%b %d, %Y')
            total_delay_days = delays['days_lost'].sum() if not delays.empty else 0
            tasks_ahead = len(tasks[tasks['variance'] < 0]) if 'variance' in tasks.columns else 0
            
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"""<div class="metric-card"><div class="metric-value">{final_date}</div><div class="metric-label">Projected Completion</div></div>""", unsafe_allow_html=True)
            c2.markdown(f"""<div class="metric-card"><div class="metric-value">{total_delay_days} Days</div><div class="metric-label">Total Delays Logged</div></div>""", unsafe_allow_html=True)
            c3.markdown(f"""<div class="metric-card"><div class="metric-value" style="color:green">{tasks_ahead}</div><div class="metric-label">Tasks Ahead of Schedule</div></div>""", unsafe_allow_html=True)
            
            # --- ALERTS: MATERIALS & INSPECTIONS ---
            st.subheader("⚠️ Action Required")
            today = datetime.date.today()
            alerts = []

            for _, t in tasks.iterrows():
                # Material Alerts
                lead = t.get('material_lead_time', 0)
                status = t.get('material_status', 'Not Ordered')
                start_dt = pd.to_datetime(t['start_date']).date()
                end_dt = pd.to_datetime(t['end_date']).date()
                
                if lead > 0 and status == 'Not Ordered':
                    days_until_start = (start_dt - today).days
                    if days_until_start <= lead:
                        alerts.append(f"<div class='alert-red'>🔴 MATERIAL: Order for '{t['name']}' (Lead: {lead} days). Starts in {days_until_start} days!</div>")
                    elif days_until_start <= (lead + 7):
                        alerts.append(f"<div class='alert-yellow'>🟡 MATERIAL: Prep order for '{t['name']}'.</div>")
                
                # Inspection Alerts (End Date - 3 days)
                if t.get('inspection_required', 0) == 1 and t.get('percent_complete', 0) < 100:
                    days_until_finish = (end_dt - today).days
                    if days_until_finish <= 3:
                        alerts.append(f"<div class='alert-red'>🔴 INSPECTION: Schedule for '{t['name']}' (Ends in {days_until_finish} days).</div>")
                    elif days_until_finish <= 5:
                        alerts.append(f"<div class='alert-yellow'>🟡 INSPECTION: Upcoming for '{t['name']}'.</div>")

            if alerts:
                for a in alerts: st.markdown(a, unsafe_allow_html=True)
            else:
                st.success("✅ No immediate alerts.")

            if not delays.empty:
                st.subheader("Delay Analysis")
                dc1, dc2 = st.columns([1, 2])
                with dc1:
                    d_chart = alt.Chart(delays).mark_arc(innerRadius=50).encode(
                        theta=alt.Theta("days_lost", stack=True), color=alt.Color("reason"), tooltip=["reason", "days_lost"])
                    st.altair_chart(d_chart, use_container_width=True)
                with dc2:
                    st.dataframe(delays[['event_date', 'reason', 'days_lost', 'description']], use_container_width=True, hide_index=True)

            st.subheader("💡 Smart Suggestions")
            critical_tasks = tasks[tasks['is_critical'] == True]
            slack_tasks = tasks[tasks['is_critical'] == False]
            if not critical_tasks.empty and not slack_tasks.empty:
                crit_start_min = pd.to_datetime(critical_tasks['start_date']).min()
                crit_end_max = pd.to_datetime(critical_tasks['end_date']).max()
                resources_available = slack_tasks[(pd.to_datetime(slack_tasks['start_date']) >= crit_start_min) & (pd.to_datetime(slack_tasks['end_date']) <= crit_end_max)]
                if not resources_available.empty:
                    st.markdown('<div class="suggestion-box"><strong>Resource Reallocation Opportunity:</strong><br>Consider moving crew from these slack tasks to the Critical Path:</div>', unsafe_allow_html=True)
                    for _, row in resources_available.iterrows(): st.write(f"- Move from **{row['name']}**")
                else: st.info("No obvious reallocations found.")
            else: st.info("Add more tasks for suggestions.")

        with tab_wbs:
            st.subheader("Work Breakdown Structure (WBS)")
            PHASE_ORDER = {
                "Pre-Construction": 1, 
                "Site Work": 2, 
                "Foundation": 3, 
                "Framing": 4,
                "Exterior Building": 5, 
                "Interior Building": 6, 
                "Paving & Parking": 7,
                "Final Systems and Testing": 8, 
                "Punchlist & Closeout": 9
            }
            tasks['wbs_major'] = tasks['phase'].map(PHASE_ORDER).fillna(99).astype(int)
            tasks = tasks.sort_values(by=['wbs_major', 'start_date'])
            wbs_list = []
            phase_counters = {}
            for _, row in tasks.iterrows():
                major = row['wbs_major']
                if major not in phase_counters: phase_counters[major] = 0
                phase_counters[major] += 1
                wbs_list.append(f"{major}.{phase_counters[major]:02d}")
            tasks['WBS ID'] = wbs_list
            st.dataframe(tasks[['WBS ID', 'phase', 'name', 'duration', 'start_date', 'end_date', 'percent_complete', 'material_status']], use_container_width=True, hide_index=True)

    if st.session_state.active_popup == 'launch_project':
        @dialog_decorator("🚀 Launch Project")
        def launch_popup():
            st.write("This will reset the Project Start Date and recalculate the entire schedule.")
            new_start = st.date_input("Actual Start Date", value=datetime.date.today())
            if st.button("Confirm Launch", type="primary"):
                execute_statement("UPDATE projects SET start_date=:s, status='In Progress' WHERE id=:id", {"s": str(new_start), "id": pid})
                st.success("Project Launched!"); time.sleep(1); st.session_state.active_popup = None; st.rerun()
        launch_popup()

elif st.session_state.page == "New Project":
    st.title("Create Project")
    with st.form("np"):
        n = st.text_input("Name"); c = st.text_input("Client"); s = st.date_input("Start")
        pt = st.selectbox("Project Type", ["Residential", "Commercial"])
        if st.form_submit_button("Create", type="primary"):
            try:
                execute_statement("INSERT INTO projects (user_id, name, client_name, start_date, project_type, non_working_days) VALUES (:u, :n, :c, :s, :pt, '[]')", 
                    {"u": st.session_state.user_id, "n": n, "c": c, "s": str(s), "pt": pt})
                st.session_state.page = "Scheduler"; st.rerun()
            except Exception as e: st.error(f"Error: {e}")

elif st.session_state.page == "Scheduler":
    st.title("Scheduler")
    projs = run_query("SELECT id, name, client_name, start_date, project_type, non_working_days FROM projects WHERE user_id=:u", {"u": st.session_state.user_id})
    if projs.empty: st.warning("Create a project first."); st.stop()
    
    c1, c2 = st.columns([3, 1])
    sel_p_name = c1.selectbox("Project", projs['name'])
    curr_proj = projs[projs['name'] == sel_p_name].iloc[0]
    pid = int(curr_proj['id'])
    
    p_blocked_json = curr_proj.get('non_working_days', '[]')
    try: p_blocked_list = json.loads(p_blocked_json)
    except: p_blocked_list = []
    
    with st.expander(f"⚙️ Project Settings: {sel_p_name}"):
        tab_gen, tab_hol = st.tabs(["General", "Non-Working Days"])
        with tab_gen:
            with st.form("edit_p"):
                en = st.text_input("Project Name", value=curr_proj['name'])
                ec = st.text_input("Client", value=curr_proj['client_name'])
                es = st.date_input("Start Date", value=datetime.datetime.strptime(curr_proj['start_date'], '%Y-%m-%d'))
                est = st.selectbox("Status", ["Planning", "In Progress", "Completed", "On Hold"])
                if st.form_submit_button("Update Project Details"):
                    execute_statement("UPDATE projects SET name=:n, client_name=:c, start_date=:s, status=:st WHERE id=:id", {"n": en, "c": ec, "s": str(es), "st": est, "id": pid})
                    st.success("Project Updated!"); st.rerun()
            st.divider()
            c_del, c_base = st.columns(2)
            if c_del.button("🗑️ Delete Project", type="secondary"):
                execute_statement("DELETE FROM tasks WHERE project_id=:pid", {"pid": pid})
                execute_statement("DELETE FROM delay_events WHERE project_id=:pid", {"pid": pid})
                execute_statement("DELETE FROM projects WHERE id=:pid", {"pid": pid})
                st.success("Deleted."); time.sleep(1); st.rerun()
            if c_base.button("📸 Capture Baseline"):
                t_curr = calculate_schedule_dates(run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": pid}), curr_proj['start_date'], p_blocked_json)
                capture_baseline(pid, t_curr)
                st.success("Baseline Captured!")

        with tab_hol:
            st.write("Add days where NO WORK will happen (Holidays, Events). The schedule will automatically skip these.")
            c_h1, c_h2 = st.columns([2,1])
            new_hol = c_h1.date_input("Select Date", key="new_hol_date")
            if c_h2.button("Add Non-Working Day"):
                if str(new_hol) not in p_blocked_list:
                    p_blocked_list.append(str(new_hol))
                    execute_statement("UPDATE projects SET non_working_days=:nw WHERE id=:id", {"nw": json.dumps(p_blocked_list), "id": pid})
                    st.success("Added!"); time.sleep(0.5); st.rerun()
            if p_blocked_list:
                st.write("**Blocked Dates:**")
                for bd in p_blocked_list:
                    cb1, cb2 = st.columns([3,1])
                    cb1.write(bd)
                    if cb2.button("Remove", key=f"rem_{bd}"):
                        p_blocked_list.remove(bd)
                        execute_statement("UPDATE projects SET non_working_days=:nw WHERE id=:id", {"nw": json.dumps(p_blocked_list), "id": pid})
                        st.rerun()

    with c2:
        col_new, col_edit = st.columns(2)
        if col_new.button("➕ Add Task"): st.session_state.active_popup = 'add_task'; st.session_state.editing_id = None; st.rerun()
        if col_edit.button("🖊️ Edit Task"): st.session_state.active_popup = 'edit_task'; st.session_state.editing_id = None; st.rerun()
        if st.button("⚠️ Log Delay"): st.session_state.active_popup = 'delay'; st.rerun()

    tasks = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": pid})
    tasks = calculate_schedule_dates(tasks, curr_proj['start_date'], p_blocked_json)
    
    if not tasks.empty:
        tasks['Color'] = tasks.apply(lambda x: '#DAA520' if x.get('is_critical') else '#2B588D', axis=1)
        
        # --- FIXED CHART SCALING & FILTERING ---
        min_start = pd.to_datetime(tasks['start_date']).min()
        max_end = pd.to_datetime(tasks['end_date']).max()
        
        # Pad dates slightly for visuals
        view_min = (min_start - datetime.timedelta(days=2)).strftime('%Y-%m-%d')
        view_max = (max_end + datetime.timedelta(days=2)).strftime('%Y-%m-%d')

        base = alt.Chart(tasks).mark_bar(cornerRadius=5).encode(
            x=alt.X('start_date:T', scale=alt.Scale(domain=[view_min, view_max])),
            x2='end_date:T',
            y='name:N',
            row='phase:N',
            color=alt.Color('Color', scale=None),
            tooltip=[
                alt.Tooltip('phase', title='Phase'),
                alt.Tooltip('name', title='Task'),
                alt.Tooltip('start_date', title='Start', format='%Y-%m-%d'),
                alt.Tooltip('end_date', title='End', format='%Y-%m-%d'),
                alt.Tooltip('duration', title='Days'),
                alt.Tooltip('percent_complete', title='% Done')
            ]
        ).interactive()
        st.altair_chart(base, use_container_width=True)
        st.data_editor(tasks[['phase', 'name', 'start_date', 'end_date', 'duration']], hide_index=True, disabled=True)

    if st.session_state.active_popup == 'add_task': edit_task_popup('new', None, pid, st.session_state.user_id, curr_proj['start_date'])
    if st.session_state.active_popup == 'edit_task': edit_task_popup('edit', None, pid, st.session_state.user_id, curr_proj['start_date'])
    if st.session_state.active_popup == 'delay': delay_popup(pid, curr_proj['start_date'], p_blocked_json)

elif st.session_state.page == "Settings":
    st.title("Settings")
    user_data = run_query("SELECT company_name FROM users WHERE id=:uid", {"uid": st.session_state.user_id}).iloc[0]
    with st.form("set_f"):
        cn = st.text_input("Company", value=user_data['company_name'] if user_data['company_name'] else "")
        if st.form_submit_button("Save"):
            execute_statement("UPDATE users SET company_name=:n WHERE id=:u", {"n": cn, "u": st.session_state.user_id})
            st.success("Saved!")