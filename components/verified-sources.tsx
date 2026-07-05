import { ShieldCheck, Database, FileSearch, Lock } from "lucide-react"

const SOURCES = [
  "NOAA / Servicio Meteorológico",
  "Permisos de construcción municipales",
  "Reportes 311",
  "Registros de propiedad",
  "Datos de inspección",
  "Señales de intención comercial",
]

const PILLARS = [
  {
    icon: Database,
    title: "Origen trazable",
    body: "Cada lead muestra de dónde viene la señal. Sin cajas negras ni listas recicladas.",
  },
  {
    icon: FileSearch,
    title: "Verificación de datos",
    body: "Validamos contacto y propiedad antes de que el lead llegue a tu pipeline.",
  },
  {
    icon: Lock,
    title: "Privacidad por usuario",
    body: "Tu actividad y tu pipeline son solo tuyos. Ningún competidor ve tus leads.",
  },
]

export function VerifiedSources() {
  return (
    <section id="fuentes" className="bg-ink py-20 text-ink-foreground lg:py-28">
      <div className="mx-auto max-w-6xl px-5">
        <div className="mx-auto max-w-2xl text-center">
          <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-emerald-300">
            <ShieldCheck size={14} />
            Fuentes verificadas
          </span>
          <h2 className="mt-5 text-pretty text-3xl font-extrabold tracking-tight sm:text-4xl">
            Datos en los que puedes confiar tu tiempo y tu dinero.
          </h2>
          <p className="mt-4 text-pretty text-lg leading-relaxed text-ink-foreground/70">
            0brix se construye sobre fuentes públicas y verificables. Sabes exactamente
            por qué un lead está frente a ti.
          </p>
        </div>

        <div className="mx-auto mt-10 flex max-w-3xl flex-wrap justify-center gap-2.5">
          {SOURCES.map((s) => (
            <span
              key={s}
              className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-ink-foreground/80"
            >
              {s}
            </span>
          ))}
        </div>

        <div className="mt-14 grid gap-4 md:grid-cols-3">
          {PILLARS.map((p) => (
            <div key={p.title} className="rounded-2xl border border-white/10 bg-white/[0.04] p-6">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-green/15 text-emerald-300">
                <p.icon size={22} />
              </span>
              <h3 className="mt-5 text-base font-bold text-ink-foreground">{p.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-foreground/65">{p.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
