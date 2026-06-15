import streamlit as st
import os
import uuid
import time
from supabase import create_client, Client
from openai import OpenAI

# --- 1. CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Asistente EIC UCN", page_icon=":material/school:", layout="wide")

# CSS MEJORADO PARA LA ESTÉTICA PRO Y VERSIÓN MÓVIL
st.markdown("""
    <style>
        .block-container {
            padding-top: 2rem;
            padding-bottom: 1rem;
        }
        [data-testid="stSidebar"] {
            background-color: #1a2a3a;
        }
        .stChatMessageAvatar {
            border-radius: 4px;
        }
        [data-testid="stSidebar"] div[data-baseweb="input"], 
        [data-testid="stSidebar"] div[data-baseweb="select"] {
            background-color: rgba(255, 255, 255, 0.05) !important;
            border: 1px solid #00b4c8 !important;
            border-radius: 6px !important;
        }
        [data-testid="stSidebar"] input {
            color: white !important;
        }
        /* CORRECCIÓN MÓVIL: Centrar todas las imágenes (logos) automáticamente */
        div[data-testid="stImage"] {
            display: flex;
            justify-content: center;
            align-items: center;
        }
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

# --- 2. LECTURA DINÁMICA DE CONOCIMIENTO ---
def cargar_base_conocimiento():
    ruta_carpeta = "base_de_conocimiento"
    texto_combinado = ""
    if not os.path.exists(ruta_carpeta):
        os.makedirs(ruta_carpeta)
    for archivo in os.listdir(ruta_carpeta):
        if archivo.endswith(".txt"):
            ruta_completa = os.path.join(ruta_carpeta, archivo)
            with open(ruta_completa, "r", encoding="utf-8") as f:
                texto_combinado += f"\n\n--- INICIO DE {archivo.upper()} ---\n{f.read()}\n--- FIN DE {archivo.upper()} ---\n"
    return texto_combinado

documentos_actualizados = cargar_base_conocimiento()

def generar_prompt_sistema(nombre=None, carrera=None):
    prompt_base = f"""
Eres el Asistente Virtual Oficial de la Jefatura de Carrera de Ingeniería (UCN, Sede Coquimbo). 
Tu fuente de información para dar respuestas proviene EXCLUSIVAMENTE de los siguientes documentos:

{documentos_actualizados}

INSTRUCCIONES:
1. Traducción Semántica: Deduce la intención real si el alumno usa jerga (ej: congelar = retiro temporal).
2. Modo Consultivo: Si no existe el trámite exacto, ofrece opciones relacionadas.
3. Fallo Total: Si no hay información relacionada a la normativa académica, indica que no tienes esa información y que contacten a Jefatura.
"""
    if nombre and carrera:
        prompt_base += f"\n\n[CONTEXTO DEL USUARIO ACTUAL]\nEstudiante: {nombre}\nCarrera: {carrera}."
    return prompt_base

# --- 3. INICIALIZAR MEMORIA ---
if "usuario_id" not in st.session_state:
    st.session_state.usuario_id = None
if "usuario_nombre" not in st.session_state:
    st.session_state.usuario_nombre = None
if "usuario_carrera" not in st.session_state:
    st.session_state.usuario_carrera = None
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": generar_prompt_sistema()}]
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = str(uuid.uuid4())
if "calificaciones_guardadas" not in st.session_state:
    st.session_state.calificaciones_guardadas = {}
if "timestamps_anonimo" not in st.session_state:
    st.session_state.timestamps_anonimo = [] 
if "ultimo_mensaje_tiempo" not in st.session_state:
    st.session_state.ultimo_mensaje_tiempo = 0.0 

if "anon_user_id" not in st.session_state:
    try:
        resp = supabase.table("usuarios").select("id").eq("correo", "anonimo@ucn.cl").execute()
        if len(resp.data) > 0:
            st.session_state.anon_user_id = resp.data[0]['id']
        else:
            nuevo_anon = {"correo": "anonimo@ucn.cl", "nombre": "Estudiante Anónimo", "contrasena": "anonimo123"}
            resp_insert = supabase.table("usuarios").insert(nuevo_anon).execute()
            st.session_state.anon_user_id = resp_insert.data[0]['id']
    except Exception:
        st.session_state.anon_user_id = None

# --- 4. BARRA LATERAL ---
with st.sidebar:
    if not st.session_state.usuario_id:
        st.markdown("### :material/account_circle: Acceso Estudiantes")
        st.caption("Inicia sesión para guardar tu historial y tener consultas ilimitadas.")
        
        tab_login, tab_registro = st.tabs(["Ingresar", "Registrarse"])
        with tab_login:
            correo_login = st.text_input("Correo Institucional:", key="login_correo")
            pass_login = st.text_input("Contraseña:", type="password", key="login_pass")
            if st.button("Acceder", type="primary", use_container_width=True, icon=":material/login:"):
                try:
                    resp = supabase.table("usuarios").select("*").eq("correo", correo_login).eq("contrasena", pass_login).execute()
                    if len(resp.data) > 0:
                        st.session_state.usuario_id = resp.data[0]['id']
                        st.session_state.usuario_nombre = resp.data[0]['nombre']
                        st.session_state.usuario_carrera = resp.data[0].get('carrera', 'Ingeniería')
                        
                        st.session_state.conversation_id = str(uuid.uuid4())
                        st.session_state.messages = [{"role": "system", "content": generar_prompt_sistema(st.session_state.usuario_nombre, st.session_state.usuario_carrera)}]
                        st.rerun()
                    else:
                        st.error("Credenciales incorrectas.", icon=":material/error:")
                except Exception:
                    st.error("Error de base de datos.", icon=":material/cloud_off:")
        
        with tab_registro:
            nombre_reg = st.text_input("Nombre Completo:", key="reg_nombre")
            correo_reg = st.text_input("Correo Institucional:", key="reg_correo")
            pass_reg = st.text_input("Contraseña:", type="password", key="reg_pass")
            carrera_reg = st.selectbox("Carrera:", ["Ingeniería Civil Industrial", "Ingeniería Civil en Computación e Informática", "Ingeniería en Información y Control de Gestión", "Otra"])
            
            if st.button("Crear Cuenta", use_container_width=True, icon=":material/person_add:"):
                # CORRECCIÓN CORREOS: Validar que termine en ucn.cl o alumnos.ucn.cl
                correo_limpio = correo_reg.strip().lower()
                if not (correo_limpio.endswith("@ucn.cl") or correo_limpio.endswith("@alumnos.ucn.cl")):
                    st.error("Solo se permiten correos institucionales (@ucn.cl o @alumnos.ucn.cl).", icon=":material/warning:")
                else:
                    try:
                        check = supabase.table("usuarios").select("id").eq("correo", correo_limpio).execute()
                        if len(check.data) > 0:
                            st.warning("El correo ya está registrado.", icon=":material/warning:")
                        else:
                            supabase.table("usuarios").insert({"correo": correo_limpio, "contrasena": pass_reg, "nombre": nombre_reg, "carrera": carrera_reg}).execute()
                            st.success("Cuenta creada con éxito. Por favor ingresa.", icon=":material/check_circle:")
                    except Exception:
                        st.error("Error al registrar.", icon=":material/error:")

    else:
        with st.container(border=True):
            st.markdown(f"#### :material/person: {st.session_state.usuario_nombre}")
            st.caption(f":material/school: {st.session_state.usuario_carrera}")
            if st.button("Cerrar Sesión", icon=":material/logout:", use_container_width=True):
                st.session_state.usuario_id = None
                st.session_state.usuario_nombre = None
                st.session_state.usuario_carrera = None
                st.session_state.messages = [{"role": "system", "content": generar_prompt_sistema()}]
                st.session_state.conversation_id = str(uuid.uuid4())
                st.rerun()
        
        if st.button("Nueva Conversación", icon=":material/add:", type="primary", use_container_width=True):
            st.session_state.conversation_id = str(uuid.uuid4())
            st.session_state.messages = [{"role": "system", "content": generar_prompt_sistema(st.session_state.usuario_nombre, st.session_state.usuario_carrera)}]
            st.rerun()
            
        st.divider()
        
        st.markdown("### :material/history: Recientes")
        
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

# --- 5. ENCABEZADO PRINCIPAL ---
col1, col2, col3 = st.columns([1, 4, 1])

with col1:
    if os.path.exists("logo_ucn.png"):
        st.image("logo_ucn.png", width=120) 

with col2:
    st.markdown("<h2 style='text-align: center; color: #00b4c8; margin: 0; padding-top: 10px;'>Asistente Virtual EIC</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #e0e0e0; margin-top: 5px; font-size: 1.1em;'>Bienvenido al chatbot de la Escuela de Ingeniería. Consulta normativas, plazos y reglamentos.</p>", unsafe_allow_html=True)

with col3:
    if os.path.exists("logo_eic.png"):
        st.image("logo_eic.png", width=140) 
    elif os.path.exists("logo_eic.svg"):
        st.image("logo_eic.svg", width=140)

st.divider()

# --- 6. CHAT Y FEEDBACK ---
for msg in st.session_state.messages:
    if msg["role"] != "system": 
        with st.chat_message(msg["role"]):
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
                        except Exception:
                            pass

user_input = st.chat_input("Escribe tu duda aquí...")

if user_input:
    tiempo_actual = time.time()
    if tiempo_actual - st.session_state.ultimo_mensaje_tiempo < 5.0:
        st.warning("Estás enviando mensajes muy rápido. Por favor, espera 5 segundos.", icon=":material/timer:")
        st.stop()

    if not st.session_state.usuario_id: 
        st.session_state.timestamps_anonimo = [t for t in st.session_state.timestamps_anonimo if tiempo_actual - t < 3600]
        if len(st.session_state.timestamps_anonimo) >= 4:
            st.error("Límite de invitados alcanzado. Inicia sesión en el menú lateral para continuar.", icon=":material/lock:")
            st.stop() 
        else:
            st.session_state.timestamps_anonimo.append(tiempo_actual)

    st.session_state.ultimo_mensaje_tiempo = tiempo_actual

    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    mensajes_api = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_response = ""
        
        inicio_llm = time.time()
        
        try:
            with st.spinner("Analizando documentos..."):
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
            
            fin_llm = time.time()
            tiempo_total = round(fin_llm - inicio_llm, 2)
            
            if not full_response.strip():
                full_response = "Comprendo tu consulta, pero no tengo esa información específica en los reglamentos."
                
            message_placeholder.markdown(full_response)
            
            nuevo_id = None
            try:
                user_id_to_save = st.session_state.usuario_id if st.session_state.usuario_id else st.session_state.anon_user_id
                datos_insercion = {
                    "conversacion_id": st.session_state.conversation_id,
                    "pregunta": user_input,
                    "respuesta": full_response,
                    "usuario_id": user_id_to_save,
                    "tiempo_respuesta": tiempo_total 
                }
                respuesta_db = supabase.table("interacciones").insert(datos_insercion).execute()
                if respuesta_db.data and len(respuesta_db.data) > 0:
                    nuevo_id = respuesta_db.data[0]['id']
            except Exception as e:
                st.error(f"Error BD: {e}", icon=":material/database:")

            st.session_state.messages.append({
                "role": "assistant", 
                "content": full_response,
                "db_id": nuevo_id
            })
            st.rerun()

        except Exception as e:
            st.error(f"Error de servidor: {e}", icon=":material/cloud_off:")

# --- 7. ENCUESTA DE SALIDA ---
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
                except Exception:
                    st.error("Error al guardar.", icon=":material/error:")