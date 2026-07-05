import type { Metadata, Viewport } from "next"
import { Geist, Geist_Mono } from "next/font/google"
import "./globals.css"

const geistSans = Geist({ subsets: ["latin"], variable: "--font-geist-sans" })
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" })

export const metadata: Metadata = {
  title: "0brix — Infraestructura comercial para contratistas",
  description:
    "0brix convierte señales de construcción en prospectos verificados: leads por daño de tormenta, oportunidades para GCs, pipeline CRM y fuentes verificadas.",
  keywords: [
    "leads contratistas",
    "daño por tormenta",
    "general contractor",
    "CRM construcción",
    "prospectos verificados",
  ],
  openGraph: {
    title: "0brix — Infraestructura comercial para contratistas",
    description:
      "Convierte señales de construcción en prospectos verificados con intención comercial real.",
    type: "website",
  },
}

export const viewport: Viewport = {
  themeColor: "#0b1220",
  width: "device-width",
  initialScale: 1,
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="es" className={`${geistSans.variable} ${geistMono.variable} bg-background`}>
      <body className="font-sans antialiased">{children}</body>
    </html>
  )
}
