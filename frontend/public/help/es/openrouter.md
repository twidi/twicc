**OpenRouter** es un servicio en línea. Una cuenta y una clave de API dan acceso a muchos modelos de IA. TwiCC puede usarlo como motor opcional en unos pocos sitios. El resto de TwiCC nunca lo necesita.

## Dónde lo usa TwiCC

| Dónde | Qué hace OpenRouter |
|---|---|
| **Configuración → Texto a voz** | Lee los mensajes en voz alta con una **configuración de OpenRouter**. También adapta el texto antes de la lectura. |
| **Configuración → Resúmenes** | Escribe los resúmenes de mensajes y selecciones. |
| **Configuración → Títulos** | Sugiere el título de una sesión. El diálogo de renombrar también puede probarlo para un solo título. |

Para la adaptación, los resúmenes y los títulos, TwiCC usa un modelo de texto rápido: hoy, Qwen3.8 27B servido por Cerebras.

Para la voz, eliges el modelo de voz y la voz en **Configuración → Texto a voz**. TwiCC carga las listas de modelos y de voces desde OpenRouter cuando las necesitas. Consulta [Texto a voz](help/text-to-speech).

## Activarlo

1. Crea una cuenta en [openrouter.ai](https://openrouter.ai) y añade créditos.
2. Crea una clave de API en la [página de claves](https://openrouter.ai/settings/keys).
3. Añade la línea `TWICC_OPENROUTER_API_KEY=your-key` al archivo `.env` del directorio de datos de TwiCC (`~/.twicc/.env` por defecto).

4. Reinicia TwiCC. Reinícialo otra vez cada vez que cambies la clave.

La clave se queda en el servidor. TwiCC nunca la envía al navegador.

Sin la clave, las opciones de OpenRouter están desactivadas. TwiCC conserva tu elección y la aplica de nuevo cuando la clave está definida.

## Privacidad y coste

- TwiCC envía el texto a OpenRouter: el mensaje para la voz y los resúmenes, los mensajes del usuario para los títulos.
- Pagas en tu cuenta de OpenRouter. Los precios que muestra la configuración son los precios de lista de OpenRouter. El coste real puede diferir.
- OpenRouter tiene sus propios términos y su propia política de datos. Léelos antes de usarlo.

## Cuando OpenRouter falla

Para la adaptación, los resúmenes y los títulos, un agente activado toma el relevo: primero Claude, después Codex. Si ningún agente puede ejecutarse, la función muestra un error, o el texto se lee tal como está.
Una configuración de voz que usa OpenRouter no tiene alternativa. Pruébala en **Configuración → Texto a voz** para ver el error.
