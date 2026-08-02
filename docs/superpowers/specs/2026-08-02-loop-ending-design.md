# Final-loop para Shorts (loop ending)

**Fecha:** 2026-08-02 · **Estado:** aprobado por Juan

## Objetivo

Los Shorts se reproducen en bucle automáticamente. Un vídeo cuyo final
desemboca en su principio se re-ve sin que el espectador lo note, lo que
sube retención y re-visualizaciones. Hoy todos los vídeos cierran con un
CTA de suscripción ("suscríbete, dale a la campanita…"), que es una señal
auditiva inequívoca de final.

## Decisiones

- **A/B por ratio**: `loop_ending_ratio` en config (0.0–1.0, default 0.5).
  Cada vídeo decide al azar si lleva final-loop o el CTA clásico.
- En vídeos loop **el CTA desaparece por completo**: sin "suscríbete", sin
  "campanita", sin despedidas — ni siquiera como fallback. Un final seco
  delata menos que cualquier fórmula de cierre.
- Cada vídeo persiste `"loop_ending": true/false` en `.mp/youtube.json`
  para que el futuro bucle de analíticas compare los dos grupos.

## Diseño

1. **Config**: `loop_ending_ratio` en `config.json` y `config.example.json`;
   getter `get_loop_ending_ratio()` en `src/config.py` (default 0.5).
2. **Selección**: en `generate_video()`, tras generar guion y prompts:
   `self.loop_ending = random.random() < get_loop_ending_ratio()`.
3. **Frase puente** (`_generate_loop_bridge()`): si el vídeo es loop, el
   LLM recibe el guion y su primera frase y devuelve UNA frase de cierre
   que desemboca de forma natural en esa primera frase. Prohibido:
   despedidas, agradecimientos, suscripción, campanita, fórmulas de
   cierre. La frase se anexa por el mecanismo existente de `self.cta`
   (compatible con monólogo y diálogo a dos voces; el QC no cambia).
   Fallback si el LLM falla o devuelve vacío: **nada** (cadena vacía).
   Nota: el fallback actual `cta or get_subscribe_cta()` en
   `generate_script_to_speech` debe saltarse cuando `loop_ending` para
   que un puente vacío no resucite el CTA.
4. **Cierre visual**: en `combine()`, si `loop_ending` y hay ≥2 escenas,
   la última escena usa la imagen de la primera
   (`image_cycle[-1] = self.images[0]`). Los clips LTXV van indexados por
   ruta de imagen, así que la última escena reutiliza la misma animación:
   frame final ≈ frame inicial.
5. **Audio**: fadeout de música 2,5 s → 0,5 s en vídeos loop.
6. **Persistencia**: `add_video()` recibe además
   `"loop_ending": bool(self.loop_ending)`.

## Validación

- Test unitario del prompt puente con 2 guiones de ejemplo (patrón del
  test de moods).
- E2E real con `loop_ending_ratio: 1.0` temporal; revisar el vídeo y
  restaurar 0.5.

## Fuera de alcance

- Reescribir el prompt maestro del guion (enfoque B, descartado).
- Medir retención (llegará con el bucle de analíticas post-vacaciones).
