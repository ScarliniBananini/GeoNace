import streamlit as st
import geopandas as gpd
import folium
import requests

from pathlib import Path
from streamlit_folium import st_folium


# =====================================================
# 1. CONFIGURACIÓN GENERAL
# =====================================================

st.set_page_config(
    page_title="Visualizador de Recintos Hospitalarios",
    page_icon="🏥",
    layout="wide"
)

st.title("Visualizador de Recintos Hospitalarios")
st.write(
    "Consulta la distribución territorial de los recintos "
    "hospitalarios y explora rutas de acceso."
)

# Los archivos geográficos deben estar en la carpeta "datos",
# ubicada junto a este archivo Python.
CARPETA_DATOS = Path(__file__).parent

archivos_shp = sorted(CARPETA_DATOS.glob("*.shp"))

if not archivos_shp:
    st.error(
        "No hay ningún archivo .shp junto a este script. "
        f"Archivos que ve el servidor: {[p.name for p in CARPETA_DATOS.iterdir()]}"
    )
    st.stop()

ARCHIVO_SHP = archivos_shp[0]

# =====================================================
# 2. CARGAR DATOS GEOGRÁFICOS
# =====================================================

@st.cache_data
def cargar_DATOS(ruta_archivo):
    ruta = Path(ruta_archivo)

    if not ruta.exists():
        raise FileNotFoundError(
            f"No se encontró el archivo: {ruta}. "
            "Comprueba que exista la carpeta 'Datos' y que "
            "contenga el Shapefile."
        )

    gdf = gpd.read_file(ruta)

    if gdf.empty:
        raise ValueError("El archivo geográfico no contiene registros.")

    if gdf.crs is None:
        raise ValueError(
            "El Shapefile no tiene un sistema de coordenadas definido. "
            "Comprueba que el archivo .prj esté junto al .shp."
        )

    gdf = gdf.to_crs(epsg=4326)

    gdf = gdf[
        gdf.geometry.notna() & ~gdf.geometry.is_empty
    ].copy()

    if gdf.empty:
        raise ValueError("No hay geometrías válidas para mostrar.")

    return gdf


try:
    gdf = cargar_datos(ARCHIVO_SHP)
except Exception as error:
    st.error(f"No se pudieron cargar los datos: {error}")
    st.stop()


# =====================================================
# 3. IDENTIFICAR LOS RECINTOS HOSPITALARIOS
# =====================================================

campos = [
    campo for campo in gdf.columns
    if campo != "geometry"
]

if not campos:
    st.error("La base de datos no contiene campos descriptivos.")
    st.stop()

posibles_campos = [
    campo for campo in campos
    if any(
        palabra in campo.lower()
        for palabra in [
            "nombre",
            "hospital",
            "establecimiento",
            "recinto",
            "nom"
        ]
    )
]

campo_inicial = posibles_campos[0] if posibles_campos else campos[0]

st.sidebar.header("Selección de recinto")

campo_nombre = st.sidebar.selectbox(
    "Campo con el nombre del establecimiento:",
    options=campos,
    index=campos.index(campo_inicial)
)

gdf["nombre_mapa"] = (
    gdf[campo_nombre]
    .fillna("Recinto sin nombre")
    .astype(str)
)

# Obtener un punto representativo por cada geometría.
gdf["punto_mapa"] = gdf.geometry.representative_point()

gdf = gdf[
    gdf["punto_mapa"].notna()
].copy()

gdf = gdf.reset_index(drop=True)

if gdf.empty:
    st.error("No hay puntos válidos para representar en el mapa.")
    st.stop()

gdf["identificador"] = range(len(gdf))

gdf["etiqueta"] = (
    gdf["nombre_mapa"]
    + " | Registro "
    + (gdf["identificador"] + 1).astype(str)
)


# =====================================================
# 4. SELECCIONAR HOSPITAL
# =====================================================

st.sidebar.header("Recinto hospitalario")

seleccion = st.sidebar.selectbox(
    "Selecciona un recinto:",
    options=gdf["identificador"].tolist(),
    format_func=lambda i: gdf.loc[i, "etiqueta"]
)

hospital = gdf.loc[seleccion]

lat_hospital = hospital["punto_mapa"].y
lon_hospital = hospital["punto_mapa"].x


# =====================================================
# 5. INDICADORES GENERALES
# =====================================================

col1, col2 = st.columns(2)

col1.metric("Total de recintos", len(gdf))
col2.metric("Recinto seleccionado", hospital["nombre_mapa"])

st.subheader("Distribución territorial de recintos")


# =====================================================
# 6. MAPA GENERAL OPENSTREETMAP
# =====================================================

centro_lat = gdf["punto_mapa"].apply(lambda p: p.y).mean()
centro_lon = gdf["punto_mapa"].apply(lambda p: p.x).mean()

mapa = folium.Map(
    location=[centro_lat, centro_lon],
    zoom_start=6,
    tiles="OpenStreetMap"
)

for _, fila in gdf.iterrows():
    lat = fila["punto_mapa"].y
    lon = fila["punto_mapa"].x

    es_seleccionado = fila["identificador"] == seleccion

    folium.Marker(
        location=[lat, lon],
        tooltip=fila["nombre_mapa"],
        popup=folium.Popup(fila["nombre_mapa"], max_width=300),
        icon=folium.Icon(
            color="green" if es_seleccionado else "red",
            icon="plus-sign",
            prefix="glyphicon"
        )
    ).add_to(mapa)

resultado = st_folium(
    mapa,
    width=None,
    height=550,
    returned_objects=["last_clicked"],
    key="mapa_principal"
)


# =====================================================
# 7. SELECCIONAR ORIGEN
# =====================================================

st.subheader("Análisis de accesibilidad territorial")

st.write(
    "Haz clic sobre el mapa para seleccionar una ubicación de origen. "
    "El destino será el recinto hospitalario seleccionado en el panel lateral."
)


# =====================================================
# 8. CALCULAR DISTANCIA Y TIEMPO DE RUTA
# =====================================================

if resultado and resultado.get("last_clicked"):

    origen = resultado["last_clicked"]

    lat_origen = float(origen["lat"])
    lon_origen = float(origen["lng"])

    st.write("### Ubicación de origen")
    st.write(
        f"Latitud: {lat_origen:.6f} | "
        f"Longitud: {lon_origen:.6f}"
    )

    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{lon_origen},{lat_origen};"
        f"{lon_hospital},{lat_hospital}"
        "?overview=full&geometries=geojson"
    )

    try:
        respuesta = requests.get(url, timeout=30)
        respuesta.raise_for_status()
        datos = respuesta.json()

        if datos.get("code") != "Ok" or not datos.get("routes"):
            st.warning(
                "No se encontró una ruta por carretera "
                "entre los puntos seleccionados."
            )
        else:
            ruta = datos["routes"][0]

            distancia_km = ruta["distance"] / 1000
            tiempo_minutos = ruta["duration"] / 60

            st.markdown("### Resultados del análisis")

            col1, col2, col3 = st.columns(3)

            col1.metric(
                "Distancia por carretera",
                f"{distancia_km:.2f} km"
            )
            col2.metric(
                "Tiempo estimado",
                f"{tiempo_minutos:.0f} minutos"
            )
            col3.metric(
                "Recinto hospitalario",
                hospital["nombre_mapa"]
            )

            mapa_ruta = folium.Map(
                location=[lat_origen, lon_origen],
                zoom_start=12,
                tiles="OpenStreetMap"
            )

            folium.Marker(
                [lat_origen, lon_origen],
                tooltip="Origen",
                popup="Punto de origen",
                icon=folium.Icon(
                    color="blue",
                    icon="home",
                    prefix="glyphicon"
                )
            ).add_to(mapa_ruta)

            folium.Marker(
                [lat_hospital, lon_hospital],
                tooltip=hospital["nombre_mapa"],
                popup=hospital["nombre_mapa"],
                icon=folium.Icon(
                    color="green",
                    icon="plus-sign",
                    prefix="glyphicon"
                )
            ).add_to(mapa_ruta)

            folium.GeoJson(
                ruta["geometry"],
                name="Ruta por carretera",
                style_function=lambda feature: {
                    "color": "blue",
                    "weight": 5,
                    "opacity": 0.8
                }
            ).add_to(mapa_ruta)

            mapa_ruta.fit_bounds([
                [lat_origen, lon_origen],
                [lat_hospital, lon_hospital]
            ])

            st.markdown("### Visualización de la ruta")

            st_folium(
                mapa_ruta,
                width=None,
                height=550,
                key="mapa_ruta"
            )

    except requests.exceptions.RequestException as error:
        st.error(f"Error de conexión con OSRM: {error}")

    except Exception as error:
        st.error(f"Error al calcular la ruta: {error}")

else:
    st.info("Haz clic en el mapa para seleccionar el punto de origen.")


# =====================================================
# 9. INFORMACIÓN DE LOS DATOS
# =====================================================

with st.expander("Información de los datos geográficos"):

    st.write("Total de recintos:", len(gdf))
    st.write("Campos disponibles:", campos)

    st.dataframe(
        gdf.drop(
            columns=["geometry", "punto_mapa"],
            errors="ignore"
        ),
        use_container_width=True
    )


# =====================================================
# 10. NOTAS Y FUENTES
# =====================================================

st.divider()

st.caption(
    "Visualizador de Recintos Hospitalarios | "
    "OpenStreetMap | OSRM | Streamlit"
)

st.caption(
    "Las distancias y tiempos corresponden a estimaciones de ruta "
    "por carretera. No representan condiciones de tráfico en tiempo real "
    "ni tiempos de respuesta de emergencia."
)
