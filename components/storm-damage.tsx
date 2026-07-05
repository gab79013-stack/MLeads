import { CloudLightning, Timer, Radar, BadgeCheck } from "lucide-react"

const POINTS = [
  {
    icon: CloudLightning,
    title: "Detección por evento climático",
    body: "Cruzamos reportes de granizo, viento y tormenta con zonas residenciales para ubicar techos y fachadas afectadas.",
  },
  {
    icon: Timer,
    title: "Velocidad de respuesta",
    body: "Recibes la señal en horas, no semanas. Llegas antes que la competencia mientras la intención sigue caliente.",
  },
  {
    icon: Radar,
    title: "Radio geográfico propio",
    body: "Define tu zona de servicio y solo verás leads dentro de tu rango operativo real.",
  },
  {
    icon: BadgeCheck,
    title: "Propietario confirmado",
    body: "Cada lead incluye datos de contacto verificados y el origen de la señal para que trabajes con confianza.",
  },
]

export function StormDamage() {
  return (
    <section id="tormenta" className="bg-background py-20 lg:py-28">
      <div className="mx-auto max-w-6xl px-5">
        <div className="max-w-2xl">
          <span className="inline-flex items-center gap-2 rounded-full bg-accent/10 px-3 py-1.5 text-xs font-semibold text-accent">
            <CloudLightning size={14} />
            Leads por daño de tormenta
          </span>
          <h2 className="mt-5 text-pretty text-3xl font-extrabold tracking-tight text-foreground sm:text-4xl">
            Llega primero cuando la tormenta crea la oportunidad.
          </h2>
          <p className="mt-4 text-pretty text-lg leading-relaxed text-muted">
            Convertimos eventos climáticos en trabajo real de roofing y restauración,
            con propietarios que ya necesitan un contratista.
          </p>
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {POINTS.map((p) => (
            <div
              key={p.title}
              className="rounded-2xl border border-line bg-card p-6 transition-shadow hover:shadow-lg"
            >
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent/10 text-accent">
                <p.icon size={22} />
              </span>
              <h3 className="mt-5 text-base font-bold text-card-foreground">{p.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{p.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
