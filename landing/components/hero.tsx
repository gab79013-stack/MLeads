import { ArrowRight, ShieldCheck, Zap, MapPin } from "lucide-react"

export function Hero() {
  return (
    <section id="top" className="relative overflow-hidden bg-ink text-ink-foreground">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.06]"
        style={{
          backgroundImage:
            "linear-gradient(to right, #fff 1px, transparent 1px), linear-gradient(to bottom, #fff 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}
        aria-hidden="true"
      />
      <div className="relative mx-auto grid max-w-6xl gap-12 px-5 py-20 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:py-28">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-ink-foreground/90">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            Infraestructura comercial para contratistas
          </span>

          <h1 className="mt-6 text-pretty text-4xl font-extrabold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">
            Convierte señales de construcción en{" "}
            <span className="text-accent">prospectos verificados</span>.
          </h1>

          <p className="mt-6 max-w-xl text-pretty text-lg leading-relaxed text-ink-foreground/70">
            0brix no vende listas frías. Ordena señales de intención real —daño por
            tormenta, permisos, oportunidades de GC— y las convierte en acciones
            comerciales simples dentro de tu pipeline.
          </p>

          <div className="mt-9 flex flex-col gap-3 sm:flex-row">
            <a
              href="#start"
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-6 py-3.5 text-base font-semibold text-accent-foreground transition-opacity hover:opacity-90"
            >
              Empezar gratis
              <ArrowRight size={18} />
            </a>
            <a
              href="#pipeline"
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/5 px-6 py-3.5 text-base font-semibold text-ink-foreground transition-colors hover:bg-white/10"
            >
              Ver el pipeline
            </a>
          </div>

          <dl className="mt-12 grid grid-cols-3 gap-6 border-t border-white/10 pt-8">
            {[
              { k: "48h", v: "de la señal al contacto" },
              { k: "100%", v: "fuentes verificables" },
              { k: "1 clic", v: "del lead al pipeline" },
            ].map((s) => (
              <div key={s.k}>
                <dt className="text-2xl font-extrabold text-ink-foreground sm:text-3xl">{s.k}</dt>
                <dd className="mt-1 text-sm text-ink-foreground/60">{s.v}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="relative">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 shadow-2xl backdrop-blur-sm">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-ink-foreground/80">Señales en vivo</span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-green/15 px-2.5 py-1 text-xs font-semibold text-emerald-300">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
                En línea
              </span>
            </div>

            <div className="mt-4 flex flex-col gap-3">
              {[
                {
                  icon: Zap,
                  tint: "text-accent",
                  bg: "bg-accent/15",
                  title: "Daño por granizo — Denver, CO",
                  meta: "Roofing · intención alta · verificado NOAA",
                },
                {
                  icon: MapPin,
                  tint: "text-blue-300",
                  bg: "bg-brand-blue/15",
                  title: "Permiso de remodelación — Austin, TX",
                  meta: "GC · $180k estimado · permiso emitido",
                },
                {
                  icon: ShieldCheck,
                  tint: "text-emerald-300",
                  bg: "bg-brand-green/15",
                  title: "Reporte 311 — Miami, FL",
                  meta: "Restauración · propietario confirmado",
                },
              ].map((row) => (
                <div
                  key={row.title}
                  className="flex items-start gap-3 rounded-xl border border-white/10 bg-ink/40 p-3.5"
                >
                  <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${row.bg} ${row.tint}`}>
                    <row.icon size={18} />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-ink-foreground">{row.title}</p>
                    <p className="mt-0.5 text-xs text-ink-foreground/60">{row.meta}</p>
                  </div>
                  <button className="ml-auto shrink-0 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-foreground">
                    Guardar
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
