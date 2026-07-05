import { ArrowRight } from "lucide-react"

export function Cta() {
  return (
    <section id="start" className="bg-background py-20 lg:py-28">
      <div className="mx-auto max-w-5xl px-5">
        <div className="relative overflow-hidden rounded-3xl bg-accent px-6 py-14 text-center sm:px-12 sm:py-20">
          <div
            className="pointer-events-none absolute inset-0 opacity-10"
            style={{
              backgroundImage:
                "radial-gradient(circle at 20% 20%, #fff 1px, transparent 1px)",
              backgroundSize: "28px 28px",
            }}
            aria-hidden="true"
          />
          <div className="relative">
            <h2 className="mx-auto max-w-2xl text-pretty text-3xl font-extrabold tracking-tight text-accent-foreground sm:text-4xl lg:text-5xl">
              Empieza a trabajar prospectos con intención real.
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-pretty text-lg leading-relaxed text-accent-foreground/85">
              Crea tu cuenta gratis, define tu zona y recibe tus primeras señales
              verificadas hoy mismo.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <a
                href="#"
                className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-ink px-7 py-3.5 text-base font-semibold text-ink-foreground transition-opacity hover:opacity-90 sm:w-auto"
              >
                Empezar gratis
                <ArrowRight size={18} />
              </a>
              <a
                href="#pipeline"
                className="inline-flex w-full items-center justify-center rounded-lg border border-white/40 px-7 py-3.5 text-base font-semibold text-accent-foreground transition-colors hover:bg-white/10 sm:w-auto"
              >
                Hablar con ventas
              </a>
            </div>
            <p className="mt-6 text-sm text-accent-foreground/75">
              Sin tarjeta de crédito · Cancela cuando quieras
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
