import os
import uuid
import yaml
import pandas as pd
import re
import hashlib
import threading
import streamlit as st
from google import genai
from google.genai import types
import snowflake.connector
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# ─── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="IPL NLP to SQL",
    page_icon="🏏",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── Session State ────────────────────────────────────────────────────────────
if 'db_schema'       not in st.session_state: st.session_state.db_schema       = None
if 'admin_logged_in' not in st.session_state: st.session_state.admin_logged_in = False
if 'current_page'    not in st.session_state: st.session_state.current_page    = "app"
if 'session_id'      not in st.session_state: st.session_state.session_id      = str(uuid.uuid4())[:8]

# ══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE LAYER
# All expensive resources cached once, reused forever
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource
def load_credentials():
    """Cached once — never re-reads .env on reruns."""
    creds = {
        'gemini_api_key':      os.getenv("GEMINI_API_KEY", ""),
        'snowflake_user':      os.getenv("SNOWFLAKE_USER", ""),
        'snowflake_password':  os.getenv("SNOWFLAKE_PASSWORD", ""),
        'snowflake_account':   os.getenv("SNOWFLAKE_ACCOUNT", ""),
        'snowflake_warehouse': os.getenv("SNOWFLAKE_WAREHOUSE", ""),
        'snowflake_database':  os.getenv("SNOWFLAKE_DATABASE", ""),
        'snowflake_schema':    os.getenv("SNOWFLAKE_SCHEMA", ""),
        'schema_file':         os.getenv("SCHEMA_FILE_PATH", ""),
        'admin_password_hash': os.getenv("ADMIN_PASSWORD_HASH", ""),
    }
    missing = [k for k, v in creds.items() if not v and k != 'admin_password_hash']
    return creds, missing


@st.cache_resource
def get_snowflake_connection(user, password, account, warehouse, database, schema):
    """
    FIX 1: Single persistent connection shared across all reruns.
    No more opening/closing a new connection on every query.
    """
    conn = snowflake.connector.connect(
        user=user, password=password, account=account,
        warehouse=warehouse, database=database, schema=schema,
        client_session_keep_alive=True   # keeps connection alive between queries
    )
    return conn


def get_conn(creds):
    """Returns the cached persistent connection, reconnects if dropped."""
    conn = get_snowflake_connection(
        creds['snowflake_user'], creds['snowflake_password'],
        creds['snowflake_account'], creds['snowflake_warehouse'],
        creds['snowflake_database'], creds['snowflake_schema']
    )
    # Auto-reconnect if session expired
    try:
        conn.cursor().execute("SELECT 1")
    except Exception:
        get_snowflake_connection.clear()
        conn = get_snowflake_connection(
            creds['snowflake_user'], creds['snowflake_password'],
            creds['snowflake_account'], creds['snowflake_warehouse'],
            creds['snowflake_database'], creds['snowflake_schema']
        )
    return conn


@st.cache_resource
def get_gemini_client(api_key):
    """
    FIX 2: Gemini client created once and reused.
    Was being recreated on every single query before.
    """
    return genai.Client(api_key=api_key)


@st.cache_resource
def load_schema(file_path):
    """Schema loaded once from disk, cached for all users."""
    try:
        with open(file_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None


@st.cache_resource
def ensure_log_table(_creds_key, creds):
    """
    FIX 3: Log table check runs ONCE ever, not on every page rerun.
    _creds_key is a hashable key so cache_resource can cache it.
    """
    try:
        conn = get_conn(creds)
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS QUERY_LOGS (
                ID            VARCHAR(36)   DEFAULT UUID_STRING(),
                SESSION_ID    VARCHAR(16),
                ASKED_AT      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                QUESTION      TEXT,
                GENERATED_SQL TEXT,
                ROWS_RETURNED INTEGER,
                STATUS        VARCHAR(20),
                ERROR_MSG     TEXT
            )
        """)
    except Exception:
        pass  # Table already exists or minor error — don't block startup

# ══════════════════════════════════════════════════════════════════════════════
# CORE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def log_query_async(creds, session_id, question, sql, rows_returned, status, error_msg=""):
    """
    FIX 4: Logging runs in a background thread.
    User sees results instantly — logging doesn't add any wait time.
    """
    def _log():
        try:
            conn = get_conn(creds)
            conn.cursor().execute("""
                INSERT INTO QUERY_LOGS
                    (SESSION_ID, QUESTION, GENERATED_SQL, ROWS_RETURNED, STATUS, ERROR_MSG)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (session_id, question, sql, rows_returned, status, error_msg))
        except Exception:
            pass  # Never let logging break the main app

    threading.Thread(target=_log, daemon=True).start()


def generate_sql(creds, schema, user_query):
    """Uses cached Gemini client — no cold start after first query."""
    client = get_gemini_client(creds['gemini_api_key'])
    prompt = f"""You are an expert SQL query generator.
Convert the natural language question into a single valid SQL query for Snowflake.

DATABASE SCHEMA:
{yaml.dump(schema, indent=2)}

RULES:
1. Output ONLY the SQL query — no explanation, no markdown backticks.
2. NEVER use commands that modify data (INSERT, UPDATE, DELETE, DROP, etc.) — only SELECT queries allowed.
3. Use exact table/column names from the schema.
4. Ensure it is syntactically correct Snowflake SQL.
"""
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[prompt, f"Question: {user_query}\n\nSQL:"],
        config=types.GenerateContentConfig(temperature=0.4)
    )
    sql = response.text.strip()
    sql = re.sub(r'```sql\s*|\s*```', '', sql, flags=re.IGNORECASE).strip()
    return sql


def execute_sql(sql, creds):
    """Reuses persistent connection — no connection overhead."""
    conn = get_conn(creds)
    cur  = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description] if cur.description else []
    return rows, cols


def fetch_logs(creds, limit=200):
    try:
        conn = get_conn(creds)
        cur  = conn.cursor()
        cur.execute(f"""
            SELECT SESSION_ID, ASKED_AT, QUESTION, GENERATED_SQL,
                   ROWS_RETURNED, STATUS, ERROR_MSG
            FROM QUERY_LOGS ORDER BY ASKED_AT DESC LIMIT {limit}
        """)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols)
    except Exception:
        return pd.DataFrame()


def clear_logs(creds):
    conn = get_conn(creds)
    conn.cursor().execute("TRUNCATE TABLE QUERY_LOGS")


# ─── Password ─────────────────────────────────────────────────────────────────
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()

def check_admin_password(entered, stored_hash):
    if not stored_hash:
        return entered == os.getenv("ADMIN_PASSWORD", "")
    return hash_password(entered) == stored_hash


# ══════════════════════════════════════════════════════════════════════════════
# PAGES
# ══════════════════════════════════════════════════════════════════════════════

def show_admin_login(creds):
    st.markdown("## 🔐 Admin Login")
    st.divider()
    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.container(border=True):
            st.markdown("### 👤 Admin Access")
            password = st.text_input("Password", type="password", placeholder="Enter admin password")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("🔓 Login", type="primary", use_container_width=True):
                    if check_admin_password(password, creds['admin_password_hash']):
                        st.session_state.admin_logged_in = True
                        st.session_state.current_page   = "admin"
                        st.rerun()
                    else:
                        st.error("❌ Incorrect password")
            with c2:
                if st.button("← Back", use_container_width=True):
                    st.session_state.current_page = "app"
                    st.rerun()


def show_admin_dashboard(creds):
    with st.sidebar:
        st.markdown("## 🛡️ Admin Panel")
        st.success("✅ Logged in as Admin")
        st.markdown("---")
        if st.button("← Back to App",  use_container_width=True):
            st.session_state.current_page = "app"; st.rerun()
        if st.button("🔒 Logout",       use_container_width=True):
            st.session_state.admin_logged_in = False
            st.session_state.current_page    = "app"; st.rerun()
        if st.button("🔄 Refresh",      use_container_width=True):
            st.rerun()

    st.markdown("# 🛡️ Admin Dashboard")
    st.caption(f"Live · {datetime.now().strftime('%d %b %Y, %I:%M %p')}")
    st.divider()

    logs_df = fetch_logs(creds)
    schema  = st.session_state.db_schema
    table_count = len(schema.get('database_schema', {}).get('tables', [])) if schema else 0

    total_q      = len(logs_df)
    success_q    = len(logs_df[logs_df['STATUS'] == 'success'])  if not logs_df.empty else 0
    error_q      = len(logs_df[logs_df['STATUS'] == 'error'])    if not logs_df.empty else 0
    empty_q      = len(logs_df[logs_df['STATUS'] == 'empty'])    if not logs_df.empty else 0
    unique_users = logs_df['SESSION_ID'].nunique()               if not logs_df.empty else 0

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("📊 Total Queries", total_q)
    c2.metric("👥 Unique Users",  unique_users)
    c3.metric("✅ Successful",    success_q)
    c4.metric("⚠️ Empty",        empty_q)
    c5.metric("❌ Errors",        error_q)
    st.divider()

    t1,t2,t3,t4,t5 = st.tabs(["📜 Live Logs","📈 Stats","🗄️ DB Config","📐 Schema","⚡ Raw SQL"])

    with t1:
        st.markdown("### All Queries — Every User, Live")
        if logs_df.empty:
            st.info("No queries yet. They'll appear here the moment users start asking.")
        else:
            col_f1, col_f2 = st.columns([2,1])
            with col_f1: search = st.text_input("🔍 Filter by keyword")
            with col_f2: status_filter = st.selectbox("Status", ["All","success","empty","error"])

            filtered = logs_df.copy()
            if search:        filtered = filtered[filtered['QUESTION'].str.contains(search, case=False, na=False)]
            if status_filter != "All": filtered = filtered[filtered['STATUS'] == status_filter]

            disp = filtered[['SESSION_ID','ASKED_AT','QUESTION','ROWS_RETURNED','STATUS']].copy()
            disp.columns = ['User','Asked At','Question','Rows','Status']
            disp['Status'] = disp['Status'].map({'success':'✅','empty':'⚠️','error':'❌'}).fillna(disp['Status'])
            st.dataframe(disp, use_container_width=True, height=280)

            st.markdown("#### Full Details")
            for _, row in filtered.iterrows():
                with st.expander(f"[{row['SESSION_ID']}]  {str(row['QUESTION'])[:70]}"):
                    ca, cb = st.columns(2)
                    with ca:
                        st.markdown(f"**Session:** `{row['SESSION_ID']}`")
                        st.markdown(f"**At:** {row['ASKED_AT']}")
                        st.markdown(f"**Status:** {row['STATUS']}  |  **Rows:** {row['ROWS_RETURNED']}")
                        if row['ERROR_MSG']: st.error(f"Error: {row['ERROR_MSG']}")
                    with cb:
                        st.markdown(f"**Question:** {row['QUESTION']}")
                    st.code(row['GENERATED_SQL'], language="sql")

            col_dl, col_clr = st.columns([3,1])
            with col_dl:
                st.download_button("📥 Export CSV", logs_df.to_csv(index=False),
                    f"logs_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", "text/csv")
            with col_clr:
                if st.button("🗑️ Clear Logs"):
                    clear_logs(creds); st.rerun()

    with t2:
        st.markdown("### Usage Analytics")
        if logs_df.empty:
            st.info("No data yet.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Queries per User**")
                pu = logs_df.groupby('SESSION_ID').size().reset_index(name='Queries')
                pu.columns = ['Session ID','Total Queries']
                st.dataframe(pu, use_container_width=True)
            with c2:
                st.markdown("**Status Breakdown**")
                sc = logs_df['STATUS'].value_counts().reset_index()
                sc.columns = ['Status','Count']
                st.dataframe(sc, use_container_width=True)

            st.markdown("**Query Volume Over Time**")
            logs_df['ASKED_AT'] = pd.to_datetime(logs_df['ASKED_AT'])
            time_df = logs_df.groupby(logs_df['ASKED_AT'].dt.floor('H')).size().reset_index(name='Queries')
            time_df.columns = ['Hour','Queries']
            st.line_chart(time_df.set_index('Hour'))

            st.markdown("**Top 10 Most Asked Questions**")
            top_q = logs_df['QUESTION'].value_counts().head(10).reset_index()
            top_q.columns = ['Question','Times Asked']
            st.dataframe(top_q, use_container_width=True)

    with t3:
        st.markdown("### Current Configuration")
        st.caption("Loaded from `.env` — never visible to users.")
        st.divider()
        ca, cb = st.columns(2)
        with ca:
            st.markdown("**Snowflake**")
            st.code(f"""
User      : {creds['snowflake_user']}
Account   : {creds['snowflake_account']}
Warehouse : {creds['snowflake_warehouse']}
Database  : {creds['snowflake_database']}
Schema    : {creds['snowflake_schema']}
""", language="text")
        with cb:
            st.markdown("**Gemini API**")
            masked = creds['gemini_api_key'][:8] + "••••••••" if creds['gemini_api_key'] else "Not Set"
            st.code(f"""
Model      : gemini-2.5-flash
API Key    : {masked}
Schema File: {creds['schema_file']}
""", language="text")

    with t4:
        st.markdown("### Full Database Schema")
        tables = schema.get('database_schema', {}).get('tables', []) if schema else []
        for table in tables:
            with st.expander(f"📋 {table.get('name')} — {len(table.get('columns', []))} cols"):
                st.markdown(f"**Description:** {table.get('description','N/A')}")
                cols_data = table.get('columns', [])
                if cols_data:
                    st.dataframe(pd.DataFrame([{
                        'Column': c.get('name',''), 'Type': c.get('type',''),
                        'Description': c.get('description','')
                    } for c in cols_data]), use_container_width=True)

    with t5:
        st.markdown("### Execute Raw SQL on Snowflake")
        st.warning("⚠️ Admin only — runs directly on the live database.")
        raw_sql = st.text_area("SQL", height=150, placeholder="SELECT * FROM QUERY_LOGS LIMIT 20")
        if st.button("▶ Run", type="primary"):
            if raw_sql.strip():
                with st.spinner("Running..."):
                    try:
                        rows, cols = execute_sql(raw_sql, creds)
                        if rows:
                            df = pd.DataFrame(rows, columns=cols)
                            st.success(f"✅ {len(df)} row(s)")
                            st.dataframe(df, use_container_width=True)
                            st.download_button("📥 CSV", df.to_csv(index=False), "result.csv", "text/csv")
                        else:
                            st.info("Executed — no rows returned.")
                    except Exception as e:
                        st.error(f"❌ {e}")
            else:
                st.warning("Enter a query first.")


def show_main_app(creds):
    # Load schema once into session
    if st.session_state.db_schema is None:
        schema = load_schema(creds['schema_file'])
        if schema is None:
            st.error(f"❌ Schema file not found: `{creds['schema_file']}`")
            st.stop()
        st.session_state.db_schema = schema

    with st.sidebar:
        st.markdown("## 🏏 IPL Query App")
        st.success("✅ App Ready")
        st.markdown("---")
        st.markdown("**Connected To**")
        st.markdown(f"- 🗄️ `{creds['snowflake_database']}`")
        st.markdown(f"- 🗂️ `{creds['snowflake_schema']}`")
        st.markdown(f"- 🤖 `gemini-2.5-flash`")
        st.markdown("---")
        st.markdown("**How to Use**")
        st.markdown("1. Type your question\n2. Click **Run Query**\n3. View SQL + results")
        st.markdown("---")
        st.markdown("**Examples**")
        st.caption("• Top 10 batsmen by total runs")
        st.caption("• Matches played in Mumbai")
        st.caption("• Highest strike rate among openers")
        st.caption("• Most wickets in a single season")
        st.markdown("---")
        if st.button("⚙️ Settings", use_container_width=True):
            st.session_state.current_page = "admin_login"
            st.rerun()

    st.markdown("# 🏏 IPL Natural Language → SQL")
    st.markdown("Ask any question about IPL data in plain English.")
    st.divider()

    tab1, tab2 = st.tabs(["🤖 Query", "📚 Schema"])

    with tab1:
        user_query = st.text_area(
            "Ask a question about IPL data:",
            placeholder="e.g. Which batsman scored the most runs across all seasons?",
            height=100,
        )
        run_btn = st.button("🚀 Run Query", type="primary", use_container_width=True)

        if run_btn:
            if not user_query.strip():
                st.warning("Please enter a question first.")
            else:
                sql = ""
                with st.spinner("Generating SQL..."):
                    try:
                        sql = generate_sql(creds, st.session_state.db_schema, user_query)
                    except Exception as e:
                        log_query_async(creds, st.session_state.session_id, user_query, "", 0, "error", str(e))
                        st.error(f"❌ Gemini Error: {e}")
                        st.stop()

                st.markdown("#### Generated SQL")
                st.code(sql, language="sql")

                with st.spinner("Fetching results..."):
                    try:
                        rows, cols = execute_sql(sql, creds)
                    except Exception as e:
                        log_query_async(creds, st.session_state.session_id, user_query, sql, 0, "error", str(e))
                        st.error(f"❌ Database Error: {e}")
                        st.stop()

                if rows:
                    df = pd.DataFrame(rows, columns=cols)
                    st.success(f"✅ {len(df)} row(s) returned")
                    st.dataframe(df, use_container_width=True)
                    st.download_button("📥 Download CSV", df.to_csv(index=False), "ipl_results.csv", "text/csv")
                    log_query_async(creds, st.session_state.session_id, user_query, sql, len(rows), "success")
                else:
                    st.info("Query executed — no rows returned.")
                    log_query_async(creds, st.session_state.session_id, user_query, sql, 0, "empty")

    with tab2:
        st.markdown("### Database Schema")
        tables = st.session_state.db_schema.get('database_schema', {}).get('tables', [])
        for table in tables:
            with st.expander(f"📋 {table.get('name','Unknown')}"):
                st.markdown(f"**Description:** {table.get('description','N/A')}")
                cols_data = table.get('columns', [])
                if cols_data:
                    st.dataframe(pd.DataFrame([{
                        'Column': c.get('name',''), 'Type': c.get('type',''),
                        'Description': c.get('description','')
                    } for c in cols_data]), use_container_width=True)


# ─── ROUTER ───────────────────────────────────────────────────────────────────
def main():
    creds, missing = load_credentials()
    if missing:
        st.error("⚠️ Missing from `.env`: `" + "`, `".join(missing) + "`")
        st.stop()

    # Runs once ever — creates QUERY_LOGS table if needed
    ensure_log_table(creds['snowflake_account'], creds)

    page = st.session_state.current_page
    if page == "admin_login":
        show_admin_login(creds)
    elif page == "admin" and st.session_state.admin_logged_in:
        show_admin_dashboard(creds)
    else:
        st.session_state.current_page = "app"
        show_main_app(creds)

if __name__ == "__main__":
    main()