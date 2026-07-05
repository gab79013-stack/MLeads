"use client"

import { useState } from "react"
import { Menu, X } from "lucide-react"
import { Logo } from "./logo"

const NAV = [
  { label: "Leads de tormenta", href: "#tormenta" },
  { label: "Oportunidades GC", href: "#gc" },
  { label: "Pipeline CRM", href: "#pipeline" },
  { label: "Fuentes", href: "#fuentes" },
]

export function SiteHeader() {
  const [open, setOpen] = useState(false)

  return (
    <header className="sticky top-0 z-50 border-b border-line/80 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3.5">
        <a href="#top" aria-label="0brix inicio">
          <Logo />
        </a>

        <nav className="hidden items-center gap-7 md:flex" aria-label="Principal">
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="text-sm font-medium text-muted transition-colors hover:text-foreground"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-2 md:flex">
          <a
            href="#login"
            className="rounded-lg px-3.5 py-2 text-sm font-semibold text-foreground transition-colors hover:bg-card"
          >
            Iniciar sesión
          </a>
          <a
            href="#start"
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground transition-opacity hover:opacity-90"
          >
            Empezar gratis
          </a>
        </div>

        <button
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-card text-foreground md:hidden"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? "Cerrar menú" : "Abrir menú"}
          aria-expanded={open}
        >
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>

      {open && (
        <div className="border-t border-line bg-background px-5 py-4 md:hidden">
          <nav className="flex flex-col gap-1" aria-label="Móvil">
            {NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                className="rounded-lg px-3 py-2.5 text-sm font-medium text-foreground hover:bg-card"
              >
                {item.label}
              </a>
            ))}
            <a
              href="#start"
              onClick={() => setOpen(false)}
              className="mt-2 rounded-lg bg-accent px-4 py-3 text-center text-sm font-semibold text-accent-foreground"
            >
              Empezar gratis
            </a>
          </nav>
        </div>
      )}
    </header>
  )
}
