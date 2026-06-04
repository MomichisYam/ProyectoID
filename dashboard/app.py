import streamlit as st
import pandas as pd
import psycopg2
import plotly.express as px
import plotly.graph_objects as go
import time
from datetime import datetime

# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SOC Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── ESTILOS GLOBALES ──────────────────────────────────────────────────────────
st.markdown("""
<style>

/* Fondo general */
.stApp { background-color: #f5f5f3; }

/* Eliminar padding superior del main */
.block-container { padding-top: 1.2rem !important; padding-bottom: 5rem !important; }

/* ── KPI cards ── */
.kpi-card {
    background: #ffffff;
    border-radius: 12px;
    border: 0.5px solid rgba(0,0,0,0.08);
    padding: 18px 20px 14px;
    border-top-width: 3px;
    border-top-style: solid;
}
.kpi-card.neutral { border-top-color: #888780; }
.kpi-card.danger  { border-top-color: #E24B4A; }
.kpi-card.success { border-top-color: #1D9E75; background: #f0faf6; }
.kpi-card.warning { border-top-color: #EF9F27; background: #fffbf0; }

.kpi-label {
    font-size: 12px;
    color: #6b7280;
    margin-bottom: 4px;
    font-weight: 400;
}
.kpi-value {
    font-size: 34px;
    font-weight: 500;
    line-height: 1.1;
    color: #111827;
}
.kpi-value.success { color: #085041; }
.kpi-value.warning { color: #633806; }
.kpi-delta {
    font-size: 11px;
    margin-top: 6px;
    color: #9ca3af;
    display: flex;
    gap: 5px;
}
.delta-up   { color: #E24B4A; }
.delta-down { color: #1D9E75; }
.delta-flat { color: #9ca3af; }

/* ── Timer cards ── */
.timer-card {
    background: #ffffff;
    border-radius: 12px;
    border: 0.5px solid rgba(0,0,0,0.08);
    padding: 16px 20px;
    border-top: 3px solid #378ADD;
}
.timer-card.warning { background: #fffbf0; border-color: rgba(250,199,117,0.4); }
.timer-label { font-size: 12px; color: #6b7280; margin-bottom: 8px; }
.timer-display {
    display: flex;
    align-items: baseline;
    gap: 2px;
    font-size: 30px;
    font-weight: 500;
    color: #111827;
    font-variant-numeric: tabular-nums;
}
.timer-display.warning { color: #633806; }
.timer-sep { font-size: 24px; font-weight: 300; color: #d1d5db; margin: 0 2px; }
.timer-units {
    display: flex;
    gap: 0;
    margin-top: 3px;
    font-size: 10px;
    color: #9ca3af;
}

/* ── Panel cards (para gráficas y tabla) ── */
.panel-card {
    background: #ffffff;
    border-radius: 12px;
    border: 0.5px solid rgba(0,0,0,0.08);
    padding: 18px 20px;
}
.panel-title {
    font-size: 13px;
    font-weight: 500;
    color: #111827;
    margin-bottom: 12px;
}

/* ── Tabla de eventos ── */
.ev-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    border-bottom: 0.5px solid rgba(0,0,0,0.07);
}
.ev-title { font-size: 13px; font-weight: 500; color: #111827; }
.ev-badge {
    font-size: 11px;
    padding: 3px 10px;
    border-radius: 20px;
    background: #fde8e8;
    color: #791F1F;
    border: 0.5px solid rgba(226,75,74,0.25);
}

/* Ocultar índice de dataframes */
.stDataFrame thead tr th:first-child { display: none; }
.stDataFrame tbody tr td:first-child { display: none; }

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: #ffffff;
    border-right: 0.5px solid rgba(0,0,0,0.08);
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background: transparent;
    gap: 4px;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 20px !important;
    padding: 4px 14px !important;
    font-size: 12px !important;
    color: #6b7280 !important;
    background: transparent !important;
}
.stTabs [aria-selected="true"] {
    background: #f3f4f6 !important;
    color: #111827 !important;
    font-weight: 500 !important;
    border: 0.5px solid rgba(0,0,0,0.12) !important;
}
.stTabs [data-baseweb="tab-highlight"] { display: none; }
.stTabs [data-baseweb="tab-border"] { display: none; }

/* Quitar borde de selectbox / inputs */
div[data-baseweb="select"] > div { border-color: rgba(0,0,0,0.12) !important; }
</style>
""", unsafe_allow_html=True)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def kpi_card(label: str, value, variant: str = "neutral",
             delta_html: str = ""):
    """Renderiza una KPI card con franja de color superior."""
    val_class = "success" if variant == "success" else ("warning" if variant == "warning" else "")
    st.markdown(f"""
    <div class="kpi-card {variant}">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value {val_class}">{value}</div>
        <div class="kpi-delta">{delta_html}</div>
    </div>
    """, unsafe_allow_html=True)


def timer_card(label: str, segments: list[tuple[str, str]],
               variant: str = ""):
    """
    Renderiza un timer card.
    segments = [("12", "horas"), ("18", "min"), ("16", "seg")]
    """
    nums = f'<span class="timer-sep">:</span>'.join(
        f'<span>{s}</span>' for s, _ in segments
    )
    units_html = "".join(
        f'<span style="flex:1">{u}</span>' for _, u in segments
    )
    st.markdown(f"""
    <div class="timer-card {variant}">
        <div class="timer-label">{label}</div>
        <div class="timer-display {variant}">{nums}</div>
        <div class="timer-units">{units_html}</div>
    </div>
    """, unsafe_allow_html=True)


def sev_badge(sev: str) -> str:
    colors = {
        "CRÍTICO": ("fde8e8", "791F1F"),
        "ALTO":    ("faeeda", "633806"),
        "MEDIO":   ("e6f1fb", "0C447C"),
        "BAJO":    ("f3f4f6", "6b7280"),
    }
    bg, fg = colors.get(sev.upper(), ("f3f4f6", "6b7280"))
    return (f'<span style="background:#{bg};color:#{fg};'
            f'font-size:11px;font-weight:500;padding:2px 8px;'
            f'border-radius:4px;">{sev}</span>')


PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="sans-serif", size=12, color="#374151"),
    margin=dict(t=10, b=10, l=10, r=10),
)

# ── CONEXIÓN ──────────────────────────────────────────────────────────────────
@st.cache_resource
def get_connection():
    conn = psycopg2.connect(
        host="postgres",
        database="security_warehouse",
        user="admin",
        password="admin123",
        options="-c timezone=America/Mexico_City"
    )
    conn.autocommit = True
    return conn


def fetch(query: str) -> pd.DataFrame:
    conn = get_connection()
    conn.rollback()
    return pd.read_sql(query, get_connection())

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Infraestructura SOC")
    st.caption("Arquitectura Kappa · Data Lakehouse activo")
    st.divider()

    st.markdown("**Data Lakehouse (MinIO)**")
    st.info("Logs JSON respaldados automáticamente en el bucket `datalake`.")
    st.link_button("Abrir consola MinIO", "http://localhost:9001",
                   use_container_width=True)

    st.divider()
    st.markdown("**Mensajería (Kafka)**")
    st.success("Tópico activo: `sec-alerts`")

    st.divider()
    st.markdown("**Data Warehouse (PostgreSQL)**")
    st.success("Base de datos: `security_warehouse`")

    st.divider()
    st.caption(f"Última actualización: {datetime.now().strftime('%H:%M:%S')}")

# ── TOPBAR ────────────────────────────────────────────────────────────────────
left, right = st.columns([3, 1])
with left:
    st.markdown("## Centro de Operaciones de Seguridad")
    st.caption("Análisis OLAP en tiempo real")
with right:
    st.markdown(
        f"<div style='text-align:right;font-size:12px;color:#6b7280;"
        f"padding-top:18px'>{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</div>",
        unsafe_allow_html=True,
    )

st.divider()

# ── LOOP PRINCIPAL ────────────────────────────────────────────────────────────
placeholder = st.empty()

while True:
    print(f"\n[DEBUG {datetime.now().strftime('%H:%M:%S')}] 🟢 Iniciando nueva iteración del ciclo...")
    
    with placeholder.container():

        # ── FETCH DATA ────────────────────────────────────────────────────────
        print("[DEBUG] Ejecutando consulta de KPIs...")
        df_kpis = fetch("""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN source = 'snort' THEN 1 END) AS total_red,
                COUNT(CASE WHEN source = 'ossec'  THEN 1 END) AS total_local,
                COUNT(CASE WHEN source = 'snort'
                           AND alert_timestamp >= NOW() - INTERVAL '1 hour'
                      THEN 1 END) AS snort_ultima_hora
            FROM security_alerts;
        """)
        
        t_total = int(df_kpis["total"][0])
        t_red   = int(df_kpis["total_red"][0])
        t_local = int(df_kpis["total_local"][0])
        t_hora  = int(df_kpis["snort_ultima_hora"][0])
        pct_red = round(t_red / t_total * 100) if t_total else 0
        print(f"[DEBUG] KPIs listos -> Total: {t_total} | Snort: {t_red} | OSSEC: {t_local}")

        print("[DEBUG] Ejecutando consulta de Severidad...")
        df_sev = fetch("""
            WITH Clasificacion AS (
                SELECT 
                    CASE 
                        WHEN level >= 10 THEN 'CRÍTICO'
                        WHEN level >= 7 THEN 'ALTO'
                        WHEN level >= 4 THEN 'MEDIO'
                        WHEN source = 'snort' THEN 'ALTO'
                        ELSE 'BAJO'
                    END as severidad
                FROM security_alerts
            )
            SELECT severidad, COUNT(*) AS total
            FROM Clasificacion
            GROUP BY severidad
            ORDER BY total DESC;
        """)
        print(f"[DEBUG] Severidad lista -> {len(df_sev)} filas recuperadas.")

        print("[DEBUG] Ejecutando consulta de Serie Temporal...")
        df_serie = fetch("""
            SELECT
                date_trunc('hour', alert_timestamp) AS hora,
                source,
                COUNT(*) AS total
            FROM security_alerts
            WHERE alert_timestamp >= NOW() - INTERVAL '12 hours'
            GROUP BY hora, source
            ORDER BY hora;
        """)
        print(f"[DEBUG] Serie Temporal lista -> {len(df_serie)} filas recuperadas.")

        print("[DEBUG] Ejecutando consulta de Cubo ROLLUP...")
        df_cubo = fetch("""
            SELECT
                COALESCE(source, 'TOTAL GENERAL') AS "Fuente",
                COALESCE(TO_CHAR(date_trunc('hour', alert_timestamp),
                         'YYYY-MM-DD HH24:00'), 'TODO EL TIEMPO') AS "Ventana",
                COUNT(*) AS "Eventos"
            FROM security_alerts
            GROUP BY ROLLUP(source, date_trunc('hour', alert_timestamp))
            ORDER BY "Fuente", "Ventana" DESC;
        """)
        print(f"[DEBUG] Cubo ROLLUP listo -> {len(df_cubo)} filas recuperadas.")

        print("[DEBUG] Ejecutando consulta de Red (Snort)...")
        df_red = fetch("""
            WITH IP_Frecuencia AS (
                SELECT src_ip, COUNT(*) AS frec
                FROM security_alerts
                WHERE source = 'snort'
                GROUP BY src_ip
            ),
            IP_Ranking AS (
                SELECT src_ip, frec,
                       RANK() OVER (ORDER BY frec DESC) AS ranking
                FROM IP_Frecuencia
            )
            SELECT
                s.alert_timestamp          AS "Timestamp",
                s.protocol                 AS "Protocolo",
                split_part(s.raw_json->>'src_ap', ':', 2) AS "Puerto",
                s.src_ip                   AS "IP Origen",
                r.frec                     AS "Frec. histórica",
                r.ranking                  AS "Ranking"
            FROM security_alerts s
            JOIN IP_Ranking r ON s.src_ip = r.src_ip
            WHERE s.source = 'snort'
            ORDER BY s.alert_timestamp DESC
            LIMIT 50;
        """)
        print(f"[DEBUG] Red (Snort) lista -> {len(df_red)} filas recuperadas.")

        print("[DEBUG] Ejecutando consulta Local (OSSEC)...")
        df_local = fetch("""
            WITH LocalOLAP AS (
                SELECT
                    alert_timestamp AS "Timestamp",
                    rule_id         AS "Regla",
                    message         AS "Evento",
                    CASE
                        WHEN location LIKE '/%'
                          OR raw_json->>'syscheck' IS NOT NULL THEN 'Sí'
                        ELSE 'No'
                    END AS "Archivo cambiado",
                    ROUND(
                        COUNT(*) OVER (PARTITION BY rule_id) * 100.0
                        / NULLIF(COUNT(*) OVER (), 0), 2
                    ) AS "% Frec. regla"
                FROM security_alerts
                WHERE source = 'ossec'
            )
            SELECT * FROM LocalOLAP
            ORDER BY "Timestamp" DESC
            LIMIT 50;
        """)
        print(f"[DEBUG] Local (OSSEC) lista -> {len(df_local)} filas recuperadas.")

        print("[DEBUG] Ejecutando consulta de Timers (Triaje)...")
        df_timers = fetch("""
            SELECT 
                COALESCE(AVG(EXTRACT(EPOCH FROM (ingested_at - alert_timestamp))), 0) AS triage_avg_sec,
                COUNT(CASE WHEN level >= 10 THEN 1 END) AS criticos_sin_asignar
            FROM security_alerts
            WHERE alert_timestamp >= NOW() - INTERVAL '24 hours';
        """)

        sin_asignar = int(df_timers["criticos_sin_asignar"][0])
        triage_sec = int(df_timers["triage_avg_sec"][0])

        t_h = f"{triage_sec // 3600:02d}"
        t_m = f"{(triage_sec % 3600) // 60:02d}"
        t_s = f"{triage_sec % 60:02d}"
        print(f"[DEBUG] Timers listos -> Triaje promedio: {triage_sec} seg | Críticos: {sin_asignar}")
        
        print("[DEBUG] 🎨 Renderizando UI de Streamlit...")
        
        # ── FILA 1: KPIs ──────────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            kpi_card(
                "Total eventos registrados",
                f"{t_total:,}",
                variant="neutral",
                delta_html='<span class="delta-up">▲ en curso</span> acumulado',
            )
        with c2:
            kpi_card(
                "Alertas de red (Snort)",
                f"{t_red:,}",
                variant="danger",
                delta_html=f'<span class="delta-up">▲ {t_hora}</span> última hora',
            )
        with c3:
            kpi_card(
                "Alertas locales (OSSEC)",
                f"{t_local:,}",
                variant="neutral",
                delta_html=f'<span class="delta-flat">→</span> {pct_red}% perimetral',
            )
        with c4:
            kpi_card(
                "Alertas críticas en 24h",
                sin_asignar,
                variant="success" if sin_asignar == 0 else "warning",
                delta_html='<span class="delta-flat">→</span> detectadas',
            )

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # ── FILA 2: TIMERS ────────────────────────────────────────────────────
        t1, t2, t3, t4 = st.columns(4)
        
        with t1:
            timer_card(
                "Promedio Tiempo de Ingesta (Triaje)",
                [(t_h, "horas"), (t_m, "min"), (t_s, "seg")],
            )

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # ── FILA 3: GRÁFICAS ──────────────────────────────────────────────────
        gc1, gc2 = st.columns([1, 1.5])

        with gc1:
            st.markdown('<div class="panel-card">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title">Alertas por severidad</div>',
                        unsafe_allow_html=True)
            if not df_sev.empty:
                sev_order = ["CRÍTICO", "ALTO", "MEDIO", "BAJO"]
                sev_colors = {
                    "CRÍTICO": "#E24B4A",
                    "ALTO":    "#EF9F27",
                    "MEDIO":   "#378ADD",
                    "BAJO":    "#888780",
                }
                df_sev["color"] = df_sev["severidad"].map(sev_colors).fillna("#888780")
                df_sev["orden"] = df_sev["severidad"].apply(
                    lambda x: sev_order.index(x) if x in sev_order else 99
                )
                df_sev = df_sev.sort_values("orden")

                fig_sev = go.Figure(go.Bar(
                    x=df_sev["total"],
                    y=df_sev["severidad"],
                    orientation="h",
                    marker_color=df_sev["color"],
                    marker_line_width=0,
                ))
                fig_sev.update_layout(
                    **PLOTLY_LAYOUT,
                    height=200,
                    xaxis=dict(showgrid=True, gridcolor="#f0f0f0",
                               zeroline=False, tickfont=dict(size=11)),
                    yaxis=dict(showgrid=False, tickfont=dict(size=11)),
                    bargap=0.3,
                )
                st.plotly_chart(fig_sev, use_container_width=True,
                                config={"displayModeBar": False}, key=f"chart_sev_{time.time()}")
            else:
                st.info("Sin datos de severidad.")
            st.markdown("</div>", unsafe_allow_html=True)

        with gc2:
            st.markdown('<div class="panel-card">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title">Eventos en el tiempo — Snort vs OSSEC</div>',
                        unsafe_allow_html=True)
            if not df_serie.empty:
                df_pivot = df_serie.pivot(
                    index="hora", columns="source", values="total"
                ).fillna(0).reset_index()

                fig_line = go.Figure()
                if "snort" in df_pivot.columns:
                    fig_line.add_trace(go.Scatter(
                        x=df_pivot["hora"], y=df_pivot["snort"],
                        name="Snort", line=dict(color="#E24B4A", width=2),
                        mode="lines+markers",
                        marker=dict(size=4, color="#E24B4A"),
                        fill="tozeroy",
                        fillcolor="rgba(226,75,74,0.07)",
                    ))
                if "ossec" in df_pivot.columns:
                    fig_line.add_trace(go.Scatter(
                        x=df_pivot["hora"], y=df_pivot["ossec"],
                        name="OSSEC", line=dict(color="#378ADD", width=2,
                                                dash="dot"),
                        mode="lines+markers",
                        marker=dict(size=4, color="#378ADD"),
                        fill="tozeroy",
                        fillcolor="rgba(55,138,221,0.06)",
                    ))
                fig_line.update_layout(
                    **PLOTLY_LAYOUT,
                    height=200,
                    showlegend=True,
                    legend=dict(
                        orientation="h", yanchor="bottom", y=1.02,
                        xanchor="left", x=0,
                        font=dict(size=11), bgcolor="rgba(0,0,0,0)",
                    ),
                    xaxis=dict(showgrid=False, tickfont=dict(size=10)),
                    yaxis=dict(showgrid=True, gridcolor="#f0f0f0",
                               zeroline=False, tickfont=dict(size=11)),
                )
                st.plotly_chart(fig_line, use_container_width=True,
                                config={"displayModeBar": False}, key=f"chart_line_{time.time()}")
            else:
                st.info("Sin datos de serie temporal.")
            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # ── FILA 4: TABS ──────────────────────────────────────────────────────
        tab_red, tab_host, tab_rollup = st.tabs([
            "Red (Snort) — OLAP",
            "Host (OSSEC) — OLAP",
            "Cubo ROLLUP",
        ])

        with tab_red:
            col_bar, col_tbl = st.columns([1, 1.4])
            with col_bar:
                st.caption("Top IPs atacantes por frecuencia histórica")
                if not df_red.empty:
                    df_top = (
                        df_red.drop_duplicates(subset=["IP Origen"])
                        .nlargest(8, "Frec. histórica")
                        .sort_values("Frec. histórica")
                    )
                    fig_top = go.Figure(go.Bar(
                        x=df_top["Frec. histórica"],
                        y=df_top["IP Origen"],
                        orientation="h",
                        marker_color="#E24B4A",
                        marker_line_width=0,
                    ))
                    fig_top.update_layout(
                        **PLOTLY_LAYOUT,
                        height=max(200, len(df_top) * 34 + 60),
                        xaxis=dict(showgrid=True, gridcolor="#f0f0f0",
                                   zeroline=False, tickfont=dict(size=11)),
                        yaxis=dict(showgrid=False, tickfont=dict(size=11)),
                        bargap=0.3,
                    )
                    st.plotly_chart(fig_top, use_container_width=True,
                                    config={"displayModeBar": False}, key=f"chart_top_{time.time()}")
                else:
                    st.info("Sin datos de red.")

            with col_tbl:
                st.caption("Últimos 50 eventos Snort con enriquecimiento OLAP (CTEs)")
                if not df_red.empty:
                    st.dataframe(
                        df_red.style.format({
                            "Frec. histórica": "{:,.0f}",
                            "Ranking": "{:.0f}",
                        }),
                        use_container_width=True,
                        hide_index=True,
                        height=340,
                    )
                else:
                    st.info("Sin eventos de red detectados.")

        with tab_host:
            st.caption("Eventos locales con integridad de archivos — Window Functions")
            if not df_local.empty:
                st.dataframe(
                    df_local.style.format({"% Frec. regla": "{:.2f}%"}),
                    use_container_width=True,
                    hide_index=True,
                    height=380,
                )
            else:
                st.info("Sin eventos locales detectados.")

        with tab_rollup:
            st.caption("Cubo OLAP multidimensional — GROUP BY ROLLUP(source, hora)")
            if not df_cubo.empty:
                def highlight_total(row):
                    if row["Fuente"] == "TOTAL GENERAL":
                        return ["background-color:#f0faf6; font-weight:500"] * len(row)
                    return [""] * len(row)

                st.dataframe(
                    df_cubo.style.apply(highlight_total, axis=1)
                                 .format({"Eventos": "{:,.0f}"}),
                    use_container_width=True,
                    hide_index=True,
                    height=380,
                )
            else:
                st.info("Sin datos en el cubo OLAP.")
    
    print("[DEBUG] 🛑 Fin de la iteración. Durmiendo 3 segundos...\n")
    time.sleep(3)
