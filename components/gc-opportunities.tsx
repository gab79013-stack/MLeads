import { Building2, Check } from "lucide-react"

const ROWS = [
  { project: "Remodelación comercial", city: "Austin, TX", value: "$180k", tag: "Permiso emitido" },
  { project: "Ampliación residencial", city: "Denver, CO", value: "$95k", tag: "Intención alta" },
  { project: "Restauración multi-unidad", city: "Miami, FL", value: "$240k", tag: "GC buscando subs" },
]

const BENEFITS = [
  "Oportunidades filtradas por especialidad y capacidad",
  "Estimados de valor para priorizar tu esfuerzo",
  "Contexto del permiso y etapa del proyecto",
  "Match directo con GCs que buscan subcontratistas",
]

export function GcOpportunities() {
  return (
    <section id="gc" className="bg-ink py-20 text-ink-foreground lg:py-28">
      <div className="mx-auto grid max-w-6xl gap-12 px-5 lg:grid-cols-2 lg:items-center">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-blue-300">
            <Building2 size={14} />
            Oportunidades para GC
          </span>
          <h2 className="mt-5 text-pretty text-3xl font-extrabold tracking-tight sm:text-4xl">
            Proyectos grandes, filtrados para tu especialidad.
          </h2>
          <p className="mt-4 text-pretty text-lg leading-relaxed text-ink-foreground/70">
            Deja de perseguir todo. 0brix te muestra oportunidades de general
            contractor que encajan con tu operación, con datos suficientes para
            decidir en segundos.
          </p>

          <ul className="mt-8 flex flex-col gap-3">
            {BENEFITS.map((b) => (
              <li key={b} className="flex items-start gap-3">
                <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-brand-blue/20 text-blue-300">
                  <Check size={13} strokeWidth={3} />
                </span>
                <span className="text-sm leading-relaxed text-ink-foreground/80">{b}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
          <div className="flex items-center justify-between px-1 pb-3 text-xs font-semibold uppercase tracking-wide text-ink-foreground/50">
            <span>Proyecto</span>
            <span>Valor estimado</span>
          </div>
          <div className="flex flex-col gap-3">
            {ROWS.map((r) => (
              <div
                key={r.project}
                className="flex items-center justify-between gap-4 rounded-xl border border-white/10 bg-ink/50 p-4"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-ink-foreground">{r.project}</p>
                  <p className="mt-1 text-xs text-ink-foreground/55">
                    {r.city} · {r.tag}
                  </p>
                </div>
                <span className="shrink-0 rounded-lg bg-brand-blue/15 px-3 py-1.5 text-sm font-bold text-blue-300">
                  {r.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
