import argparse
import ftplib
import html
import io
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from PIL import Image

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Fuente JSON de noticias de Inteligencia Artificial (TechCrunch)
DEFAULT_FEEDS = [
    {
        "name": "TechCrunch AI",
        "url": "https://techcrunch.com/wp-json/wp/v2/posts?categories=577047203&per_page=50",
    },
]

# Cadena priorizada de modelos en Google Gemini API
GEMINI_MODELS_PRIORITY = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemma-4-31b-it",
    "gemini-2.5-flash",
]

# Cadena priorizada de modelos gratuitos en Groq API (https://console.groq.com/docs/rate-limits)
GROQ_MODELS_PRIORITY = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
]

# Cadena priorizada de modelos gratuitos en OpenRouter API (https://openrouter.ai/docs/quickstart)
OPENROUTER_MODELS_PRIORITY = [
    "openrouter/free",
    "qwen/qwen3-235b-a22b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
]

DEFAULT_PIXABAY_KEY = "46784606-438ba39acca420863fed8c139"

DEFAULT_FTP_HOST = "ftp.goex-ia.com"
DEFAULT_FTP_PORT = 21
DEFAULT_FTP_USER = "bauto_marta@goex-ia.com"
DEFAULT_FTP_PASSWORD = "Marta IA Goex"
DEFAULT_FTP_BLOG_DIR = "/goex-ia.com/public_html/blog"

MESES_CORTOS_ES = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
SEEN_NEWS_FILE = REPO_ROOT / "seen_news.txt"
TEMPLATE_FILE = REPO_ROOT / "templates" / "plantilla-nuevo-articulo.html"
LAST_PUBLISHED_FILE = REPO_ROOT / "last_published_blogs.json"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Registro en memoria de imágenes de Pixabay ya usadas en la ejecución actual para no repetirlas
USED_PIXABAY_IDS: Set[int] = set()


def get_current_date_short_es() -> str:
    """Devuelve la fecha actual en formato corto español para la plantilla (ej: '18 Nov 2026')."""
    now_utc = datetime.now(timezone.utc)
    try:
        madrid_tz = ZoneInfo("Europe/Madrid")
        now_local = now_utc.astimezone(madrid_tz)
    except Exception:
        now_local = now_utc

    mes = MESES_CORTOS_ES.get(now_local.month, "Nov")
    return f"{now_local.day:02d} {mes} {now_local.year}"


def estimate_reading_time(html_content: str) -> str:
    """Calcula el tiempo estimado de lectura en minutos a partir del texto del artículo."""
    text = clean_html_to_text(html_content)
    words = len(text.split())
    mins = max(2, round(words / 200))
    return f"{mins} min"


def load_seen_guids(file_path: Path) -> Set[str]:
    """Carga los GUIDs de noticias ya procesadas desde seen_news.txt."""
    if not file_path.exists():
        return set()
    seen: Set[str] = set()
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                seen.add(cleaned)
    return seen


def append_seen_guid(file_path: Path, guid: str) -> None:
    """Añade un GUID al archivo seen_news.txt."""
    with file_path.open("a", encoding="utf-8") as f:
        f.write(f"{guid}\n")


def clean_html_to_text(raw_html: str) -> str:
    """Convierte HTML de WordPress en texto limpio para enviar al LLM."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_random_pixabay_image(pixabay_api_key: str) -> Optional[Tuple[bytes, str]]:
    """
    Obtiene una imagen aleatoria de alta calidad de Pixabay usando la query solicitada:
    https://pixabay.com/api/?key=...&q=artificial-intelligenc&image_type=photos&orientation=horizontal&min_width=1280&min_height=720&per_page=200&safesearch=true&page=1
    """
    key = (pixabay_api_key or DEFAULT_PIXABAY_KEY).strip()
    candidate_urls = [
        (
            f"https://pixabay.com/api/?key={key}"
            f"&q=artificial-intelligenc&image_type=photos&orientation=horizontal"
            f"&min_width=1280&min_height=720&per_page=200&safesearch=true&page=1"
        ),
        (
            f"https://pixabay.com/api/?key={key}"
            f"&q=artificial-intelligenc&image_type=photo&orientation=horizontal"
            f"&min_width=1280&min_height=720&per_page=200&safesearch=true&page=1"
        ),
        (
            f"https://pixabay.com/api/?key={key}"
            f"&q=artificial+intelligence&image_type=photo&orientation=horizontal"
            f"&min_width=1280&min_height=720&per_page=200&safesearch=true&page=1"
        ),
    ]

    hits: List[Dict[str, Any]] = []
    for api_url in candidate_urls:
        try:
            logger.info("Consultando API de Pixabay...")
            resp = requests.get(api_url, headers=HTTP_HEADERS, timeout=25)
            if not resp.ok:
                logger.warning("Pixabay devolvió HTTP %d, probando variante...", resp.status_code)
                continue
            data = resp.json()
            page_hits = data.get("hits") or []
            if isinstance(page_hits, list) and len(page_hits) > 0:
                hits = page_hits
                break
        except Exception as exc:
            logger.warning("Fallo consultando Pixabay: %s", exc)

    if not hits:
        logger.warning("No se encontraron imágenes en Pixabay.")
        return None

    available_hits = [h for h in hits if h.get("id") not in USED_PIXABAY_IDS] or hits
    selected_hit = random.choice(available_hits)
    hit_id = selected_hit.get("id")
    if isinstance(hit_id, int):
        USED_PIXABAY_IDS.add(hit_id)

    image_url = (
        selected_hit.get("fullHDURL")
        or selected_hit.get("largeImageURL")
        or selected_hit.get("imageURL")
        or selected_hit.get("webformatURL")
    )
    if not image_url:
        return None

    try:
        logger.info(
            "Descargando imagen aleatoria de alta calidad de Pixabay (id=%s): %s",
            hit_id,
            image_url,
        )
        img_resp = requests.get(
            image_url,
            headers={
                "User-Agent": HTTP_HEADERS["User-Agent"],
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
            timeout=30,
        )
        img_resp.raise_for_status()

        with Image.open(io.BytesIO(img_resp.content)) as img:
            if img.mode not in ("RGB", "L"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if "A" in img.getbands():
                    background.paste(img, mask=img.split()[-1])
                else:
                    background.paste(img)
                img = background
            elif img.mode == "L":
                img = img.convert("RGB")

            output_buffer = io.BytesIO()
            img.save(output_buffer, format="JPEG", quality=92, optimize=True)
            img_bytes = output_buffer.getvalue()
            logger.info(
                "Imagen de Pixabay procesada en alta calidad (%dx%d px, %d bytes).",
                img.width,
                img.height,
                len(img_bytes),
            )
            return img_bytes, image_url
    except Exception as exc:
        logger.warning("Error descargando/procesando imagen de Pixabay (%s): %s", image_url, exc)
        return None


def fetch_feed_posts(feed: Dict[str, str]) -> List[Dict[str, Any]]:
    """Descarga y normaliza las entradas desde el endpoint WP REST JSON de TechCrunch."""
    name = feed["name"]
    url = feed["url"]
    logger.info("Consultando fuente: %s (%s)", name, url)

    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("Error al obtener noticias de %s: %s", name, exc)
        return []

    if not isinstance(data, list):
        logger.warning("Formato inesperado en %s (se esperaba una lista JSON).", name)
        return []

    normalized_posts: List[Dict[str, Any]] = []
    for item in data:
        guid = (
            item.get("guid", {}).get("rendered")
            or item.get("link")
            or str(item.get("id", ""))
        )
        guid = html.unescape(str(guid)).strip()
        if not guid:
            continue

        raw_title = item.get("title", {}).get("rendered", "")
        raw_content = item.get("content", {}).get("rendered", "")
        title = clean_html_to_text(raw_title)
        content_text = clean_html_to_text(raw_content)

        if not title or not content_text:
            continue

        normalized_posts.append(
            {
                "guid": guid,
                "source_name": name,
                "source_url": item.get("link", guid),
                "date": item.get("date", ""),
                "title": title,
                "content_text": content_text,
            }
        )

    logger.info("Se obtuvieron %d noticias válidas de %s.", len(normalized_posts), name)
    return normalized_posts


def build_prompt(news_item: Dict[str, Any]) -> str:
    """Construye el prompt para redactar el artículo del blog en español para GOEX."""
    return f"""Eres un redactor experto en Inteligencia Artificial, automatización de procesos y estrategia digital para empresas en España, y especialista en posicionamiento SEO para el blog de GOEX (goex-ia.com).

A continuación te proporciono una noticia reciente sobre tecnología e Inteligencia Artificial:
- Fuente original: {news_item['source_name']}
- Título original: {news_item['title']}
- Contenido de la noticia:
\"\"\"
{news_item['content_text'][:12000]}
\"\"\"

TU TAREA:
Reescribe, traduce y transforma esta noticia en un artículo de blog en ESPAÑOL 100% original, atractivo, riguroso y optimizado para SEO, pensado para directivos, emprendedores y empresas que buscan aplicar IA, agentes de voz y automatización en sus negocios.

REQUISITOS DEL ARTÍCULO:
1. Redacta íntegramente en español profesional y cercano. No copies frases literalmente: reelabora la información con voz propia orientada a impacto empresarial.
2. Mantén todos los datos técnicos, cifras, nombres de modelos/empresas y conclusiones clave fieles a la noticia original.
3. Estructura el cuerpo del artículo (`content_html`) en HTML limpio (SIN etiquetas <html>, <head>, <body> ni <h1>), utilizando únicamente:
   - Subtítulos `<h2>` y `<h3>` atractivos.
   - Párrafos `<p>` bien redactados y fáciles de leer.
   - Listas `<ul>` / `<li>` con negritas `<strong>` para destacar datos, ventajas o claves importantes.
   - Una sección final sobre qué impacto o aplicación práctica tiene esta novedad para las empresas.
4. Genera también:
   - `title`: Título atractivo en español y optimizado para SEO.
   - `slug`: Slug corto y amigable para el nombre del archivo HTML (solo letras minúsculas sin tildes, números y guiones, ej. `nuevos-agentes-ia-empresas`).
   - `excerpt`: Resumen breve de 2 líneas (entre 140 y 220 caracteres) ideal para la tarjeta de previsualización del blog y la etiqueta meta description.
   - `lead`: Entradilla o párrafo destacado de 2 a 3 frases que introduce el tema del artículo de forma clara y directa debajo del título H1.
   - `category`: Elige exactamente una de estas tres categorías del blog de GOEX según el tema principal: `"Inteligencia Artificial"`, `"Automatización"` o `"Marketing"`.

FORMATO DE RESPUESTA OBLIGATORIO:
Responde ÚNICAMENTE con un objeto JSON válido (sin texto adicional antes ni después) con la siguiente estructura exacta:
{{
  "title": "Título atractivo en español y optimizado para SEO",
  "slug": "titulo-atractivo-optimizado-seo",
  "category": "Inteligencia Artificial",
  "excerpt": "Breve resumen de 2 líneas que aparecerá en la tarjeta de previsualización del blog.",
  "lead": "Entradilla o párrafo destacado que introduce el tema del artículo de forma clara y directa.",
  "content_html": "<p>Introducción...</p><h2>Primer subtítulo</h2><p>Desarrollo...</p><ul><li><strong>Clave 1:</strong> Explicación...</li></ul><h2>Conclusión para empresas</h2><p>Cierre...</p>"
}}
"""


def slugify_text(text: str) -> str:
    """Convierte un texto en un slug limpio apto para nombre de archivo .html."""
    replacements = str.maketrans(
        "áéíóúüñÁÉÍÓÚÜÑ",
        "aeiouunAEIOUUN",
    )
    cleaned = text.translate(replacements).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return cleaned[:65].strip("-") or f"articulo-ia-{int(time.time())}"


def parse_llm_json_response(raw_text: str, fallback_title: str) -> Dict[str, str]:
    """Extrae y valida el JSON devuelto por cualquier LLM (eliminando etiquetas <think> si existen)."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]

    try:
        parsed = json.loads(cleaned)
        title = str(parsed.get("title") or fallback_title).strip()
        excerpt = str(parsed.get("excerpt") or "").strip()
        lead = str(parsed.get("lead") or excerpt).strip()
        category = str(parsed.get("category") or "Inteligencia Artificial").strip()
        if category not in ("Inteligencia Artificial", "Automatización", "Marketing"):
            category = "Inteligencia Artificial"
        raw_slug = str(parsed.get("slug") or title).strip()
        slug = slugify_text(raw_slug)
        content_html = str(parsed.get("content_html") or "").strip()
        if content_html:
            if not excerpt:
                plain = clean_html_to_text(content_html)
                excerpt = (plain[:195] + "...") if len(plain) > 195 else plain
            if not lead:
                lead = excerpt
            return {
                "title": title,
                "excerpt": excerpt,
                "lead": lead,
                "category": category,
                "slug": slug,
                "content_html": content_html,
            }
    except json.JSONDecodeError as exc:
        logger.warning("No se pudo parsear JSON directo de la respuesta del LLM (%s). Aplicando fallback.", exc)

    plain_fallback = clean_html_to_text(cleaned or raw_text)
    excerpt_fallback = (plain_fallback[:195] + "...") if len(plain_fallback) > 195 else plain_fallback
    return {
        "title": fallback_title,
        "excerpt": excerpt_fallback,
        "lead": excerpt_fallback,
        "category": "Inteligencia Artificial",
        "slug": slugify_text(fallback_title),
        "content_html": cleaned or f"<p>{raw_text.strip()}</p>",
    }


def normalize_gemini_api_key(api_key: str) -> str:
    """Si la clave empieza por 'Q.' en lugar de 'AQ.', antepone 'A'."""
    cleaned = api_key.strip()
    if cleaned.startswith("Q."):
        return "A" + cleaned
    return cleaned


def call_openai_compatible_chat(
    endpoint_url: str,
    api_key: str,
    model_name: str,
    prompt: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> str:
    """Realiza una llamada Chat Completions compatible con OpenAI (usado para Groq y OpenRouter)."""
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "Eres un redactor experto en Inteligencia Artificial para empresas y SEO en España. Respondes siempre en JSON válido.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.7,
    }

    resp = requests.post(endpoint_url, headers=headers, json=payload, timeout=90)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:350]}")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Respuesta sin 'choices': {data}")

    content = choices[0].get("message", {}).get("content", "")
    if not content or not str(content).strip():
        raise RuntimeError("El modelo devolvió un contenido vacío.")

    return str(content)


def generate_blog_with_fallbacks(
    news_item: Dict[str, Any],
    gemini_api_key: str,
    groq_api_key: str = "",
    openrouter_api_key: str = "",
) -> Dict[str, str]:
    """Genera un artículo de blog probando en cascada Gemini -> Groq -> OpenRouter."""
    prompt = build_prompt(news_item)
    errors_summary: List[str] = []

    if gemini_api_key.strip():
        effective_gemini_key = normalize_gemini_api_key(gemini_api_key)
        client = genai.Client(api_key=effective_gemini_key)

        for model_name in GEMINI_MODELS_PRIORITY:
            try:
                logger.info(
                    "[Proveedor: Google Gemini API] Probando modelo '%s' para: %s",
                    model_name,
                    news_item["title"],
                )
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                raw_output = response.text or ""
                if not raw_output.strip():
                    raise RuntimeError("Respuesta vacía del modelo.")
                logger.info("Blog generado con éxito usando Google Gemini API (%s).", model_name)
                return parse_llm_json_response(raw_output, fallback_title=news_item["title"])
            except Exception as exc:
                err_str = f"Gemini/{model_name}: {exc}"
                logger.warning("Fallo en %s. Iterando al siguiente modelo...", err_str)
                errors_summary.append(err_str)
                time.sleep(2)

    if groq_api_key.strip():
        groq_endpoint = "https://api.groq.com/openai/v1/chat/completions"
        for model_name in GROQ_MODELS_PRIORITY:
            try:
                logger.info(
                    "[Proveedor: Groq API] Probando modelo '%s' para: %s",
                    model_name,
                    news_item["title"],
                )
                raw_output = call_openai_compatible_chat(
                    endpoint_url=groq_endpoint,
                    api_key=groq_api_key,
                    model_name=model_name,
                    prompt=prompt,
                )
                logger.info("Blog generado con éxito usando Groq API (%s).", model_name)
                return parse_llm_json_response(raw_output, fallback_title=news_item["title"])
            except Exception as exc:
                err_str = f"Groq/{model_name}: {exc}"
                logger.warning("Fallo en %s. Iterando al siguiente modelo...", err_str)
                errors_summary.append(err_str)
                time.sleep(2)

    if openrouter_api_key.strip():
        openrouter_endpoint = "https://openrouter.ai/api/v1/chat/completions"
        for model_name in OPENROUTER_MODELS_PRIORITY:
            try:
                logger.info(
                    "[Proveedor: OpenRouter API] Probando modelo gratuito '%s' para: %s",
                    model_name,
                    news_item["title"],
                )
                raw_output = call_openai_compatible_chat(
                    endpoint_url=openrouter_endpoint,
                    api_key=openrouter_api_key,
                    model_name=model_name,
                    prompt=prompt,
                    extra_headers={
                        "HTTP-Referer": "https://goex-ia.com",
                        "X-Title": "GOEX AI Blog Automation",
                    },
                )
                logger.info("Blog generado con éxito usando OpenRouter API (%s).", model_name)
                return parse_llm_json_response(raw_output, fallback_title=news_item["title"])
            except Exception as exc:
                err_str = f"OpenRouter/{model_name}: {exc}"
                logger.warning("Fallo en %s. Iterando al siguiente modelo...", err_str)
                errors_summary.append(err_str)
                time.sleep(2)

    raise RuntimeError(
        "Todos los proveedores y modelos LLM fallaron al generar el blog. Resumen: "
        + " | ".join(errors_summary)
    )


def render_article_html(
    blog_post: Dict[str, str],
    image_src: str,
    date_str: str,
    reading_time_str: str,
) -> str:
    """
    Integra los datos generados por el LLM y la imagen de Pixabay dentro de
    la plantilla HTML `plantilla-nuevo-articulo.html`.
    """
    if not TEMPLATE_FILE.exists():
        raise FileNotFoundError(f"No se encontró la plantilla HTML en {TEMPLATE_FILE}")

    template_html = TEMPLATE_FILE.read_text(encoding="utf-8")

    title_clean = blog_post["title"].strip()
    excerpt_clean = blog_post["excerpt"].strip()
    lead_clean = (blog_post.get("lead") or excerpt_clean).strip()
    category_clean = (blog_post.get("category") or "Inteligencia Artificial").strip()
    content_html = blog_post["content_html"].strip()

    # Escapar atributos para etiquetas <meta> y alt
    title_escaped = html.escape(title_clean, quote=True)
    excerpt_escaped = html.escape(excerpt_clean, quote=True)
    category_escaped = html.escape(category_clean, quote=True)
    date_escaped = html.escape(date_str, quote=True)
    rt_escaped = html.escape(reading_time_str, quote=True)
    image_escaped = html.escape(image_src, quote=True)

    rendered = template_html

    # 1. <title>
    rendered = re.sub(
        r"<title>.*?</title>",
        f"<title>GOEX · {title_escaped}</title>",
        rendered,
        count=1,
        flags=re.DOTALL,
    )

    # 2. Meta tags que leen api.php y actualizar_blog.py
    rendered = re.sub(
        r'<meta\s+name="description"\s+content="[^"]*"\s*/>',
        f'<meta name="description" content="{excerpt_escaped}" />',
        rendered,
        count=1,
    )
    rendered = re.sub(
        r'<meta\s+name="category"\s+content="[^"]*"\s*/>',
        f'<meta name="category" content="{category_escaped}" />',
        rendered,
        count=1,
    )
    rendered = re.sub(
        r'<meta\s+name="date"\s+content="[^"]*"\s*/>',
        f'<meta name="date" content="{date_escaped}" />',
        rendered,
        count=1,
    )
    rendered = re.sub(
        r'<meta\s+name="reading-time"\s+content="[^"]*"\s*/>',
        f'<meta name="reading-time" content="{rt_escaped}" />',
        rendered,
        count=1,
    )
    rendered = re.sub(
        r'<meta\s+name="image"\s+content="[^"]*"\s*/>',
        f'<meta name="image" content="{image_escaped}" />',
        rendered,
        count=1,
    )

    # 3. Cabecera del artículo (badges, H1, lead)
    rendered = re.sub(
        r'<span class="badge-cat">.*?</span>',
        f'<span class="badge-cat">{category_escaped}</span>',
        rendered,
        count=1,
        flags=re.DOTALL,
    )
    rendered = re.sub(
        r'<span class="meta-item">📅.*?</span>',
        f'<span class="meta-item">📅 {date_escaped}</span>',
        rendered,
        count=1,
        flags=re.DOTALL,
    )
    rendered = re.sub(
        r'<span class="meta-item">⏱.*?</span>',
        f'<span class="meta-item">⏱ {rt_escaped} de lectura</span>',
        rendered,
        count=1,
        flags=re.DOTALL,
    )
    rendered = re.sub(
        r"<h1>.*?</h1>",
        f"<h1>{html.escape(title_clean)}</h1>",
        rendered,
        count=1,
        flags=re.DOTALL,
    )
    rendered = re.sub(
        r'<p class="lead">.*?</p>',
        f'<p class="lead">{html.escape(lead_clean)}</p>',
        rendered,
        count=1,
        flags=re.DOTALL,
    )

    # 4. Imagen principal de portada (.article-cover)
    rendered = re.sub(
        r'(<div class="article-cover">\s*)<img[^>]*/>(\s*</div>)',
        rf'\1<img src="{image_escaped}" alt="{title_escaped}" />\2',
        rendered,
        count=1,
        flags=re.DOTALL,
    )

    # 5. Cuerpo del artículo (.article-body) conservando el bloque .article-cta intacto
    cta_block = (
        '        <div class="article-cta">\n'
        "          <h3>¿Quieres aplicar esto a tu empresa?</h3>\n"
        "          <p>En GOEX analizamos tu operativa y te proponemos soluciones reales de IA y automatización.</p>\n"
        '          <a class="btn-gold" href="../contacto/contacto.html">Agenda una auditoría gratuita →</a>\n'
        "        </div>"
    )
    body_pattern = re.compile(
        r'(<div class="article-body">).*?(<div class="article-cta">.*?</div>\s*</div>)',
        re.DOTALL,
    )
    if body_pattern.search(rendered):
        rendered = body_pattern.sub(
            lambda m: f"{m.group(1)}\n        {content_html}\n\n        {m.group(2)}",
            rendered,
            count=1,
        )
    else:
        rendered = rendered.replace(
            "<h2>Primer subtítulo</h2>",
            f"{content_html}\n{cta_block}",
        )

    return rendered


def ensure_ftp_dir(ftp: ftplib.FTP, remote_dir: str) -> None:
    """Navega o crea los directorios necesarios en el servidor FTP."""
    parts = [p for p in remote_dir.strip("/").split("/") if p]
    ftp.cwd("/")
    for part in parts:
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def upload_article_to_siteground_ftp(
    blog_post: Dict[str, str],
    ftp_host: str,
    ftp_port: int,
    ftp_user: str,
    ftp_password: str,
    ftp_blog_dir: str,
    pixabay_api_key: str,
) -> Dict[str, Any]:
    """
    1. Obtiene una imagen aleatoria de Pixabay.
    2. Sube la imagen a SiteGround por FTP (en `<ftp_blog_dir>/images/<slug>.jpg`) y usa esa ruta en el HTML.
    3. Genera el HTML final a partir de `plantilla-nuevo-articulo.html`.
    4. Sube el archivo `<slug>.html` a `<ftp_blog_dir>` (`goex-ia.com > public_html > blog`).
    5. Actualiza también `posts.json` en `<ftp_blog_dir>` para mantener el índice sincronizado junto con `api.php`.
    """
    slug = blog_post["slug"]
    html_filename = f"{slug}.html"
    image_filename = f"{slug}.jpg"

    date_str = get_current_date_short_es()
    reading_time_str = estimate_reading_time(blog_post["content_html"])

    pixabay_result = fetch_random_pixabay_image(pixabay_api_key)
    image_bytes: Optional[bytes] = None
    image_src_in_html = "../images/servicio_ia_automatizacion.jpg"

    if pixabay_result is not None:
        image_bytes, pixabay_remote_url = pixabay_result
        image_src_in_html = f"images/{image_filename}"

    logger.info("Conectando por FTP a %s:%d con usuario %s...", ftp_host, ftp_port, ftp_user)
    ftp = ftplib.FTP()
    ftp.connect(ftp_host, ftp_port, timeout=30)
    ftp.login(ftp_user, ftp_password)
    ftp.encoding = "utf-8"

    try:
        # Asegurar directorio base del blog (/goex-ia.com/public_html/blog)
        ensure_ftp_dir(ftp, ftp_blog_dir)

        # Si tenemos bytes de la imagen de Pixabay, la subimos a /goex-ia.com/public_html/blog/images/<slug>.jpg
        if image_bytes is not None:
            try:
                ensure_ftp_dir(ftp, f"{ftp_blog_dir.rstrip('/')}/images")
                logger.info("Subiendo imagen de Pixabay por FTP: images/%s (%d bytes)...", image_filename, len(image_bytes))
                ftp.storbinary(f"STOR {image_filename}", io.BytesIO(image_bytes))
                ensure_ftp_dir(ftp, ftp_blog_dir)
            except Exception as img_exc:
                logger.warning(
                    "No se pudo subir el archivo de imagen local por FTP (%s), usando URL directa de Pixabay.",
                    img_exc,
                )
                if pixabay_result is not None:
                    image_src_in_html = pixabay_result[1]
                ensure_ftp_dir(ftp, ftp_blog_dir)

        # Renderizar HTML final con la plantilla
        article_html = render_article_html(
            blog_post=blog_post,
            image_src=image_src_in_html,
            date_str=date_str,
            reading_time_str=reading_time_str,
        )
        html_bytes = article_html.encode("utf-8")

        # Evitar colisión si ya existiera un archivo con el mismo nombre
        existing_files: List[str] = []
        try:
            existing_files = ftp.nlst()
        except Exception:
            pass

        if html_filename in existing_files:
            slug = f"{slug}-{int(time.time()) % 10000}"
            html_filename = f"{slug}.html"

        logger.info("Subiendo artículo HTML por FTP a %s/%s (%d bytes)...", ftp_blog_dir, html_filename, len(html_bytes))
        ftp.storbinary(f"STOR {html_filename}", io.BytesIO(html_bytes))

        # Actualizar posts.json remoto como respaldo adicional (además de api.php que indexa dinámicamente)
        try:
            posts_buffer = io.BytesIO()
            current_posts: List[Dict[str, Any]] = []
            try:
                ftp.retrbinary("RETR posts.json", posts_buffer.write)
                raw_posts = posts_buffer.getvalue().decode("utf-8", errors="ignore").strip()
                if raw_posts:
                    parsed_posts = json.loads(raw_posts)
                    if isinstance(parsed_posts, list):
                        current_posts = parsed_posts
            except Exception:
                current_posts = []

            new_entry = {
                "url": html_filename,
                "title": blog_post["title"],
                "excerpt": blog_post["excerpt"],
                "category": blog_post.get("category", "Inteligencia Artificial"),
                "date": date_str,
                "reading_time": reading_time_str,
                "image": image_src_in_html,
                "timestamp": time.time(),
            }
            current_posts = [p for p in current_posts if p.get("url") != html_filename]
            current_posts.insert(0, new_entry)

            updated_posts_bytes = json.dumps(current_posts, indent=2, ensure_ascii=False).encode("utf-8")
            ftp.storbinary("STOR posts.json", io.BytesIO(updated_posts_bytes))
            logger.info("Índice posts.json actualizado en el servidor FTP (%d artículos).", len(current_posts))
        except Exception as json_exc:
            logger.warning("No se pudo actualizar posts.json vía FTP (api.php lo indexará automáticamente): %s", json_exc)

    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()

    public_url = f"https://goex-ia.com/blog/{html_filename}"
    logger.info("Artículo publicado con éxito en SiteGround -> %s", public_url)
    return {
        "filename": html_filename,
        "url": public_url,
        "image": image_src_in_html,
        "date": date_str,
    }


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Scraper de noticias de IA (TechCrunch), generador multi-LLM con plantilla HTML + Pixabay y subida FTP a SiteGround."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Genera el HTML del blog localmente pero no lo sube por FTP ni modifica seen_news.txt.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=int(os.getenv("MAX_POSTS_PER_RUN", "3")),
        help="Número máximo de artículos a generar en esta ejecución.",
    )
    args = parser.parse_args()

    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    pixabay_api_key = os.getenv("PIXABAY_API_KEY", DEFAULT_PIXABAY_KEY).strip()

    ftp_host = os.getenv("FTP_HOST", DEFAULT_FTP_HOST).strip()
    ftp_port = int(os.getenv("FTP_PORT", str(DEFAULT_FTP_PORT)).strip() or DEFAULT_FTP_PORT)
    ftp_user = os.getenv("FTP_USER", DEFAULT_FTP_USER).strip()
    ftp_password = os.getenv("FTP_PASSWORD", DEFAULT_FTP_PASSWORD).strip()
    ftp_blog_dir = os.getenv("FTP_BLOG_DIR", DEFAULT_FTP_BLOG_DIR).strip()

    seed_older_on_first_run = os.getenv("SEED_OLDER_ON_FIRST_RUN", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    if not any([gemini_api_key, groq_api_key, openrouter_api_key]):
        logger.error("Falta configurar al menos una API key de LLM (GEMINI_API_KEY, GROQ_API_KEY u OPENROUTER_API_KEY).")
        return 1

    if not args.dry_run and (not ftp_host or not ftp_user or not ftp_password):
        logger.error(
            "Faltan credenciales FTP (FTP_HOST, FTP_USER, FTP_PASSWORD). "
            "Configúralas en GitHub Secrets o en tu archivo .env (o usa --dry-run para probar)."
        )
        return 1

    seen_guids = load_seen_guids(SEEN_NEWS_FILE)
    is_first_run = len(seen_guids) == 0
    logger.info("GUIDs cargados en histórico (%s): %d", SEEN_NEWS_FILE.name, len(seen_guids))

    all_posts: List[Dict[str, Any]] = []
    for feed in DEFAULT_FEEDS:
        all_posts.extend(fetch_feed_posts(feed))

    if not all_posts:
        logger.warning("No se pudieron obtener noticias de ninguna fuente.")
        return 0

    unseen_posts: List[Dict[str, Any]] = []
    seen_in_batch: Set[str] = set()
    for post in all_posts:
        guid = post["guid"]
        if guid not in seen_guids and guid not in seen_in_batch:
            unseen_posts.append(post)
            seen_in_batch.add(guid)

    unseen_posts.sort(key=lambda x: x.get("date", ""), reverse=True)

    if not unseen_posts:
        logger.info("No hay noticias nuevas pendientes de procesar hoy.")
        try:
            with LAST_PUBLISHED_FILE.open("w", encoding="utf-8") as f:
                json.dump([], f)
        except Exception:
            pass
        return 0

    logger.info("Se encontraron %d noticias nuevas.", len(unseen_posts))

    posts_to_process = unseen_posts[: args.max_posts]
    older_posts_to_seed = unseen_posts[args.max_posts :] if (is_first_run and seed_older_on_first_run) else []

    published_count = 0
    published_blogs_record: List[Dict[str, Any]] = []
    for idx, news_item in enumerate(posts_to_process, start=1):
        logger.info(
            "[%d/%d] Procesando noticia: %s (%s)",
            idx,
            len(posts_to_process),
            news_item["title"],
            news_item["guid"],
        )
        try:
            blog_post = generate_blog_with_fallbacks(
                news_item=news_item,
                gemini_api_key=gemini_api_key,
                groq_api_key=groq_api_key,
                openrouter_api_key=openrouter_api_key,
            )

            if args.dry_run:
                date_str = get_current_date_short_es()
                rt_str = estimate_reading_time(blog_post["content_html"])
                rendered_preview = render_article_html(
                    blog_post=blog_post,
                    image_src="../images/servicio_ia_automatizacion.jpg",
                    date_str=date_str,
                    reading_time_str=rt_str,
                )
                logger.info(
                    "[DRY-RUN] Blog HTML generado exitosamente:\n  Título: %s\n  Slug: %s.html\n  Categoría: %s\n  Extracto: %s\n  Longitud HTML final: %d caracteres",
                    blog_post["title"],
                    blog_post["slug"],
                    blog_post.get("category"),
                    blog_post["excerpt"],
                    len(rendered_preview),
                )
            else:
                uploaded_info = upload_article_to_siteground_ftp(
                    blog_post=blog_post,
                    ftp_host=ftp_host,
                    ftp_port=ftp_port,
                    ftp_user=ftp_user,
                    ftp_password=ftp_password,
                    ftp_blog_dir=ftp_blog_dir,
                    pixabay_api_key=pixabay_api_key,
                )
                append_seen_guid(SEEN_NEWS_FILE, news_item["guid"])
                seen_guids.add(news_item["guid"])
                published_count += 1
                published_blogs_record.append(
                    {
                        "filename": uploaded_info.get("filename"),
                        "title": blog_post["title"],
                        "url": uploaded_info.get("url", ""),
                        "image": uploaded_info.get("image", ""),
                    }
                )

            if idx < len(posts_to_process):
                time.sleep(3)

        except Exception as exc:
            logger.error("Error procesando la noticia '%s': %s", news_item["title"], exc)

    try:
        with LAST_PUBLISHED_FILE.open("w", encoding="utf-8") as f:
            json.dump(published_blogs_record, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("No se pudo guardar %s: %s", LAST_PUBLISHED_FILE.name, exc)

    if not args.dry_run and older_posts_to_seed and published_count > 0:
        logger.info(
            "Primera ejecución detectada: registrando %d noticias anteriores en %s para inicializar el histórico.",
            len(older_posts_to_seed),
            SEEN_NEWS_FILE.name,
        )
        for older_post in older_posts_to_seed:
            append_seen_guid(SEEN_NEWS_FILE, older_post["guid"])

    logger.info("Proceso finalizado. Artículos publicados en esta ejecución: %d", published_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
