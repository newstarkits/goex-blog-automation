# Automatización Diaria de Blogs de Inteligencia Artificial para GOEX (TechCrunch + Multi-LLM + Pixabay + SiteGround FTP)

Este repositorio ejecuta diariamente un flujo automatizado mediante **GitHub Actions** que:

1. **Extrae noticias recientes de Inteligencia Artificial** desde el endpoint JSON de WordPress de TechCrunch:
   - `https://techcrunch.com/wp-json/wp/v2/posts?categories=577047203&per_page=50`
2. **Filtra noticias nuevas** comparando la clave primaria `guid.rendered` de cada noticia contra el archivo histórico [`seen_news.txt`](./seen_news.txt) versionado en el propio repositorio.
3. **Traduce y redacta un artículo de blog 100% original en español y optimizado para SEO** usando en cascada las APIs de **Google Gemini (`gemini-3.8-flash`, `gemma-4-31b-it`, etc.) -> Groq -> OpenRouter**.
4. **Obtiene una imagen aleatoria de alta calidad de Pixabay** (`q=artificial-intelligenc`) y la integra junto con el artículo generado dentro de la plantilla HTML [`templates/plantilla-nuevo-articulo.html`](./templates/plantilla-nuevo-articulo.html).
5. **Sube automáticamente el archivo `.html` del artículo (y la imagen + `posts.json` actualizado)** mediante **FTP** a SiteGround en la ruta `goex-ia.com > public_html > blog` (`/goex-ia.com/public_html/blog`).
6. **Actualiza `seen_news.txt`** mediante un commit automático al finalizar la ejecución para no repetir noticias en ejecuciones futuras.

---

## Secretos de GitHub Actions

En tu repositorio de GitHub, ve a **Settings → Secrets and variables → Actions → New repository secret** y configura las claves de LLM (los datos de FTP y Pixabay tienen valores de respaldo configurados según tus especificaciones, aunque también puedes sobrescribirlos con secretos):

| Nombre del Secreto | Descripción |
| :--- | :--- |
| `GEMINI_API_KEY` | Tu clave de API de Google AI Studio / Gemini API. |
| `GROQ_API_KEY` | (Opcional) Clave de respaldo de Groq API. |
| `OPENROUTER_API_KEY` | (Opcional) Clave de respaldo de OpenRouter API. |
| `PIXABAY_API_KEY` | (Opcional) Clave de API de Pixabay (`46784606-438ba39acca420863fed8c139` por defecto). |
| `FTP_HOST` | (Opcional) Host FTP de SiteGround (`ftp.goex-ia.com` por defecto). |
| `FTP_PORT` | (Opcional) Puerto FTP (`21` por defecto). |
| `FTP_USER` | (Opcional) Usuario FTP (`bauto_marta@goex-ia.com` por defecto). |
| `FTP_PASSWORD` | (Opcional) Contraseña FTP de SiteGround. |
| `FTP_BLOG_DIR` | (Opcional) Ruta remota del blog (`/goex-ia.com/public_html/blog` por defecto). |

### Variables opcionales (`Settings → Secrets and variables → Actions → Variables`)

- `MAX_POSTS_PER_RUN`: Máximo de artículos a publicar por día (por defecto `3`).

---

## Ejecución local

```bash
pip install -r requirements.txt
cp .env.example .env
# Edita .env con tus credenciales de LLM

# Probar generación sin subir por FTP ni modificar seen_news.txt:
python src/main.py --dry-run --max-posts 1

# Ejecutar flujo completo:
python src/main.py
```
