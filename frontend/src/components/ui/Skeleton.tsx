// Content-shaped loading placeholders. Respects prefers-reduced-motion (the
// pulse is a Tailwind utility that we disable via the `motion-reduce` variant).
export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`animate-pulse motion-reduce:animate-none rounded-sm bg-slate-200 ${className}`} />;
}

// A few lines of skeleton text (e.g. inside a loading card).
export function SkeletonText({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={`h-3 ${i === lines - 1 ? "w-2/3" : "w-full"}`} />
      ))}
    </div>
  );
}
