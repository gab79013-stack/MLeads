export function Logo({ inverted = false }: { inverted?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span
        className={`grid h-9 w-9 place-items-center rounded-lg text-lg font-black ${
          inverted ? "bg-card text-ink" : "bg-ink text-ink-foreground"
        }`}
        aria-hidden="true"
      >
        0
      </span>
      <span
        className={`text-xl font-extrabold tracking-tight ${
          inverted ? "text-ink-foreground" : "text-foreground"
        }`}
      >
        0brix
      </span>
    </div>
  )
}
