import streamlit as st
import pandas as pd
import datetime
import time
import json
import altair as alt
import matplotlib.pyplot as plt
import io
from fpdf import FPDF
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
    
    .metric-card {
        background-color: white; padding: 20px; border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center;
        border-top: 5px solid #2B588D; margin-bottom: 15px; height: 140px;
        display: flex; flex-direction: column; justify-content: center;
    }
    
    .task-list-card {
        background-color: white; padding: 15px; border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        border-top: 5px solid #2B588D; margin-bottom: 15px; height: 140px;
        overflow-y: auto; 
    }
    .task-list-item {
        font-size: 0.85rem; border-bottom: 1px solid #f0f0f0; padding: 4px 0; text-align: left;
    }
    
    .metric-value { font-size: 2.2rem; font-weight: 800; color: #2B588D; }
    .metric-label { font-size: 0.95rem; color: #555; text-transform: uppercase; letter-spacing: 1px; margin-top: 5px; }
    
    .alert-red { background-color: #ffebee; color: #c62828; padding: 12px; border-radius: 8px; border-left: 5px solid #c62828; margin-bottom: 8px; font-weight: 500; font-size: 0.9rem; }
    .alert-yellow { background-color: #fff8e1; color: #f57f17; padding: 12px; border-radius: 8px; border-left: 5px solid #f57f17; margin-bottom: 8px; font-weight: 500; font-size: 0.9rem; }
    
    .stButton button { background-color: #2B588D !important; color: white !important; font-weight: bold; border-radius: 6px; }
    text { font-family: sans-serif !important; }
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
        
        # Migrations
        for col in ['material_status', 'exposure', 'baseline_start_date', 'baseline_end_date', 'material_vendor', 'material_po', 'material_notes', 'material_order_date']:
            try: conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} TEXT"))
            except: pass
        for col in ['material_lead_time', 'inspection_required', 'percent_complete']:
            try: conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} INTEGER DEFAULT 0"))
            except: pass

init_db()

# --- 5. SCHEDULE LOGIC ---
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
    
    try: proj_start = pd.to_datetime(project_start_date_str).date()
    except: proj_start = datetime.date.today()
    
    # 1. FORWARD PASS
    for t in tasks:
        t['early_start'] = add_business_days(proj_start, 0, blocked_dates)
        if t.get('start_date_override'):
            try: t['early_start'] = pd.to_datetime(t['start_date_override']).date()
            except: pass
        t['early_finish'] = add_business_days(t['early_start'], t['duration'], blocked_dates)
        try: t['dep_list'] = [int(x) for x in json.loads(t['dependencies'])]
        except: t['dep_list'] = []

    for _ in range(len(tasks)): 
        changed = False
        for t in tasks:
            if not t['dep_list']: new_start = t['early_start']
            else:
                pred_finishes = []
                for pred_id in t['dep_list']:
                    if pred_id in task_map: pred_finishes.append(task_map[pred_id]['early_finish'])
                new_start = max(pred_finishes) if pred_finishes else proj_start
            
            new_start = add_business_days(new_start, 0, blocked_dates)
            if new_start > t['early_start']:
                t['early_start'] = new_start
                t['early_finish'] = add_business_days(new_start, t['duration'], blocked_dates)
                changed = True
        if not changed: break
    
    # 2. CRITICAL PATH
    max_finish = max(t['early_finish'] for t in tasks) if tasks else proj_start
    successors = {t['id']: [] for t in tasks}
    for t in tasks:
        t['is_critical'] = False 
        for dep in t['dep_list']:
            if dep in successors: successors[dep].append(t['id'])
    
    for t in tasks:
        if t['early_finish'] >= max_finish:
            t['is_critical'] = True
            
    sorted_tasks = sorted(tasks, key=lambda x: x['early_finish'], reverse=True)
    for t in sorted_tasks:
        for succ_id in successors[t['id']]:
            succ = task_map[succ_id]
            if succ['is_critical']:
                if t['early_finish'] >= succ['early_start']:
                    t['is_critical'] = True

    # 3. FINALIZE
    for t in tasks:
        t['start_date'] = t['early_start'].strftime('%Y-%m-%d')
        t['end_date'] = t['early_finish'].strftime('%Y-%m-%d')
        t['variance'] = 0
        if t.get('baseline_end_date'):
            try:
                base_end = pd.to_datetime(t['baseline_end_date']).date()
                t['variance'] = (t['early_finish'] - base_end).days
            except: pass
            
    return pd.DataFrame(tasks)

def capture_baseline(project_id, tasks_df):
    if tasks_df.empty: return
    try:
        with engine.begin() as conn:
            for _, row in tasks_df.iterrows():
                s_str = str(row['start_date'])
                e_str = str(row['end_date'])
                t_id = int(row['id'])
                conn.execute(
                    text("UPDATE tasks SET baseline_start_date=:s, baseline_end_date=:e WHERE id=:id"), 
                    {"s": s_str, "e": e_str, "id": t_id}
                )
        st.success("Baseline Captured Successfully!")
    except Exception as e:
        st.error(f"Database Error during capture: {e}")

# --- 6. PDF GENERATION LOGIC ---
class PDFReport(FPDF):
    def header(self):
        try:
            self.image('https://balanceandbuildconsulting.com/wp-content/uploads/2026/01/ScheduleSite-Pro-Logo.png', 10, 8, 33)
        except:
            self.set_font('Arial', 'B', 12)
            self.cell(33, 10, 'ScheduleSite Pro', 0, 0, 'C')
            
        self.set_font('Arial', 'B', 15)
        self.cell(80)
        self.cell(30, 10, 'Project Status Report', 0, 0, 'C')
        self.ln(20)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128)
        self.cell(0, 10, 'ScheduleSite Pro - Powered by Balance & Build Consulting', 0, 0, 'C')
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'R')

def generate_pdf_report(project, tasks_df, delay_df, upcoming_count, completion_date):
    pdf = PDFReport()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # 1. Project Info
    pdf.set_font("Arial", 'B', 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 10, f"Project: {project['name']}", 0, 1, 'L', fill=True)
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 8, f"Client: {project['client_name']}", 0, 1, 'L')
    pdf.cell(0, 8, f"Report Date: {datetime.date.today()}", 0, 1, 'L')
    pdf.ln(5)

    # 2. Key Metrics
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(95, 20, f"Est. Completion: {completion_date}", 1, 0, 'C')
    pdf.cell(95, 20, f"Tasks Starting (2 Wks): {upcoming_count}", 1, 1, 'C')
    pdf.ln(10)

    # 3. Charts
    completed = tasks_df[tasks_df['percent_complete'] == 100]['duration'].sum()
    in_prog = tasks_df[(tasks_df['percent_complete'] > 0) & (tasks_df['percent_complete'] < 100)]['duration'].sum()
    upcoming = tasks_df[tasks_df['percent_complete'] == 0]['duration'].sum()
    
    fig1, ax1 = plt.subplots(figsize=(4, 3))
    ax1.pie([completed, in_prog, upcoming], labels=['Completed', 'In Progress', 'Upcoming'], 
            colors=['#28a745', '#17a2b8', '#6c757d'], autopct='%1.1f%%', startangle=90)
    ax1.axis('equal')
    ax1.set_title("Progress (Time Allocated)")
    
    img_buf1 = io.BytesIO()
    plt.savefig(img_buf1, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig1)

    PHASE_ORDER = ["Pre-Construction", "Site Work", "Foundation", "Framing", "Exterior Building", "Interior Building", "Paving & Parking", "Final Systems and Testing", "Punchlist & Closeout"]
    tasks_df['phase_idx'] = tasks_df['phase'].apply(lambda x: PHASE_ORDER.index(x) if x in PHASE_ORDER else 99)
    phase_var = tasks_df.groupby('phase')['variance'].max().reset_index()
    phase_var['sort_idx'] = phase_var['phase'].apply(lambda x: PHASE_ORDER.index(x) if x in PHASE_ORDER else 99)
    phase_var = phase_var.sort_values('sort_idx')

    fig2, ax2 = plt.subplots(figsize=(5, 3))
    colors = ['#d9534f' if v > 0 else '#28a745' for v in phase_var['variance']]
    ax2.bar(phase_var['phase'], phase_var['variance'], color=colors)
    ax2.set_title("Schedule Variance by Phase")
    ax2.set_ylabel("Days Variance")
    plt.xticks(rotation=45, ha='right', fontsize=8)
    plt.tight_layout()

    img_buf2 = io.BytesIO()
    plt.savefig(img_buf2, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig2)

    pdf.image(img_buf1, x=10, y=pdf.get_y(), w=90)
    pdf.image(img_buf2, x=110, y=pdf.get_y(), w=90)
    pdf.ln(80) 

    # 4. Delay Table
    pdf.add_page()
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Delay Event Log", 0, 1, 'L')
    pdf.ln(5)

    if delay_df.empty:
        pdf.set_font("Arial", 'I', 10)
        pdf.cell(0, 10, "No delays recorded.", 0, 1, 'L')
    else:
        pdf.set_font("Arial", 'B', 10)
        pdf.set_fill_color(43, 88, 141)
        pdf.set_text_color(255)
        pdf.cell(30, 8, "Date", 1, 0, 'C', True)
        pdf.cell(30, 8, "Reason", 1, 0, 'C', True)
        pdf.cell(20, 8, "Days", 1, 0, 'C', True)
        pdf.cell(0, 8, "Notes / Affected", 1, 1, 'C', True)
        
        pdf.set_text_color(0)
        pdf.set_font("Arial", '', 9)
        for _, row in delay_df.iterrows():
            desc = row.get('description') or ""
            clean_desc = (desc[:75] + '...') if len(desc) > 75 else desc
            pdf.cell(30, 8, str(row['event_date']), 1, 0, 'C')
            pdf.cell(30, 8, str(row['reason']), 1, 0, 'C')
            pdf.cell(20, 8, str(row['days_lost']), 1, 0, 'C')
            pdf.cell(0, 8, clean_desc, 1, 1, 'L')

    # FIX: Return bytes directly, not encoded string
    return bytes(pdf.output())

# --- 7. POPUPS ---
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
    t_data = {'name': "New Task", 'phase': 'Pre-Construction', 'duration': 1, 'start_date_override': project_start_date, 'subcontractor_id': 0, 'dependencies': "[]", 'exposure': 'Outdoor', 'material_lead_time': 0, 'material_status': 'Not Ordered', 'inspection_required': 0, 'percent_complete': 0}

    if mode == 'edit' and selected_task_id is None:
        all_tasks_raw = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": project_id})
        if all_tasks_raw.empty: st.warning("No tasks to edit."); return
        pj_q = run_query("SELECT non_working_days FROM projects WHERE id=:pid", {"pid": project_id})
        blocked = pj_q.iloc[0]['non_working_days'] if not pj_q.empty else "[]"
        all_tasks_calc = calculate_schedule_dates(all_tasks_raw, project_start_date, blocked)
        all_tasks_calc = all_tasks_calc.sort_values(by=['start_date', 'id'])
        t_options = {row['id']: f"{row['start_date']} | {row['name']}" for _, row in all_tasks_calc.iterrows()}
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
    final_name_val = sel_task if sel_task != "Custom" else t_data['name']

    with st.form("task_f"):
        new_name = st.text_input("Task Name", value=final_name_val)
        c1, c2 = st.columns(2)
        dur = c1.number_input("Duration", value=t_data['duration'], min_value=1)
        d_val = None
        if t_data['start_date_override']:
            try: d_val = pd.to_datetime(t_data['start_date_override']).date()
            except: d_val = None
        start_ov = c2.date_input("Manual Start", value=d_val)

        c3, c4 = st.columns(2)
        exp_idx = 0 if t_data.get('exposure') == 'Outdoor' else 1
        exposure = c3.selectbox("Exposure", ["Outdoor", "Indoor"], index=exp_idx)
        lead_time = c4.number_input("Material Delivery Lead Time (Days)", value=t_data.get('material_lead_time', 0))
        
        c5, c6 = st.columns(2)
        insp = c5.checkbox("Inspection Required?", value=(t_data.get('inspection_required', 0) == 1))
        pct = c6.slider("% Complete", 0, 100, value=t_data.get('percent_complete', 0))

        subs = run_query("SELECT id, company_name FROM subcontractors WHERE user_id=:u", {"u": user_id})
        sub_opts = {0: "Unassigned"}
        for _, s in subs.iterrows(): sub_opts[s['id']] = s['company_name']
        curr_sub_idx = list(sub_opts.keys()).index(t_data['subcontractor_id']) if t_data['subcontractor_id'] in sub_opts else 0
        sub_id = st.selectbox("Subcontractor", options=list(sub_opts.keys()), format_func=lambda x: sub_opts[x], index=curr_sub_idx)
        
        all_t = run_query("SELECT id, name FROM tasks WHERE project_id=:pid", {"pid": project_id})
        t_opts = {row['id']: row['name'] for _, row in all_t.iterrows()}
        if selected_task_id and selected_task_id in t_opts: del t_opts[selected_task_id]
        
        curr_deps = []
        try: curr_deps = [int(x) for x in json.loads(t_data['dependencies'])]
        except: pass
        deps = st.multiselect("Predecessors", options=t_opts.keys(), format_func=lambda x: t_opts[x], default=[d for d in curr_deps if d in t_opts])
        
        if st.form_submit_button("💾 Save Task"):
            ov = str(start_ov) if start_ov else None
            dep_j = json.dumps(deps)
            ph = sel_phase if sel_phase != "Custom" else "General"
            insp_int = 1 if insp else 0
            
            current_stat = t_data.get('material_status')
            if not current_stat: current_stat = 'Not Ordered'
            final_mat_status = 'Delivered' if lead_time == 0 else current_stat

            if selected_task_id:
                execute_statement("UPDATE tasks SET name=:n, phase=:p, duration=:d, start_date_override=:ov, subcontractor_id=:s, dependencies=:dep, exposure=:ex, material_lead_time=:mlt, material_status=:ms, inspection_required=:ir, percent_complete=:pc WHERE id=:id",
                    {"n": new_name, "p": ph, "d": dur, "ov": ov, "s": sub_id, "dep": dep_j, "ex": exposure, "mlt": lead_time, "ms": final_mat_status, "ir": insp_int, "pc": pct, "id": selected_task_id})
            else:
                execute_statement("INSERT INTO tasks (project_id, name, phase, duration, start_date_override, subcontractor_id, dependencies, exposure, material_lead_time, material_status, inspection_required, percent_complete) VALUES (:pid, :n, :p, :d, :ov, :s, :dep, :ex, :mlt, :ms, :ir, :pc)",
                    {"pid": project_id, "n": new_name, "p": ph, "d": dur, "ov": ov, "s": sub_id, "dep": dep_j, "ex": exposure, "mlt": lead_time, "ms": final_mat_status, "ir": insp_int, "pc": pct})
            st.session_state.active_popup = None; st.rerun()

    if selected_task_id and st.button("✅ Mark 100% Complete"):
         execute_statement("UPDATE tasks SET percent_complete=100 WHERE id=:id", {"id": selected_task_id})
         st.rerun()

@dialog_decorator("Log Delay")
def delay_popup(project_id, project_start_date, blocked_dates_json):
    if 'delay_step' not in st.session_state: st.session_state.delay_step = 1
    if 'delay_temp' not in st.session_state: st.session_state.delay_temp = {}

    if st.session_state.delay_step == 1:
        st.write("### Step 1: Delay Details")
        with st.form("d_step1"):
            d_date = st.date_input("Date of Delay", value=datetime.date.today())
            d_reason = st.selectbox("Reason", ["Weather", "Material", "Inspection", "Other"])
            d_days = st.number_input("Days Lost", min_value=1, value=1)
            d_notes = st.text_area("Notes")
            if st.form_submit_button("Next: Find Affected Tasks"):
                st.session_state.delay_temp = {'date': d_date, 'reason': d_reason, 'days': d_days, 'notes': d_notes}
                st.session_state.delay_step = 2
                st.rerun()

    if st.session_state.delay_step == 2:
        st.write("### Step 2: Select Affected Tasks")
        dt_val = st.session_state.delay_temp['date']
        st.write(f"Showing tasks active on: **{dt_val}**")

        tasks_raw = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": project_id})
        tasks = calculate_schedule_dates(tasks_raw, project_start_date, blocked_dates_json)
        
        t_opts = {}
        if not tasks.empty:
            tasks['start_dt'] = pd.to_datetime(tasks['start_date']).dt.date
            tasks['end_dt'] = pd.to_datetime(tasks['end_date']).dt.date
            active = tasks[(tasks['start_dt'] <= dt_val) & (tasks['end_dt'] >= dt_val)]
            source = active if not active.empty else tasks
            t_opts = {row['id']: row['name'] for _, row in source.iterrows()}
        
        with st.form("d_step2"):
            aff = st.multiselect("Select Tasks to Push", options=t_opts.keys(), format_func=lambda x: t_opts[x])
            
            mitigation = False
            override = False
            if st.session_state.delay_temp['reason'] == "Weather" and aff:
                aff_subs = tasks[tasks['id'].isin(aff)]['subcontractor_id'].unique()
                aff_subs = [s for s in aff_subs if s != 0]
                indoor = tasks[(tasks['subcontractor_id'].isin(aff_subs)) & (tasks['exposure'] == 'Indoor') & (~tasks['id'].isin(aff))]
                if not indoor.empty:
                    mitigation = True
                    st.markdown("<div class='alert-red'>🛑 INDOOR TASK AVAILABLE: Reassign Sub?</div>", unsafe_allow_html=True)
                    for _, r in indoor.iterrows(): st.write(f"- {r['name']}")
                    override = st.checkbox("Force Delay (Ignore Mitigation)")

            if st.form_submit_button("✅ Confirm Delay"):
                if not aff: st.error("Please select at least one task.")
                elif mitigation and not override: st.error("Please review mitigation options above.")
                else:
                    meta = st.session_state.delay_temp
                    execute_statement("INSERT INTO delay_events (project_id, reason, days_lost, affected_task_ids, event_date, description) VALUES (:pid, :r, :d, :a, :date, :desc)",
                        {"pid": project_id, "r": meta['reason'], "d": meta['days'], "a": json.dumps(aff), "date": str(meta['date']), "desc": meta['notes']})
                    for tid in aff:
                        execute_statement("UPDATE tasks SET duration = duration + :d WHERE id=:tid", {"d": meta['days'], "tid": tid})
                    del st.session_state.delay_step
                    del st.session_state.delay_temp
                    st.session_state.active_popup = None; st.rerun()

        if st.button("⬅️ Back"):
            st.session_state.delay_step = 1; st.rerun()

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
                st.session_state.user_id = int(q.iloc[0]['id']); st.rerun()
            else: st.error("Invalid")
    with tab2:
        u = st.text_input("New User").strip()
        p = st.text_input("New Pass", type="password")
        if st.button("Sign Up"):
            execute_statement("INSERT INTO users (username, password, created_at) VALUES (:u, :p, :d)", {"u": u, "p": p, "d": str(datetime.date.today())})
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

    st.divider()
    sim_date = st.date_input("📆 Simulation Date", value=datetime.date.today(), help="Use this to test alerts for future project dates.")

if st.session_state.page == "Dashboard":
    st.title("Command Center")
    projs = run_query("SELECT * FROM projects WHERE user_id=:u ORDER BY id DESC", {"u": st.session_state.user_id})
    if projs.empty: st.info("No projects."); st.stop()
    sel_p_name = st.selectbox("Select Project", projs['name'])
    curr_proj = projs[projs['name'] == sel_p_name].iloc[0]
    pid = int(curr_proj['id'])
    p_blocked = curr_proj.get('non_working_days', '[]')
    
    if st.button("🚀 Launch Project (Reset Start)"): st.session_state.active_popup = 'launch_project'; st.rerun()

    tasks_raw = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": pid})
    tasks = calculate_schedule_dates(tasks_raw, curr_proj['start_date'], p_blocked)
    delays = run_query("SELECT * FROM delay_events WHERE project_id=:pid", {"pid": pid})
    
    if tasks.empty: st.warning("No tasks.")
    else:
        tab_dash, tab_wbs = st.tabs(["📊 Dashboard", "📋 WBS"])
        with tab_dash:
            final_date = pd.to_datetime(tasks['end_date']).max().strftime('%b %d, %Y')
            
            two_weeks_out = sim_date + datetime.timedelta(days=14)
            tasks['start_dt_obj'] = pd.to_datetime(tasks['start_date']).dt.date
            upcoming_tasks_df = tasks[ (tasks['start_dt_obj'] >= sim_date) & (tasks['start_dt_obj'] <= two_weeks_out) ]
            upcoming_count = len(upcoming_tasks_df)
            
            if upcoming_tasks_df.empty:
                list_html = "<div style='color:#999; font-style:italic; padding:10px;'>No upcoming tasks</div>"
            else:
                items = "".join([f"<div class='task-list-item'>• <strong>{row['name']}</strong> <br><span style='color:#888; font-size:0.8em; margin-left:10px;'>{row['start_date']}</span></div>" for _, row in upcoming_tasks_df.iterrows()])
                list_html = items

            c1, c2 = st.columns(2)
            c1.markdown(f"<div class='metric-card'><div class='metric-value'>{final_date}</div><div class='metric-label'>Completion</div></div>", unsafe_allow_html=True)
            c2.markdown(f"""
                <div class='task-list-card'>
                    <div class='metric-label' style='margin-bottom:8px; border-bottom:2px solid #eee; padding-bottom:5px;'>Starting (Next 14 Days)</div>
                    {list_html}
                </div>
            """, unsafe_allow_html=True)
            
            st.divider()
            
            completed_dur = tasks[tasks['percent_complete'] == 100]['duration'].sum()
            in_progress_dur = tasks[(tasks['percent_complete'] > 0) & (tasks['percent_complete'] < 100)]['duration'].sum()
            upcoming_dur = tasks[tasks['percent_complete'] == 0]['duration'].sum()
            
            pie_data = pd.DataFrame({
                'Status': ['Completed', 'In Progress', 'Upcoming'],
                'Duration': [completed_dur, in_progress_dur, upcoming_dur],
                'Color': ['#28a745', '#17a2b8', '#6c757d']
            })
            
            pie_chart = alt.Chart(pie_data).mark_arc(innerRadius=50).encode(
                theta=alt.Theta(field="Duration", type="quantitative"),
                color=alt.Color(field="Status", type="nominal", scale=alt.Scale(domain=['Completed', 'In Progress', 'Upcoming'], range=['#28a745', '#17a2b8', '#6c757d'])),
                tooltip=["Status", "Duration"]
            ).properties(title="Project Progress (Time Allocated)")

            PHASE_ORDER = ["Pre-Construction", "Site Work", "Foundation", "Framing", "Exterior Building", "Interior Building", "Paving & Parking", "Final Systems and Testing", "Punchlist & Closeout"]
            tasks['phase_idx'] = tasks['phase'].apply(lambda x: PHASE_ORDER.index(x) if x in PHASE_ORDER else 99)
            phase_var = tasks.groupby(['phase', 'phase_idx'])['variance'].max().reset_index()
            phase_var = phase_var.sort_values('phase_idx')
            phase_var['color'] = phase_var['variance'].apply(lambda x: '#d9534f' if x > 0 else '#28a745')
            
            var_chart = alt.Chart(phase_var).mark_bar().encode(
                x=alt.X('phase:N', sort=None, title='Phase', axis=alt.Axis(labelAngle=-45)),
                y=alt.Y('variance:Q', title='Days Behind (-) / Ahead (+)'),
                color=alt.Color('color', scale=None),
                tooltip=['phase', 'variance']
            ).properties(title="Schedule Variance by Phase")

            vc1, vc2 = st.columns(2)
            vc1.altair_chart(pie_chart, use_container_width=True)
            vc2.altair_chart(var_chart, use_container_width=True)
            
            st.divider()
            
            # --- FIX: Button Logic for PDF ---
            if st.button("📄 Generate PDF Report"):
                pdf_bytes = generate_pdf_report(curr_proj, tasks, delays, upcoming_count, final_date)
                st.session_state.pdf_data = pdf_bytes # Store in session state

            if 'pdf_data' in st.session_state:
                st.download_button("Click to Download Report", data=st.session_state.pdf_data, file_name=f"Report_{curr_proj['name']}_{datetime.date.today()}.pdf", mime='application/pdf')

            st.subheader(f"⚠️ Action Required (As of {sim_date})")
            
            alerts_found = False
            for _, t in tasks.iterrows():
                lead = t.get('material_lead_time') or 0
                status = t.get('material_status')
                if not status or status == 'None': status = 'Not Ordered'
                
                if lead > 0 and status not in ['Ordered', 'Delivered', 'Installed', 'N/A']:
                    start_dt = pd.to_datetime(t['start_date']).date()
                    must_order_by = start_dt - datetime.timedelta(days=lead)
                    days_until_drop_dead = (must_order_by - sim_date).days
                    
                    alert_html = None
                    if sim_date >= must_order_by:
                        alert_html = f"<div class='alert-red'>🔴 JEOPARDY: '{t['name']}' - Order IMMEDIATELY (Need {lead} days, starts {t['start_date']}).</div>"
                    elif sim_date >= (must_order_by - datetime.timedelta(days=14)):
                        alert_html = f"<div class='alert-yellow'>🟡 WARNING: '{t['name']}' - Order by {must_order_by} ({days_until_drop_dead} days left).</div>"
                    
                    if alert_html:
                        alerts_found = True
                        ac1, ac2 = st.columns([5, 1])
                        ac1.markdown(alert_html, unsafe_allow_html=True)
                        if ac2.button("📦 Mark Ordered", key=f"btn_ord_{t['id']}"):
                            st.session_state.active_popup = ('order_mat', t['id'])
                            st.rerun()

            if not alerts_found: st.success("✅ No material alerts.")

        with tab_wbs:
            PHASE_ORDER = ["Pre-Construction", "Site Work", "Foundation", "Framing", "Exterior Building", "Interior Building", "Paving & Parking", "Final Systems and Testing", "Punchlist & Closeout"]
            p_map = {p: i+1 for i, p in enumerate(PHASE_ORDER)}
            tasks['wbs_major'] = tasks['phase'].map(p_map).fillna(99).astype(int)
            tasks = tasks.sort_values(by=['wbs_major', 'start_date'])
            wbs = []
            cnt = {}
            for _, r in tasks.iterrows():
                m = r['wbs_major']
                if m not in cnt: cnt[m] = 0
                cnt[m] += 1
                wbs.append(f"{m}.{cnt[m]:02d}")
            tasks['WBS ID'] = wbs
            st.dataframe(tasks[['WBS ID', 'phase', 'name', 'duration', 'start_date', 'end_date', 'percent_complete', 'material_status']], use_container_width=True, hide_index=True)

    if st.session_state.active_popup == 'launch_project':
        @dialog_decorator("🚀 Launch Project")
        def launch_popup():
            new_start = st.date_input("Actual Start Date", value=datetime.date.today())
            if st.button("Confirm Launch", type="primary"):
                execute_statement("UPDATE projects SET start_date=:s, status='In Progress' WHERE id=:id", {"s": str(new_start), "id": pid})
                st.success("Launched!"); st.session_state.active_popup = None; st.rerun()
        launch_popup()

    if isinstance(st.session_state.active_popup, tuple) and st.session_state.active_popup[0] == 'order_mat':
        target_tid = st.session_state.active_popup[1]
        @dialog_decorator("📦 Mark Material Ordered")
        def order_popup(tid):
            t_name = run_query("SELECT name FROM tasks WHERE id=:id", {"id": tid}).iloc[0]['name']
            st.write(f"**Task:** {t_name}")
            with st.form("ord_f"):
                ord_date = st.date_input("Date Ordered", value=datetime.date.today())
                vend = st.text_input("Vendor / Supplier")
                po = st.text_input("PO Number")
                notes = st.text_area("Notes / Communication")
                if st.form_submit_button("✅ Confirm Order"):
                    execute_statement("""
                        UPDATE tasks 
                        SET material_status='Ordered', material_vendor=:v, material_po=:p, material_notes=:n, material_order_date=:d 
                        WHERE id=:id
                    """, {"v": vend, "p": po, "n": notes, "d": str(ord_date), "id": tid})
                    
                    st.success("Updated!")
                    st.session_state.active_popup = None
                    st.cache_resource.clear()
                    time.sleep(0.5) 
                    st.rerun()
        order_popup(target_tid)

elif st.session_state.page == "New Project":
    st.title("Create Project")
    with st.form("np"):
        n = st.text_input("Name"); c = st.text_input("Client"); s = st.date_input("Start"); pt = st.selectbox("Type", ["Residential", "Commercial"])
        if st.form_submit_button("Create"):
            execute_statement("INSERT INTO projects (user_id, name, client_name, start_date, project_type, non_working_days) VALUES (:u, :n, :c, :s, :pt, '[]')", {"u": st.session_state.user_id, "n": n, "c": c, "s": str(s), "pt": pt})
            st.session_state.page = "Scheduler"; st.rerun()

elif st.session_state.page == "Scheduler":
    st.title("Scheduler")
    projs = run_query("SELECT id, name, client_name, start_date, project_type, non_working_days FROM projects WHERE user_id=:u", {"u": st.session_state.user_id})
    if projs.empty: st.warning("Create a project."); st.stop()
    c1, c2 = st.columns([3, 1])
    sel_p_name = c1.selectbox("Project", projs['name'])
    curr_proj = projs[projs['name'] == sel_p_name].iloc[0]
    pid = int(curr_proj['id'])
    p_blocked = curr_proj.get('non_working_days', '[]')
    try: p_blocked_list = json.loads(p_blocked)
    except: p_blocked_list = []
    
    with c2:
        if st.button("➕ Add Task"): st.session_state.active_popup = 'add_task'; st.session_state.editing_id = None; st.rerun()
        if st.button("🖊️ Edit Task"): st.session_state.active_popup = 'edit_task'; st.session_state.editing_id = None; st.rerun()
        if st.button("⚠️ Log Delay"): st.session_state.active_popup = 'delay'; st.rerun()

    tasks = run_query("SELECT * FROM tasks WHERE project_id=:pid", {"pid": pid})
    tasks = calculate_schedule_dates(tasks, curr_proj['start_date'], p_blocked)
    
    if not tasks.empty:
        def get_color(row):
            if row.get('percent_complete', 0) == 100: return '#28a745' 
            if row.get('is_critical'): return '#DAA520' 
            return '#2B588D' 
            
        tasks['Color'] = tasks.apply(get_color, axis=1)
        
        PHASE_ORDER = ["Pre-Construction", "Site Work", "Foundation", "Framing", "Exterior Building", "Interior Building", "Paving & Parking", "Final Systems and Testing", "Punchlist & Closeout"]
        p_map = {p: i for i, p in enumerate(PHASE_ORDER)}
        tasks['phase_order'] = tasks['phase'].map(p_map).fillna(99)
        tasks = tasks.sort_values(by=['phase_order', 'start_date'])
        
        min_start = pd.to_datetime(tasks['start_date']).min()
        max_end = pd.to_datetime(tasks['end_date']).max()
        view_min = (min_start - datetime.timedelta(days=5)).strftime('%Y-%m-%d')
        view_max = (max_end + datetime.timedelta(days=5)).strftime('%Y-%m-%d')
        
        chart_height = max(400, len(tasks) * 30)
        
        base = alt.Chart(tasks).mark_bar(cornerRadius=3, height=20).encode(
            x=alt.X('start_date:T', scale=alt.Scale(domain=[view_min, view_max]), title='Date'),
            x2='end_date:T',
            y=alt.Y('name:N', sort=list(tasks['name']), title=None, axis=alt.Axis(labelLimit=300)), 
            color=alt.Color('Color', scale=None, legend=None),
            tooltip=['phase', 'name', 'start_date', 'end_date']
        ).properties(height=chart_height).interactive()
        
        st.altair_chart(base, use_container_width=True)
        st.caption("Green = Complete. Gold = Critical Path. Blue = Standard Task.")

        editor_df = tasks[['id', 'phase', 'name', 'start_date', 'end_date', 'percent_complete']].copy()
        edited_df = st.data_editor(
            editor_df,
            hide_index=True,
            use_container_width=True,
            key="scheduler_editor",
            column_config={
                "id": None, 
                "phase": st.column_config.TextColumn("Phase", disabled=True),
                "name": st.column_config.TextColumn("Task Name", disabled=True),
                "start_date": st.column_config.DateColumn("Start", disabled=True),
                "end_date": st.column_config.DateColumn("End", disabled=True),
                "percent_complete": st.column_config.NumberColumn(
                    "% Done", 
                    min_value=0, 
                    max_value=100, 
                    step=5,
                    format="%d%%",
                    help="Enter a value between 0 and 100"
                )
            }
        )

        if st.session_state.get("scheduler_editor") and st.session_state["scheduler_editor"].get("edited_rows"):
            updates = st.session_state["scheduler_editor"]["edited_rows"]
            for index, changes in updates.items():
                if "percent_complete" in changes:
                    try:
                        t_id = int(editor_df.iloc[index]['id'])
                        new_pct = int(changes['percent_complete'])
                        execute_statement("UPDATE tasks SET percent_complete=:pc WHERE id=:id", {"pc": new_pct, "id": t_id})
                    except: pass

    with st.expander(f"⚙️ Project Settings: {sel_p_name}"):
        with st.form("edit_p"):
            en = st.text_input("Name", value=curr_proj['name'])
            es = st.date_input("Start", value=datetime.datetime.strptime(curr_proj['start_date'], '%Y-%m-%d'))
            if st.form_submit_button("Update"):
                execute_statement("UPDATE projects SET name=:n, start_date=:s WHERE id=:id", {"n": en, "s": str(es), "id": pid}); st.rerun()
        
        st.write("**Non-Working Days (Holidays)**")
        c_h1, c_h2 = st.columns([2,1])
        new_hol = c_h1.date_input("Select Date", key="nh")
        if c_h2.button("Add"):
            if str(new_hol) not in p_blocked_list:
                p_blocked_list.append(str(new_hol))
                execute_statement("UPDATE projects SET non_working_days=:nw WHERE id=:id", {"nw": json.dumps(p_blocked_list), "id": pid}); st.rerun()
        
        for i, bd in enumerate(p_blocked_list):
            st.write(f"📅 {bd}")
            if st.button("Remove", key=f"rm_{i}"):
                p_blocked_list.pop(i)
                execute_statement("UPDATE projects SET non_working_days=:nw WHERE id=:id", {"nw": json.dumps(p_blocked_list), "id": pid}); st.rerun()
        
        st.divider()
        if st.button("📸 Capture Baseline"): capture_baseline(pid, tasks)
        if st.button("🗑️ Delete Project", type="secondary"):
            execute_statement("DELETE FROM tasks WHERE project_id=:pid", {"pid": pid})
            execute_statement("DELETE FROM projects WHERE id=:pid", {"pid": pid}); st.rerun()

    if st.session_state.active_popup == 'add_task': edit_task_popup('new', None, pid, st.session_state.user_id, curr_proj['start_date'])
    if st.session_state.active_popup == 'edit_task': edit_task_popup('edit', None, pid, st.session_state.user_id, curr_proj['start_date'])
    if st.session_state.active_popup == 'delay': delay_popup(pid, curr_proj['start_date'], p_blocked)

elif st.session_state.page == "Settings":
    st.title("Settings")
    user_data = run_query("SELECT company_name FROM users WHERE id=:uid", {"uid": st.session_state.user_id}).iloc[0]
    with st.form("set_f"):
        cn = st.text_input("Company", value=user_data['company_name'] if user_data['company_name'] else "")
        if st.form_submit_button("Save"):
            execute_statement("UPDATE users SET company_name=:n WHERE id=:u", {"n": cn, "u": st.session_state.user_id}); st.success("Saved!")