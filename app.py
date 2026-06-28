import streamlit as st
import os
import uuid
import time
import base64
import pandas as pd
import plotly.express as px
import fitz  # PyMuPDF para extracción y vista previa de PDFs
from supabase import create_client, Client
from openai import OpenAI

# --- 1. CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Asistente EIC UCN", page_icon=":material/school:", layout="wide")

st.markdown("""
    <style>
        .block-container { padding-top: 4rem; padding-bottom: 1rem; }
        [data-testid="stSidebar"] { background-color: #1a2a3a; }
        .stChatMessageAvatar { border-radius: 4px; }
        [data-testid="stSidebar"] div[data-baseweb="input"], 
        [data-testid="stSidebar"] div[data-baseweb="select"] {
            background-color: rgba(255, 255, 255, 0.05) !important;
            border: 1px solid #00b4c8 !important;
            border-radius: 6px !important;
        }
        [data-testid="stSidebar"] input { color: white !important; }
        div[data-testid="stImage"] { display: flex; justify-content: center; align-items: center; }
        div[data-testid="stImage"] img { object-fit: contain !important; }
        div[data-testid="metric-container"] {
            background-color: #1e2f42;
            border: 1px solid #00b4c8;
            padding: 15px;
            border-radius: 8px;
        }
        [data-testid="stSidebar"] div[data-testid="stButton"] button {
            justify-content: flex-start !important;
            padding-left: 1rem !important;
        }
        [data-testid="stSidebar"] div[data-testid="stButton"] button > div {
            display: flex !important;
            justify-content: flex-start !important;
            width: 100% !important;
        }
        [data-testid="stSidebar"] div[data-testid="stButton"] button p {
            text-align: left !important;
            margin-left: 0.5rem !important;
        }
        hr { margin-top: 1rem; margin-bottom: 1rem; }
    </style>
""", unsafe_allow_html=True)

# ---> CONEXIONES A SERVICIOS <---
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

@st.cache_resource
def iniciar_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = iniciar_supabase()

cliente_llm = OpenAI(
    base_url="https://eic-proyectos.ucn.cl/myllm/v1",
    api_key=st.secrets["LLM_API_KEY"]
)

cliente_respaldo = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=st.secrets["GEMINI_API_KEY"]
)

# --- 2. BASE DE CONOCIMIENTO Y REGLAS DINÁMICAS (AHORA EN SUPABASE STORAGE) ---
def cargar_base_conocimiento():
    texto_combinado = ""
    try:
        lista_archivos = supabase.storage.from_("conocimiento").list()
        for archivo in lista_archivos:
            if archivo['name'].endswith(".txt"):
                res = supabase.storage.from_("conocimiento").download(archivo['name'])
                contenido = res.decode('utf-8')
                texto_combinado += f"\n\n--- INICIO DE {archivo['name'].upper()} ---\n{contenido}\n--- FIN DE {archivo['name'].upper()} ---\n"
    except Exception as e:
        print(f"Error al cargar desde Storage: {e}")
    return texto_combinado

documentos_actualizados = cargar_base_conocimiento()

def cargar_reglas_jefatura():
    try:
        resp = supabase.table("reglas_jefatura").select("*").execute()
        reglas_str = ""
        for fila in resp.data:
            reglas_str += f"- Cuando te pregunten sobre '{fila.get('tema_o_pregunta', '')}', DEBES RESPONDER EXACTAMENTE ESTO: {fila.get('respuesta_correcta_exigida', '')}\n"
        return reglas_str
    except Exception:
        return ""

def generar_prompt_sistema(nombre=None, carrera=None):
    reglas_extra = cargar_reglas_jefatura()
    prompt_base = f"""
Eres el Asistente Virtual Oficial de la Jefatura de Carrera de Ingeniería (UCN, Sede Coquimbo). 
Tu fuente principal de información proviene de estos documentos:
{documentos_actualizados}

INSTRUCCIONES:
1. Traducción Semántica (MUY IMPORTANTE): 
   - Si el alumno pregunta "cuándo salgo de clases", "cuándo termino" o "salir de vacaciones", se refiere a las FECHAS DE TÉRMINO DEL SEMESTRE.
   - "Echarse un ramo" = Reprobar una asignatura.
   - "Congelar" = Suspensión temporal de estudios.
2. Modo Consultivo: Si no existe el trámite exacto, ofrece opciones.
3. Fallo Total: Si no hay información relacionada, indica que contacten a Jefatura.
4. Precisión y Contexto (REGLA ESTRICTA): Enfócate SOLO en el trámite consultado. Si preguntan por la "Práctica", extrae los requisitos exclusivos del documento de Práctica. NO mezcles información con el reglamento general a menos que sea estrictamente necesario.
5. Proactividad Inteligente con Fechas: Si el trámite requiere una fecha, busca en el Calendario Académico SÓLO la fecha exacta de ese hito. ESTÁ ESTRICTAMENTE PROHIBIDO transcribir o listar el calendario completo.
6. Citar Fuentes: Al final de tu respuesta, DEBES indicar obligatoriamente de qué documento sacaste la información usando el formato: "**Fuente:** [Nombre del documento]".
"""
    if reglas_extra:
        prompt_base += f"\n\n[INSTRUCCIONES SUPREMAS DEL JEFE DE CARRERA - PRIORIDAD ABSOLUTA]\n{reglas_extra}"
    if nombre and carrera:
        prompt_base += f"\n\n[CONTEXTO DEL USUARIO ACTUAL]\nEstudiante: {nombre}\nCarrera: {carrera}."
    return prompt_base

# --- 3. INICIALIZAR MEMORIA ---
variables_sesion = ["usuario_id", "usuario_nombre", "usuario_carrera", "usuario_rol", "conversation_id", "calificaciones_guardadas", "timestamps_anonimo", "ultimo_mensaje_tiempo", "menu_admin"]
for var in variables_sesion:
    if var not in st.session_state:
        if "usuario" in var: st.session_state[var] = None
        elif var == "conversation_id": st.session_state[var] = str(uuid.uuid4())
        elif var == "calificaciones_guardadas": st.session_state[var] = {}
        elif var == "timestamps_anonimo": st.session_state[var] = []
        elif var == "menu_admin": st.session_state[var] = "Dashboard"
        else: st.session_state[var] = 0.0

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": generar_prompt_sistema()}]

if "anon_user_id" not in st.session_state:
    try:
        resp = supabase.table("usuarios").select("id").eq("correo", "anonimo@ucn.cl").execute()
        if len(resp.data) > 0:
            st.session_state.anon_user_id = resp.data[0]['id']
        else:
            resp_insert = supabase.table("usuarios").insert({"correo": "anonimo@ucn.cl", "nombre": "Estudiante Anónimo", "contrasena": "anonimo123"}).execute()
            st.session_state.anon_user_id = resp_insert.data[0]['id']
    except Exception:
        st.session_state.anon_user_id = None

# --- 4. BARRA LATERAL ---
with st.sidebar:
    if not st.session_state.usuario_id:
        st.markdown("### :material/account_circle: Acceso Sistema")
        tab_login, tab_registro = st.tabs(["Ingresar", "Registrarse"])
        
        with tab_login:
            correo_login = st.text_input("Correo Institucional:", key="log_mail")
            pass_login = st.text_input("Contraseña:", type="password", key="log_pass")
            if st.button("Acceder", type="primary", use_container_width=True, icon=":material/login:"):
                try:
                    resp = supabase.table("usuarios").select("*").eq("correo", correo_login).eq("contrasena", pass_login).execute()
                    if len(resp.data) > 0:
                        st.session_state.usuario_id = resp.data[0]['id']
                        st.session_state.usuario_nombre = resp.data[0]['nombre']
                        st.session_state.usuario_carrera = resp.data[0].get('carrera', 'N/A')
                        st.session_state.usuario_rol = resp.data[0].get('rol', 'estudiante')
                        
                        st.session_state.conversation_id = str(uuid.uuid4())
                        st.session_state.messages = [{"role": "system", "content": generar_prompt_sistema(st.session_state.usuario_nombre, st.session_state.usuario_carrera)}]
                        st.rerun()
                    else:
                        st.error("Credenciales incorrectas.", icon=":material/error:")
                except Exception:
                    st.error("Error de conexión.", icon=":material/cloud_off:")
        
        with tab_registro:
            nombre_reg = st.text_input("Nombre Completo:", key="reg_name")
            correo_reg = st.text_input("Correo Institucional:", key="reg_mail")
            pass_reg = st.text_input("Contraseña:", type="password", key="reg_pass")
            carrera_reg = st.selectbox("Carrera:", ["Ingeniería Civil Industrial", "Ingeniería Civil en Computación e Informática", "Ingeniería en Información y Control de Gestión", "Otra"])
            if st.button("Crear Cuenta", use_container_width=True, icon=":material/person_add:"):
                correo_limpio = correo_reg.strip().lower()
                if not (correo_limpio.endswith("@ucn.cl") or correo_limpio.endswith("@alumnos.ucn.cl")):
                    st.error("Solo correos @ucn.cl o @alumnos.ucn.cl")
                else:
                    try:
                        check = supabase.table("usuarios").select("id").eq("correo", correo_limpio).execute()
                        if len(check.data) > 0:
                            st.warning("Correo ya registrado.")
                        else:
                            supabase.table("usuarios").insert({"correo": correo_limpio, "contrasena": pass_reg, "nombre": nombre_reg, "carrera": carrera_reg, "rol": "estudiante"}).execute()
                            st.success("Cuenta creada. Por favor ingresa.")
                    except Exception:
                        st.error("Error al registrar.")
    else:
        with st.container(border=True):
            icono_rol = ":material/admin_panel_settings:" if st.session_state.usuario_rol == "admin" else ":material/person:"
            st.markdown(f"#### {icono_rol} {st.session_state.usuario_nombre}")
            st.caption(f":material/school: {st.session_state.usuario_carrera}")
            
            if st.button("Cerrar Sesión", icon=":material/logout:", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()
        
        if st.session_state.usuario_rol == "admin":
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<p style='color:#00b4c8; font-size: 0.85em; font-weight: bold; letter-spacing: 1.5px;'>MENÚ PRINCIPAL</p>", unsafe_allow_html=True)
            
            def boton_menu(texto, icono, clave):
                es_activo = st.session_state.menu_admin == clave
                tipo_boton = "primary" if es_activo else "tertiary"
                if st.button(texto, icon=icono, type=tipo_boton, use_container_width=True):
                    st.session_state.menu_admin = clave
                    st.rerun()

            boton_menu("Dashboard Analítico", ":material/insights:", "Dashboard")
            boton_menu("Entrenar Bot", ":material/psychology:", "Entrenar")
            boton_menu("Gestión de FAQs", ":material/contact_support:", "FAQs")
            boton_menu("Base Conocimiento", ":material/folder_managed:", "PDFs")

        elif st.session_state.usuario_rol != "admin":
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Nueva Conversación", icon=":material/add:", type="primary", use_container_width=True):
                st.session_state.conversation_id = str(uuid.uuid4())
                st.session_state.messages = [{"role": "system", "content": generar_prompt_sistema(st.session_state.usuario_nombre, st.session_state.usuario_carrera)}]
                st.rerun()
                
            st.divider()
            st.markdown("<p style='color:#00b4c8; font-size: 0.85em; font-weight: bold; letter-spacing: 1.5px;'>HISTORIAL</p>", unsafe_allow_html=True)
            try:
                historial_bd = supabase.table("interacciones").select("conversacion_id, pregunta, fecha").eq("usuario_id", st.session_state.usuario_id).order("fecha").execute()
                conversaciones_agrupadas = {}
                for fila in historial_bd.data:
                    cid = fila["conversacion_id"]
                    if cid not in conversaciones_agrupadas:
                        conversaciones_agrupadas[cid] = fila["pregunta"]
                
                for cid, pregunta in reversed(list(conversaciones_agrupadas.items())):
                    titulo_corto = pregunta[:28] + "..." if len(pregunta) > 28 else pregunta
                    es_activo = cid == st.session_state.conversation_id
                    tipo_btn = "secondary" if es_activo else "tertiary"
                    icono_btn = ":material/chat:" if es_activo else ":material/chat_bubble_outline:"
                    
                    if st.button(titulo_corto, key=f"hist_{cid}", type=tipo_btn, icon=icono_btn, use_container_width=True):
                        st.session_state.conversation_id = cid
                        st.session_state.messages = [{"role": "system", "content": generar_prompt_sistema(st.session_state.usuario_nombre, st.session_state.usuario_carrera)}]
                        
                        chat_completo = supabase.table("interacciones").select("*").eq("usuario_id", st.session_state.usuario_id).eq("conversacion_id", cid).order("fecha").execute()
                        for chat_fila in chat_completo.data:
                            st.session_state.messages.append({"role": "user", "content": chat_fila["pregunta"]})
                            st.session_state.messages.append({"role": "assistant", "content": chat_fila["respuesta"], "db_id": chat_fila["id"]})
                            
                            calif = chat_fila.get("calificacion")
                            if calif is not None:
                                st.session_state[f"stars_{chat_fila['id']}"] = calif - 1
                                st.session_state.calificaciones_guardadas[chat_fila["id"]] = calif
                        st.rerun()
            except Exception:
                st.caption("No hay chats recientes.")

# --- 5. ENRUTADOR PRINCIPAL ---
if st.session_state.usuario_rol == "admin":
    st.markdown("<h2 style='color: #00b4c8; margin-bottom: 0;'>Panel de Control - Jefatura EIC</h2>", unsafe_allow_html=True)
    st.caption("Bienvenido al centro de administración del Asistente Virtual.")
    st.divider()

    opcion_elegida = st.session_state.menu_admin

    if opcion_elegida == "Dashboard":
        st.markdown("### :material/insights: Dashboard Analítico")
        try:
            resp_usuarios = supabase.table("usuarios").select("correo").execute()
            correos_excluidos = ["anonimo@ucn.cl", "diego.uribe01@alumnos.ucn.cl", "jc.icindustrial.cqbo@alumnos.ucn.cl"]
            usuarios_reales = [u for u in resp_usuarios.data if u['correo'] not in correos_excluidos]
            total_estudiantes = len(usuarios_reales)

            interacciones = supabase.table("interacciones").select("*").execute()
            data_int = interacciones.data
            
            if data_int:
                df = pd.DataFrame(data_int)
                total_preguntas = len(df)
                avg_tiempo = df['tiempo_respuesta'].mean() if 'tiempo_respuesta' in df.columns else 0
                avg_estrellas = df['calificacion'].mean() if 'calificacion' in df.columns and not df['calificacion'].isnull().all() else 0
                avg_jefe = df['calificacion_jefatura'].dropna().mean() if 'calificacion_jefatura' in df.columns and not df['calificacion_jefatura'].isnull().all() else 0
                
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("Alumnos Registrados", total_estudiantes)
                col2.metric("Total Preguntas", total_preguntas)
                col3.metric("Tiempo Promedio", f"{avg_tiempo:.1f} seg")
                col4.metric("Satisfacción Alumnos", f"{avg_estrellas:.1f} ⭐")
                col5.metric("Satisfacción Jefatura", f"{avg_jefe:.1f} ⭐")
                
                st.divider()
                st.markdown("#### :material/monitoring: Visualización de Datos")
                
                col_grafico1, col_grafico2 = st.columns(2)
                with col_grafico1:
                    st.markdown("##### Temáticas más consultadas")
                    if 'categoria' in df.columns and not df['categoria'].isnull().all():
                        df_cat = df[df['categoria'].notna()]
                        conteo_cat = df_cat['categoria'].value_counts().reset_index()
                        conteo_cat.columns = ['Categoría', 'Consultas']
                        
                        fig_bar = px.bar(conteo_cat, x='Categoría', y='Consultas', color_discrete_sequence=["#00b4c8"], text_auto=True)
                        fig_bar.update_layout(xaxis_title="Temática Académica", yaxis_title="Cantidad de Consultas", dragmode=False, margin=dict(l=0, r=0, t=30, b=0))
                        fig_bar.update_xaxes(fixedrange=True)
                        fig_bar.update_yaxes(fixedrange=True)
                        st.plotly_chart(fig_bar, use_container_width=True, config={'displayModeBar': False})
                    else:
                        st.info("Aún no hay datos categorizados suficientes.", icon=":material/info:")

                with col_grafico2:
                    st.markdown("##### Volumen de consultas diario")
                    if 'fecha' in df.columns:
                        df['fecha_corta'] = pd.to_datetime(df['fecha']).dt.date
                        conteo_fechas = df['fecha_corta'].value_counts().sort_index().reset_index()
                        conteo_fechas.columns = ['Fecha', 'Consultas']
                        
                        fig_line = px.line(conteo_fechas, x='Fecha', y='Consultas', color_discrete_sequence=["#ff4b4b"], markers=True)
                        fig_line.update_layout(xaxis_title="Fecha de Consulta", yaxis_title="Volumen", dragmode=False, hovermode="x unified", margin=dict(l=0, r=0, t=30, b=0))
                        fig_line.update_xaxes(fixedrange=True, tickformat="%d-%m-%Y")
                        fig_line.update_yaxes(fixedrange=True)
                        st.plotly_chart(fig_line, use_container_width=True, config={'displayModeBar': False})

                st.markdown("##### Evolución de Tiempos de Respuesta (Segundos)")
                if 'fecha' in df.columns and 'tiempo_respuesta' in df.columns:
                    df['fecha_completa'] = pd.to_datetime(df['fecha'])
                    df_time = df.sort_values(by='fecha_completa')
                    
                    fig_time = px.line(
                        df_time, x='fecha_completa', y='tiempo_respuesta',
                        color_discrete_sequence=["#fbbc05"], markers=True,
                        hover_data={"fecha_completa": "|%d-%m-%Y %H:%M:%S"} 
                    )
                    fig_time.update_layout(xaxis_title="Fecha y Hora de la Consulta", yaxis_title="Segundos de Respuesta", dragmode=False, hovermode="x unified", margin=dict(l=0, r=0, t=30, b=0))
                    fig_time.update_xaxes(fixedrange=True, tickformat="%d-%b %H:%M")
                    fig_time.update_yaxes(fixedrange=True)
                    st.plotly_chart(fig_time, use_container_width=True, config={'displayModeBar': False})
                
                st.divider()
                st.markdown("#### :material/policy: Auditoría y Evaluación de Jefatura")
                
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(label="Descargar datos en Excel (CSV)", data=csv, file_name="auditoria_chatbot.csv", mime="text/csv", icon=":material/download:")

                for d in reversed(data_int[-30:]): 
                    calif_alumno = f"{int(d['calificacion'])} ⭐" if pd.notna(d.get('calificacion')) else "Sin calificar"
                    calif_jefe = f"{int(d['calificacion_jefatura'])} ⭐" if pd.notna(d.get('calificacion_jefatura')) else "Pendiente de revisión"
                    
                    with st.expander(f"👤 {d['fecha'][:10]} | Pregunta: {d['pregunta'][:50]}..."):
                        st.markdown(f"**Consulta del estudiante:** {d['pregunta']}")
                        st.info(f"**Respuesta del Bot:** {d['respuesta']}", icon=":material/forum:")
                        st.markdown(f"**Evaluación del Alumno:** {calif_alumno}")
                        
                        st.divider()
                        st.markdown("**Evaluación de Jefatura:**")
                        st.write(f"Estado actual: **{calif_jefe}**")
                        
                        feedback_jefatura = st.feedback("stars", key=f"eval_jef_{d['id']}")
                        if feedback_jefatura is not None:
                            estrellas_jefe = feedback_jefatura + 1
                            if d.get('calificacion_jefatura') != estrellas_jefe:
                                try:
                                    supabase.table("interacciones").update({"calificacion_jefatura": estrellas_jefe}).eq("id", d["id"]).execute()
                                    st.toast(f"Calificación guardada ({estrellas_jefe} ⭐)", icon=":material/check_circle:")
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error al guardar: {e}", icon=":material/error:")
            else:
                st.info("Aún no hay interacciones registradas en la base de datos.", icon=":material/info:")
        except Exception as e:
            st.error(f"Error procesando métricas: {e}", icon=":material/error:")

    elif opcion_elegida == "Entrenar":
        st.markdown("### :material/psychology: Entrenar Bot (Reglas Directas)")
        with st.form("form_reglas"):
            tema = st.text_input("Tema o pregunta clave (ej: Práctica Profesional)")
            respuesta_exigida = st.text_area("¿Qué debe responder el bot obligatoriamente?")
            if st.form_submit_button("Guardar Regla", icon=":material/save:"):
                if tema and respuesta_exigida:
                    try:
                        supabase.table("reglas_jefatura").insert({"tema_o_pregunta": tema, "respuesta_correcta_exigida": respuesta_exigida, "creado_por_id": st.session_state.usuario_id}).execute()
                        st.success("Regla aprendida exitosamente.", icon=":material/check_circle:")
                        st.rerun()
                    except Exception as e: st.error(f"Error: {e}", icon=":material/error:")
        try:
            reglas_bd = supabase.table("reglas_jefatura").select("*").execute()
            if reglas_bd.data: st.dataframe(reglas_bd.data, use_container_width=True, hide_index=True)
        except Exception: pass

    elif opcion_elegida == "FAQs":
        st.markdown("### :material/contact_support: Gestor Inteligente de FAQs")
        if "draft_pregunta" not in st.session_state: st.session_state.draft_pregunta = ""
        if "draft_respuesta" not in st.session_state: st.session_state.draft_respuesta = ""

        pregunta_input = st.text_input("Pregunta del estudiante:", value=st.session_state.draft_pregunta)

        if st.button("Generar Borrador con IA", type="primary", use_container_width=True, icon=":material/auto_awesome:"):
            if pregunta_input:
                st.session_state.draft_pregunta = pregunta_input
                texto_generado = ""
                placeholder = st.empty()
                try:
                    mensajes_borrador = [{"role": "system", "content": generar_prompt_sistema(st.session_state.usuario_nombre, st.session_state.usuario_carrera)}, {"role": "user", "content": pregunta_input}]
                    try:
                        respuesta_stream = cliente_llm.chat.completions.create(model="unsloth/Qwen3.6-35B-A3B-MTP-GGUF", messages=mensajes_borrador, temperature=0.1, max_tokens=2000, stream=True)
                    except Exception:
                        respuesta_stream = cliente_respaldo.chat.completions.create(model="gemini-2.5-flash", messages=mensajes_borrador, temperature=0.1, max_tokens=2000, stream=True)
                        
                    for chunk in respuesta_stream:
                        if chunk.choices[0].delta.content is not None:
                            texto_generado += chunk.choices[0].delta.content
                            placeholder.info(f"**Escribiendo borrador...**\n\n{texto_generado}▌", icon=":material/memory:")
                    if texto_generado.strip(): st.session_state.draft_respuesta = texto_generado
                    else:
                        st.warning("Servidor no generó texto.", icon=":material/warning:")
                        st.session_state.draft_respuesta = " "
                    time.sleep(1)
                    st.rerun()
                except Exception as e: st.error(f"Error de servidor: {e}", icon=":material/error:")

        if st.session_state.draft_respuesta:
            with st.form("form_guardar_faq"):
                respuesta_editada = st.text_area("Modifica la respuesta:", value=st.session_state.draft_respuesta.strip(), height=200)
                colA, colB = st.columns(2)
                with colA:
                    if st.form_submit_button("Aprobar y Publicar FAQ", use_container_width=True, type="primary"):
                        try:
                            supabase.table("faqs").insert({"pregunta": st.session_state.draft_pregunta, "respuesta": respuesta_editada, "estado": "activa", "creado_por_id": st.session_state.usuario_id}).execute()
                            st.success("FAQ guardada exitosamente.", icon=":material/check_circle:")
                            st.session_state.draft_pregunta = ""
                            st.session_state.draft_respuesta = ""
                            time.sleep(1)
                            st.rerun()
                        except Exception as e: st.error(f"Error BD: {e}", icon=":material/error:")
                with colB:
                    if st.form_submit_button("Descartar", use_container_width=True):
                        st.session_state.draft_pregunta = ""
                        st.session_state.draft_respuesta = ""
                        st.rerun()
        st.divider()
        try:
            faqs_bd = supabase.table("faqs").select("*").execute()
            if faqs_bd.data: st.dataframe(faqs_bd.data, use_container_width=True, hide_index=True, column_order=["estado", "pregunta", "respuesta"])
        except Exception: pass
        
    elif opcion_elegida == "PDFs":
        st.markdown("### :material/folder_managed: Gestor Documental Autónomo (Cloud Storage)")
        st.write("Ahora todos los reglamentos se guardan de forma permanente y segura en la nube de Supabase.")
        
        # --- INICIALIZAR VARIABLES DEL EDITOR ---
        if "archivo_a_editar" not in st.session_state:
            st.session_state.archivo_a_editar = None
        if "contenido_edicion" not in st.session_state:
            st.session_state.contenido_edicion = ""

        archivo_pdf = st.file_uploader("Arrastra aquí un documento PDF nuevo", type=["pdf"])

        if archivo_pdf is not None:
            col_pdf, col_texto = st.columns([1, 1])
            
            pdf_bytes = archivo_pdf.read()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")

            with col_pdf:
                st.markdown("#### Vista Previa del Documento")
                if len(doc) > 0:
                    with st.container(height=550):
                        for i in range(len(doc)):
                            pix = doc[i].get_pixmap(matrix=fitz.Matrix(2, 2))
                            img_data = pix.tobytes("png")
                            st.image(img_data, use_container_width=True)
                else:
                    st.warning("El PDF parece estar vacío.", icon=":material/warning:")

            with col_texto:
                st.markdown("#### Extracción y Limpieza")
                try:
                    texto_extraido = ""
                    for pagina in doc:
                        texto_extraido += pagina.get_text("text") + "\n"
                    
                    with st.form("form_guardar_txt"):
                        st.caption("Edita este texto si es necesario antes de guardarlo en la nube.")
                        texto_final = st.text_area("Texto Extraído (Editable):", value=texto_extraido, height=400)
                        
                        nombre_limpio = archivo_pdf.name.replace(".pdf", "").replace(" ", "_").lower()
                        nombre_archivo = st.text_input("Nombre de archivo (sin el .txt):", value=nombre_limpio)
                        
                        if st.form_submit_button("Subir a Supabase Storage", type="primary", use_container_width=True, icon=":material/cloud_upload:"):
                            if nombre_archivo:
                                nombre_txt = f"{nombre_archivo}.txt"
                                texto_bytes = texto_final.encode('utf-8')
                                
                                # Si ya existe, lo borramos para sobrescribir
                                try:
                                    supabase.storage.from_("conocimiento").remove([nombre_txt])
                                except: pass
                                
                                supabase.storage.from_("conocimiento").upload(file=texto_bytes, path=nombre_txt, file_options={"content-type": "text/plain"})
                                    
                                st.success(f"¡Documento '{nombre_txt}' integrado exitosamente en la nube!", icon=":material/check_circle:")
                                time.sleep(2.5)
                                st.rerun() 
                            else:
                                st.error("Debes asignarle un nombre al archivo.", icon=":material/error:")
                except Exception as e:
                    st.error(f"Error al procesar: {e}", icon=":material/error:")
                    
        st.divider()
        st.markdown("#### :material/cloud: Archivos en la Nube (Base de Conocimiento)")
        
        # --- EDITOR DE TEXTO EN VIVO ---
        if st.session_state.archivo_a_editar:
            st.info(f"Modificando el archivo: **{st.session_state.archivo_a_editar}**", icon=":material/edit_note:")
            with st.form("form_edicion_directa"):
                nuevo_texto = st.text_area("Contenido del documento:", value=st.session_state.contenido_edicion, height=350)
                col_save, col_cancel = st.columns(2)
                
                with col_save:
                    if st.form_submit_button("Guardar Cambios en la Nube", type="primary", use_container_width=True, icon=":material/save:"):
                        texto_bytes = nuevo_texto.encode('utf-8')
                        try:
                            supabase.storage.from_("conocimiento").update(file=texto_bytes, path=st.session_state.archivo_a_editar, file_options={"content-type": "text/plain"})
                        except:
                            supabase.storage.from_("conocimiento").remove([st.session_state.archivo_a_editar])
                            supabase.storage.from_("conocimiento").upload(file=texto_bytes, path=st.session_state.archivo_a_editar, file_options={"content-type": "text/plain"})
                            
                        st.success("¡Archivo actualizado correctamente!", icon=":material/check_circle:")
                        st.session_state.archivo_a_editar = None
                        time.sleep(1.5)
                        st.rerun()
                
                with col_cancel:
                    if st.form_submit_button("Cancelar", use_container_width=True, icon=":material/cancel:"):
                        st.session_state.archivo_a_editar = None
                        st.rerun()
            st.divider()
        # -------------------------------

        try:
            lista_archivos_nube = supabase.storage.from_("conocimiento").list()
            archivos_txt = [f['name'] for f in lista_archivos_nube if f['name'].endswith(".txt")]
            
            if archivos_txt:
                for arch in archivos_txt:
                    colA, colB, colC = st.columns([6, 2, 2])
                    with colA:
                        st.markdown(f":material/description: **{arch}**")
                    with colB:
                        if st.button("Editar", key=f"edit_{arch}", icon=":material/edit:", use_container_width=True):
                            st.session_state.archivo_a_editar = arch
                            datos_archivo = supabase.storage.from_("conocimiento").download(arch)
                            st.session_state.contenido_edicion = datos_archivo.decode('utf-8')
                            st.rerun()
                    with colC:
                        if st.button("Eliminar", key=f"del_{arch}", icon=":material/delete:", use_container_width=True):
                            try:
                                supabase.storage.from_("conocimiento").remove([arch])
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error al eliminar: {e}", icon=":material/error:")
            else:
                st.info("No hay documentos en el Storage de Supabase aún.", icon=":material/info:")
        except Exception as e:
            st.warning("No se pudo conectar al Bucket 'conocimiento' o aún no existe.", icon=":material/warning:")

else:
    def obtener_base64(ruta_imagen):
        if os.path.exists(ruta_imagen):
            with open(ruta_imagen, "rb") as img_file: return base64.b64encode(img_file.read()).decode()
        return ""

    b64_ucn = obtener_base64("logo_ucn.png")
    b64_eic = obtener_base64("logo_eic.png") 

    html_logos = f"""
    <div style="display: flex; flex-direction: row; justify-content: center; align-items: center; gap: 30px; margin-bottom: 10px;">
        {f'<img src="data:image/png;base64,{b64_ucn}" style="height: 55px; width: auto; object-fit: contain;">' if b64_ucn else ''}
        {f'<img src="data:image/png;base64,{b64_eic}" style="height: 50px; width: auto; object-fit: contain;">' if b64_eic else ''}
    </div>
    """
    st.markdown(html_logos, unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center; color: #00b4c8; margin: 0;'>Asistente Virtual EIC</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #e0e0e0; margin-top: 5px; font-size: 1.1em;'>Bienvenido al chatbot de la Escuela de Ingeniería. Consulta normativas, plazos y reglamentos.</p>", unsafe_allow_html=True)
    st.divider()

    for msg in st.session_state.messages:
        if msg["role"] != "system": 
            avatar_img = ":material/school:" if msg["role"] == "assistant" else ":material/person:"
            with st.chat_message(msg["role"], avatar=avatar_img):
                st.markdown(msg["content"])
                if msg["role"] == "assistant" and "db_id" in msg and msg["db_id"] is not None:
                    db_id = msg["db_id"]
                    calificacion = st.feedback("stars", key=f"stars_{db_id}")
                    if calificacion is not None:
                        estrellas = calificacion + 1
                        if st.session_state.calificaciones_guardadas.get(db_id) != estrellas:
                            try:
                                supabase.table("interacciones").update({"calificacion": estrellas}).eq("id", db_id).execute()
                                st.session_state.calificaciones_guardadas[db_id] = estrellas
                                st.toast(f"Calificaste con {estrellas} estrellas", icon=":material/star:")
                            except Exception: pass

    user_input = st.chat_input("Escribe tu duda aquí...")

    if user_input:
        tiempo_actual = time.time()
        if tiempo_actual - st.session_state.ultimo_mensaje_tiempo < 5.0:
            st.warning("Espera 5 segundos.", icon=":material/timer:")
            st.stop()
        if not st.session_state.usuario_id: 
            st.session_state.timestamps_anonimo = [t for t in st.session_state.timestamps_anonimo if tiempo_actual - t < 3600]
            if len(st.session_state.timestamps_anonimo) >= 4:
                st.error("Límite de invitados alcanzado.", icon=":material/block:")
                st.stop() 
            else:
                st.session_state.timestamps_anonimo.append(tiempo_actual)

        st.session_state.ultimo_mensaje_tiempo = tiempo_actual

        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(user_input)
        st.session_state.messages.append({"role": "user", "content": user_input})
        mensajes_api = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]

        with st.chat_message("assistant", avatar=":material/school:"):
            message_placeholder = st.empty()
            full_response = ""
            inicio_llm = time.time()
            
            try:
                with st.spinner("Analizando documentos (Servidor UCN)..."):
                    respuesta = cliente_llm.chat.completions.create(
                        model="unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
                        messages=mensajes_api,
                        temperature=0.1,
                        max_tokens=2000,
                        stream=True 
                    )
                    for chunk in respuesta:
                        if chunk.choices[0].delta.content is not None:
                            full_response += chunk.choices[0].delta.content
                            message_placeholder.markdown(full_response + "▌")
            
            except Exception as e_ucn:
                try:
                    with st.spinner("Servidor UCN ocupado. Conectando al respaldo Gemini..."):
                        respuesta_gemini = cliente_respaldo.chat.completions.create(
                            model="gemini-2.5-flash",
                            messages=mensajes_api,
                            temperature=0.1,
                            max_tokens=2000,
                            stream=True 
                        )
                        for chunk in respuesta_gemini:
                            if chunk.choices[0].delta.content is not None:
                                full_response += chunk.choices[0].delta.content
                                message_placeholder.markdown(full_response + "▌")
                except Exception as e_gemini:
                    st.error("Error crítico: Ambos servidores (UCN y Respaldo) están inactivos en este momento.", icon=":material/cloud_off:")
                    st.stop()
                
            tiempo_total = round(time.time() - inicio_llm, 2)
            if not full_response.strip():
                full_response = "Comprendo tu consulta, pero no tengo esa información específica."
            message_placeholder.markdown(full_response)
            
            categoria_asignada = "Otro"
            try:
                categorias_validas = [
                    "Titulación", "Práctica", "Toma de Ramos", "Reglamentos", 
                    "Beneficios", "Certificados", "Congelación", "Convalidación", 
                    "Malla Curricular", "Minor", "Fechas y Plazos"
                ]
                categorias_texto = ", ".join(categorias_validas) + ", Otro"
                cat_prompt = f"Clasifica la siguiente intención del alumno en UNA de estas categorías: {categorias_texto}. REGLA ESTRICTA: Responde SOLO con el nombre exacto de la categoría.\nPregunta: '{user_input}'"
                
                try:
                    cat_resp = cliente_llm.chat.completions.create(
                        model="unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
                        messages=[{"role": "user", "content": cat_prompt}],
                        temperature=0.0, max_tokens=8, stream=False
                    )
                except Exception:
                    cat_resp = cliente_respaldo.chat.completions.create(
                        model="gemini-2.5-flash",
                        messages=[{"role": "user", "content": cat_prompt}],
                        temperature=0.0, max_tokens=8, stream=False
                    )
                    
                respuesta_bruta = cat_resp.choices[0].message.content.strip().lower()
                
                for cat in categorias_validas:
                    cat_limpia = cat.lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
                    resp_limpia = respuesta_bruta.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
                    
                    if cat_limpia in resp_limpia:
                        categoria_asignada = cat
                        break
                
                if categoria_asignada == "Otro" and ("fecha" in respuesta_bruta or "plazo" in respuesta_bruta or "cuando" in user_input.lower() or "calendario" in respuesta_bruta):
                    categoria_asignada = "Fechas y Plazos"

            except Exception as e: print(f"Error clasificación: {e}")
            
            nuevo_id = None
            try:
                uid = st.session_state.usuario_id if st.session_state.usuario_id else st.session_state.anon_user_id
                res_db = supabase.table("interacciones").insert({
                    "conversacion_id": st.session_state.conversation_id, 
                    "pregunta": user_input, 
                    "respuesta": full_response, 
                    "usuario_id": uid, 
                    "tiempo_respuesta": tiempo_total,
                    "categoria": categoria_asignada
                }).execute()
                if res_db.data: nuevo_id = res_db.data[0]['id']
            except Exception as e: print(f"Error BD: {e}")

            st.session_state.messages.append({"role": "assistant", "content": full_response, "db_id": nuevo_id})
            st.rerun()

    if st.session_state.usuario_id and len(st.session_state.messages) > 1:
        st.markdown("<br><br>", unsafe_allow_html=True)
        with st.expander("¿Te ayudó esta conversación? Déjanos tu sugerencia"):
            with st.form("encuesta_form_bottom"):
                resolvio = st.selectbox("¿Resolviste tu duda principal?", ["Sí", "Parcialmente", "No"])
                comentarios = st.text_area("Comentario o sugerencia:")
                if st.form_submit_button("Enviar Feedback", icon=":material/send:"):
                    try:
                        supabase.table("encuestas_salida").insert({
                            "usuario_id": st.session_state.usuario_id, "resolvio_duda": resolvio, "comentario": comentarios
                        }).execute()
                        st.success("¡Gracias por ayudarnos a mejorar!", icon=":material/thumb_up:")
                    except Exception as e:
                        st.error(f"Error al guardar: {e}", icon=":material/error:")