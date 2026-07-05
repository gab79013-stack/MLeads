import { StickyNote, FileText, Receipt, LayoutGrid } from "lucide-react"

const STAGES = [
  { name: "Nuevos", count: 8, tint: "border-t-accent" },
  { name: "Contactados", count: 5, tint: "border-t-brand-blue" },
  { name: "Estimate", count: 3, tint: "border-t-brand-amber" },
  { name: "Ganados", count: 2, tint: "border-t-brand-green" },
]

const TOOLS = [
  { icon: StickyNote, title: "Notas y seguimiento", body: "Registra cada interacción y nunca pierdas el hilo de un prospecto." },
  { icon: FileText, title: "Estimate draft", body: "Genera borradores de estimado directamente desde el lead." },
  { icon: Receipt, title: "Invoice draft", body: "Convierte el trabajo ganado en factura sin salir de 0brix." },
  { icon: LayoutGrid, title: "CRM por usuario", body: "Cada contratista tiene su propio pipeline privado y ordenado." },
]

export function PipelineCrm() {
  return (
    <section id="pipeline" className="bg-background py-20 lg:py-28">
      <div className="mx-auto max-w-6xl px-5">
        <div className="mx-auto max-w-2xl text-center">
          <span className="inline-flex items-center gap-2 rounded-full bg-brand-blue/10 px-3 py-1.5 text-xs font-semibold text-brand-blue">
            <LayoutGrid size={14} />
            Pipeline CRM
          </span>
          <h2 className="mt-5 text-pretty text-3xl font-extrabold tracking-tight text-foreground sm:text-4xl">
            Del lead al contrato, todo en un solo lugar.
          </h2>
          <p className="mt-4 text-pretty text-lg leading-relaxed text-muted">
            Cada señal que guardas entra a tu Pipeline 0brix con seguimiento, notas,
            estimate e invoice draft. Simple, privado y hecho para contratistas.
          </p>
        </div>

        <div className="mt-12 rounded-2xl border border-line bg-card p-4 shadow-sm sm:p-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {STAGES.map((s) => (
              <div key={s.name} className={`rounded-xl border border-line border-t-4 ${s.tint} bg-background p-4`}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-bold text-foreground">{s.name}</span>
                  <span className="rounded-full bg-card px-2 py-0.5 text-xs font-semibold text-muted">
                    {s.count}
                  </span>
                </div>
                <div className="mt-3 flex flex-col gap-2">
                  {Array.from({ length: Math.min(s.count, 3) }).map((_, i) => (
                    <div key={i} className="rounded-lg border border-line bg-card p-3">
                      <div className="h-2 w-2/3 rounded bg-line" />
                      <div className="mt-2 h-2 w-1/2 rounded bg-line/70" />
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {TOOLS.map((t) => (
            <div key={t.title} className="rounded-2xl border border-line bg-card p-6">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-blue/10 text-brand-blue">
                <t.icon size={22} />
              </span>
              <h3 className="mt-5 text-base font-bold text-card-foreground">{t.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{t.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
