import { Logo } from "./logo"

const COLUMNS = [
  {
    title: "Producto",
    links: ["Leads de tormenta", "Oportunidades GC", "Pipeline CRM", "Fuentes verificadas"],
  },
  {
    title: "Empresa",
    links: ["Sobre 0brix", "Clientes", "Precios", "Contacto"],
  },
  {
    title: "Recursos",
    links: ["Centro de ayuda", "Guías", "Estado del sistema", "API"],
  },
]

export function SiteFooter() {
  return (
    <footer className="bg-ink text-ink-foreground">
      <div className="mx-auto max-w-6xl px-5 py-14">
        <div className="grid gap-10 lg:grid-cols-[1.4fr_1fr_1fr_1fr]">
          <div>
            <Logo inverted />
            <p className="mt-4 max-w-xs text-sm leading-relaxed text-ink-foreground/60">
              Infraestructura comercial para contratistas. Señales de construcción
              convertidas en prospectos verificados con intención comercial.
            </p>
          </div>

          {COLUMNS.map((col) => (
            <div key={col.title}>
              <h3 className="text-sm font-bold text-ink-foreground">{col.title}</h3>
              <ul className="mt-4 flex flex-col gap-2.5">
                {col.links.map((link) => (
                  <li key={link}>
                    <a
                      href="#"
                      className="text-sm text-ink-foreground/60 transition-colors hover:text-ink-foreground"
                    >
                      {link}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-12 flex flex-col items-start justify-between gap-4 border-t border-white/10 pt-8 sm:flex-row sm:items-center">
          <p className="text-sm text-ink-foreground/50">
            © {new Date().getFullYear()} 0brix. Todos los derechos reservados.
          </p>
          <div className="flex gap-6">
            <a href="#" className="text-sm text-ink-foreground/50 hover:text-ink-foreground">
              Privacidad
            </a>
            <a href="#" className="text-sm text-ink-foreground/50 hover:text-ink-foreground">
              Términos
            </a>
          </div>
        </div>
      </div>
    </footer>
  )
}
